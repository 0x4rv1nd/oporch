"""oporch built-in codebase indexer — Python-native AST knowledge graph.

Builds a local knowledge graph of the project's code and stores it in
``oporch.db`` (the existing SQLite database).  No external tools or
language runtimes required — Python files are parsed with the stdlib
``ast`` module; other languages use lightweight regex extraction.

Auto-indexing
-------------
On REPL startup the indexer runs in a background thread (incremental: only
files whose mtime has changed since the last index pass are re-parsed).
A small "⚙ indexing…" status line is shown until it completes.

Manual commands
---------------
``/index``          — force full re-index
``/search <pat>``   — regex search for symbols (functions/classes)
``/callers <name>`` — show every call site that calls *name*
``/arch``           — architecture summary (entry points, top modules, hotspots)

APIs used by context_builder.py
---------------------------------
``search_symbols(db, project, pattern)``
``get_callers(db, project, name)``
``get_architecture(db, project)``
``get_snippet(symbol)``

Supported languages
-------------------
Python  → full AST (functions, classes, methods, imports, call sites)
JS/TS   → regex (function declarations, arrow functions, classes)
Go      → regex (func declarations)
Rust    → regex (fn declarations, impl blocks)
Java/Kotlin → regex (method declarations, class declarations)
"""

from __future__ import annotations

import ast
import os
import re
import threading
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .db import OporchDB

logger = logging.getLogger(__name__)

# ── Skip patterns ──────────────────────────────────────────────────────────────
_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", ".env",
    "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", "dist", "build", ".opencode-orchestrator",
    ".serena", "site-packages", ".tox", ".eggs",
}

_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
}

MAX_FILE_SIZE = 500_000  # bytes — skip very large generated files


# ── Data classes ───────────────────────────────────────────────────────────────
@dataclass
class Symbol:
    name: str
    kind: str          # function | class | method | variable
    filepath: str
    line_start: int
    line_end: int
    parent: str | None = None
    docstring: str | None = None
    language: str = "python"


@dataclass
class CallSite:
    caller_file: str
    caller_name: str
    callee_name: str
    line: int


@dataclass
class ImportRecord:
    filepath: str
    module: str
    names: str  # comma-separated imported names or "*"


@dataclass
class ArchSummary:
    project: str
    total_files: int
    total_symbols: int
    top_modules: list[str]           # most-symbol-dense files
    entry_points: list[str]          # functions named main / __main__ / app
    hotspots: list[tuple[str, int]]  # (function_name, caller_count)
    languages: dict[str, int]        # language → file count
    classes: list[str]               # top-level class names


# ── Python AST analysis ────────────────────────────────────────────────────────
class PythonAnalyzer:
    """Full AST analysis of a Python source file."""

    def __init__(self, filepath: str, source: str) -> None:
        self.filepath = filepath
        self.source = source
        self.symbols: list[Symbol] = []
        self.calls: list[CallSite] = []
        self.imports: list[ImportRecord] = []
        self._current_class: str | None = None
        self._current_func: str | None = None

    def analyze(self) -> None:
        try:
            tree = ast.parse(self.source, filename=self.filepath)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self._handle_class(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._handle_func(node, parent=None)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports.append(ImportRecord(
                        filepath=self.filepath,
                        module=alias.name,
                        names=alias.asname or alias.name,
                    ))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names = ",".join(
                        a.name for a in node.names
                    ) if node.names else "*"
                    self.imports.append(ImportRecord(
                        filepath=self.filepath,
                        module=node.module,
                        names=names,
                    ))

    def _handle_class(self, node: ast.ClassDef) -> None:
        doc = ast.get_docstring(node)
        sym = Symbol(
            name=node.name,
            kind="class",
            filepath=self.filepath,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=doc[:200] if doc else None,
            language="python",
        )
        self.symbols.append(sym)
        prev_class = self._current_class
        self._current_class = node.name
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child is not node:
                    self._handle_func(child, parent=node.name)
        self._current_class = prev_class

    def _handle_func(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent: str | None,
    ) -> None:
        doc = ast.get_docstring(node)
        kind = "method" if parent else "function"
        sym = Symbol(
            name=node.name,
            kind=kind,
            filepath=self.filepath,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            parent=parent,
            docstring=doc[:200] if doc else None,
            language="python",
        )
        self.symbols.append(sym)

        func_fqn = f"{parent}.{node.name}" if parent else node.name
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee = self._callee_name(child)
                if callee:
                    self.calls.append(CallSite(
                        caller_file=self.filepath,
                        caller_name=func_fqn,
                        callee_name=callee,
                        line=child.lineno if hasattr(child, "lineno") else 0,
                    ))

    @staticmethod
    def _callee_name(node: ast.Call) -> str | None:
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                return f"{func.value.id}.{func.attr}"
            return func.attr
        return None


# ── Regex-based generic analyzer ───────────────────────────────────────────────
_GENERIC_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "javascript": [
        (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(", "function"),
        (r"^(?:export\s+)?class\s+(\w+)", "class"),
    ],
    "typescript": [
        (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(", "function"),
        (r"^(?:export\s+)?class\s+(\w+)", "class"),
        (r"^\s+(?:async\s+)?(\w+)\s*\(.*\)\s*(?::\s*\w+)?\s*\{", "method"),
    ],
    "go": [
        (r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", "function"),
        (r"^type\s+(\w+)\s+struct", "class"),
    ],
    "rust": [
        (r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", "function"),
        (r"^(?:pub\s+)?struct\s+(\w+)", "class"),
        (r"^(?:pub\s+)?impl\s+(\w+)", "class"),
    ],
    "java": [
        (r"^\s+(?:public|private|protected)?\s+(?:static\s+)?(?:\w+\s+)+(\w+)\s*\(", "method"),
        (r"^(?:public\s+)?(?:abstract\s+)?class\s+(\w+)", "class"),
        (r"^(?:public\s+)?interface\s+(\w+)", "class"),
    ],
    "kotlin": [
        (r"^(?:fun\s+)(\w+)\s*\(", "function"),
        (r"^(?:class|object|interface)\s+(\w+)", "class"),
    ],
}


class GenericAnalyzer:
    """Regex-based analyzer for non-Python source files."""

    def __init__(self, filepath: str, source: str, language: str) -> None:
        self.filepath = filepath
        self.source = source
        self.language = language
        self.symbols: list[Symbol] = []

    def analyze(self) -> None:
        patterns = _GENERIC_PATTERNS.get(self.language, [])
        if not patterns:
            return
        compiled = [(re.compile(p, re.MULTILINE), kind) for p, kind in patterns]
        lines = self.source.splitlines()
        seen: set[str] = set()
        for lineno, line in enumerate(lines, 1):
            for pat, kind in compiled:
                m = pat.match(line)
                if m:
                    name = m.group(1)
                    key = f"{name}:{lineno}"
                    if key not in seen:
                        seen.add(key)
                        self.symbols.append(Symbol(
                            name=name,
                            kind=kind,
                            filepath=self.filepath,
                            line_start=lineno,
                            line_end=lineno,
                            language=self.language,
                        ))


# ── File walker ────────────────────────────────────────────────────────────────
def iter_source_files(root: Path) -> list[tuple[Path, str]]:
    """Yield (path, language) for all indexable source files under *root*."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place so os.walk won't descend into them
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            lang = _LANGUAGE_MAP.get(ext)
            if lang:
                full = Path(dirpath) / fname
                result.append((full, lang))
    return result


# ── Main indexer class ─────────────────────────────────────────────────────────
class CodebaseIndexer:
    """Incremental code indexer backed by oporch.db."""

    def __init__(self, db: "OporchDB", project: str | None = None) -> None:
        self._db = db
        self._project = project or str(Path.cwd())
        self._lock = threading.Lock()

    @property
    def project(self) -> str:
        return self._project

    # ── Public API ─────────────────────────────────────────────────────────────
    def index_project(
        self,
        root: Path | None = None,
        full: bool = False,
        on_progress: Any = None,
    ) -> dict[str, int]:
        """Index the project.

        Args:
            root: directory to index (default: project root / cwd)
            full: if True, clear existing index first and re-index everything
            on_progress: optional callable(files_done, files_total)

        Returns:
            dict with ``files``, ``symbols``, ``calls``, ``imports`` counts.
        """
        root = root or Path(self._project)
        if not root.exists():
            return {"files": 0, "symbols": 0, "calls": 0, "imports": 0}

        with self._lock:
            if full:
                self._db.clear_index(self._project)

            source_files = iter_source_files(root)
            total = len(source_files)
            counts = {"files": 0, "symbols": 0, "calls": 0, "imports": 0}

            for i, (path, lang) in enumerate(source_files, 1):
                if on_progress:
                    on_progress(i, total)
                try:
                    if not full and not self._file_changed(path):
                        continue
                    syms, calls, imps = self._analyze_file(path, lang)
                    # Clear old entries for this file then insert fresh
                    self._db.clear_file_index(self._project, str(path))
                    self._db.save_symbols(self._project, syms)
                    self._db.save_calls(self._project, calls)
                    self._db.save_imports(self._project, imps)
                    self._db.record_file_mtime(self._project, str(path), path.stat().st_mtime)
                    counts["files"] += 1
                    counts["symbols"] += len(syms)
                    counts["calls"] += len(calls)
                    counts["imports"] += len(imps)
                except Exception as exc:
                    logger.debug("Index skip %s: %s", path, exc)

            return counts

    def index_project_async(
        self,
        root: Path | None = None,
        callback: Any = None,
    ) -> threading.Thread:
        """Start background indexing; call *callback(counts)* when done."""
        def _run() -> None:
            try:
                counts = self.index_project(root=root, full=False)
                if callback:
                    callback(counts)
            except Exception as exc:
                logger.debug("Background index error: %s", exc)

        t = threading.Thread(target=_run, daemon=True, name="oporch-indexer")
        t.start()
        return t

    def search_symbols(
        self,
        pattern: str,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self._db.search_symbols(self._project, pattern, kind=kind, limit=limit)

    def get_callers(self, name: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._db.get_callers(self._project, name, limit=limit)

    def get_architecture(self) -> ArchSummary:
        return _build_arch_summary(self._db, self._project)

    # ── Internal helpers ────────────────────────────────────────────────────────
    def _file_changed(self, path: Path) -> bool:
        """True if file is new or mtime differs from stored value."""
        stored = self._db.get_file_mtime(self._project, str(path))
        if stored is None:
            return True
        try:
            return abs(path.stat().st_mtime - stored) > 0.01
        except OSError:
            return True

    def _analyze_file(
        self,
        path: Path,
        language: str,
    ) -> tuple[list[Symbol], list[CallSite], list[ImportRecord]]:
        try:
            size = path.stat().st_size
        except OSError:
            return [], [], []
        if size > MAX_FILE_SIZE:
            return [], [], []

        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], [], []

        filepath = str(path)
        if language == "python":
            analyzer = PythonAnalyzer(filepath, source)
            analyzer.analyze()
            return analyzer.symbols, analyzer.calls, analyzer.imports
        else:
            analyzer = GenericAnalyzer(filepath, source, language)
            analyzer.analyze()
            return analyzer.symbols, [], []


# ── Architecture summary ────────────────────────────────────────────────────────
def _build_arch_summary(db: "OporchDB", project: str) -> ArchSummary:
    # Query all symbols directly (search_symbols LIKE would filter by pattern)
    symbols = db._query(
        "SELECT * FROM code_symbols WHERE project = ? ORDER BY filepath, line_start LIMIT 10000",
        (project,),
    )
    symbols = [dict(r) for r in symbols]
    calls = db.all_calls(project)

    total_symbols = len(symbols)
    languages: dict[str, int] = {}
    file_symbol_count: dict[str, int] = {}
    classes: list[str] = []
    entry_points: list[str] = []

    for s in symbols:
        lang = s.get("language", "unknown")
        languages[lang] = languages.get(lang, 0) + 1
        fp = s.get("filepath", "")
        file_symbol_count[fp] = file_symbol_count.get(fp, 0) + 1
        if s.get("kind") == "class":
            classes.append(s["name"])
        if s.get("name") in ("main", "__main__", "app", "create_app", "run"):
            entry_points.append(f"{s.get('filepath','')}:{s['name']}")

    top_modules = sorted(file_symbol_count, key=lambda k: -file_symbol_count[k])[:10]

    callee_counts: dict[str, int] = {}
    for c in calls:
        callee = c.get("callee_name", "")
        if callee:
            callee_counts[callee] = callee_counts.get(callee, 0) + 1
    hotspots = sorted(callee_counts.items(), key=lambda x: -x[1])[:10]

    return ArchSummary(
        project=project,
        total_files=len(file_symbol_count),
        total_symbols=total_symbols,
        top_modules=[Path(m).name for m in top_modules],
        entry_points=entry_points[:10],
        hotspots=hotspots,
        languages=languages,
        classes=classes[:30],
    )


# ── Context builder integration ─────────────────────────────────────────────────
def enrich_context_with_index(
    db: "OporchDB",
    project: str,
    files_likely_affected: list[str],
    acceptance_criteria: list[str],
) -> str | None:
    """Build a compact index summary string to inject into agent context.

    Returns None if the index has no data yet.
    """
    try:
        symbols: list[dict[str, Any]] = []
        callers_seen: set[str] = set()
        call_lines: list[str] = []

        for fp in files_likely_affected[:5]:
            filename = Path(fp).name
            # Symbols defined in this file
            rows = db.search_symbols(project, ".*", filepath=fp, limit=10)
            for r in rows:
                sym_desc = f"  {r['kind']} {r['name']} (line {r.get('line_start','')})"
                if r.get("docstring"):
                    sym_desc += f" — {r['docstring'][:60]}"
                symbols.append(sym_desc)
            # Callers of functions in this file
            for r in rows:
                if r["name"] not in callers_seen and r["kind"] in ("function", "method"):
                    callers = db.get_callers(project, r["name"], limit=5)
                    if callers:
                        callers_seen.add(r["name"])
                        callers_str = ", ".join(
                            f"{c['caller_name']} ({Path(c['caller_file']).name})"
                            for c in callers[:3]
                        )
                        call_lines.append(f"  {r['name']} ← called by: {callers_str}")

        if not symbols and not call_lines:
            return None

        parts = ["## Code Index (auto-generated)"]
        if symbols:
            parts.append("Symbols in affected files:")
            parts.extend(symbols[:15])
        if call_lines:
            parts.append("Call graph context:")
            parts.extend(call_lines[:8])
        return "\n".join(parts)
    except Exception:
        return None


# ── Global indexer instance ─────────────────────────────────────────────────────
_global_indexer: CodebaseIndexer | None = None


def get_indexer(db: "OporchDB | None" = None) -> CodebaseIndexer | None:
    """Return the process-wide CodebaseIndexer (creates on first call if db is given)."""
    global _global_indexer
    if _global_indexer is None and db is not None:
        _global_indexer = CodebaseIndexer(db)
    return _global_indexer
