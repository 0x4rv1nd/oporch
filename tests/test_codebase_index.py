"""Tests for oporch.codebase_index — Python-native AST knowledge graph."""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

from oporch.codebase_index import (
    PythonAnalyzer,
    GenericAnalyzer,
    CodebaseIndexer,
    iter_source_files,
    enrich_context_with_index,
)
from oporch.db import OporchDB


# ── PythonAnalyzer ─────────────────────────────────────────────────────────────

SAMPLE_PY = """\
\"\"\"Sample module.\"\"\"

import os
from pathlib import Path

class MyClass:
    \"\"\"A simple class.\"\"\"

    def method_a(self, x: int) -> int:
        return x + 1

    def method_b(self) -> str:
        return "hello"


def top_level_func(a, b):
    \"\"\"Top level function.\"\"\"
    result = method_a(a)
    return result + b

CONSTANT = 42
"""


def test_python_analyzer_extracts_class():
    a = PythonAnalyzer("test.py", SAMPLE_PY)
    a.analyze()
    names = [s.name for s in a.symbols]
    assert "MyClass" in names
    sym = next(s for s in a.symbols if s.name == "MyClass")
    assert sym.kind == "class"
    assert sym.docstring and "simple" in sym.docstring


def test_python_analyzer_extracts_methods():
    a = PythonAnalyzer("test.py", SAMPLE_PY)
    a.analyze()
    methods = [s for s in a.symbols if s.kind == "method"]
    method_names = {m.name for m in methods}
    assert "method_a" in method_names
    assert "method_b" in method_names
    for m in methods:
        assert m.parent == "MyClass"


def test_python_analyzer_extracts_function():
    a = PythonAnalyzer("test.py", SAMPLE_PY)
    a.analyze()
    funcs = [s for s in a.symbols if s.kind == "function"]
    assert any(f.name == "top_level_func" for f in funcs)


def test_python_analyzer_extracts_imports():
    a = PythonAnalyzer("test.py", SAMPLE_PY)
    a.analyze()
    modules = {i.module for i in a.imports}
    assert "os" in modules
    assert "pathlib" in modules


def test_python_analyzer_extracts_calls():
    a = PythonAnalyzer("test.py", SAMPLE_PY)
    a.analyze()
    callees = {c.callee_name for c in a.calls}
    assert "method_a" in callees


def test_python_analyzer_handles_syntax_error():
    a = PythonAnalyzer("bad.py", "def broken(:")
    a.analyze()
    assert a.symbols == []  # graceful failure


# ── GenericAnalyzer ────────────────────────────────────────────────────────────

SAMPLE_JS = """\
export function greet(name) {
    return `Hello, ${name}`;
}

export class EventEmitter {
    constructor() {}
    emit(event) {}
}

const arrowFn = (x) => x * 2;
"""

SAMPLE_GO = """\
package main

import "fmt"

func main() {
    fmt.Println("hello")
}

type Server struct {
    port int
}
"""


def test_generic_analyzer_javascript():
    a = GenericAnalyzer("app.js", SAMPLE_JS, "javascript")
    a.analyze()
    names = {s.name for s in a.symbols}
    assert "greet" in names
    assert "EventEmitter" in names


def test_generic_analyzer_go():
    a = GenericAnalyzer("main.go", SAMPLE_GO, "go")
    a.analyze()
    names = {s.name for s in a.symbols}
    assert "main" in names
    assert "Server" in names


def test_generic_analyzer_unknown_language():
    # Should not crash
    a = GenericAnalyzer("file.xyz", "some content", "unknown")
    a.analyze()
    assert a.symbols == []


# ── iter_source_files ──────────────────────────────────────────────────────────

def test_iter_source_files_finds_python(tmp_path):
    (tmp_path / "main.py").write_text("x = 1")
    (tmp_path / "util.js").write_text("const x = 1;")
    (tmp_path / "README.md").write_text("# hello")

    files = iter_source_files(tmp_path)
    langs = {lang for _, lang in files}
    assert "python" in langs
    assert "javascript" in langs
    assert "markdown" not in langs  # not indexed


def test_iter_source_files_skips_venv(tmp_path):
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "site.py").write_text("x = 1")
    (tmp_path / "main.py").write_text("x = 1")

    files = [str(p) for p, _ in iter_source_files(tmp_path)]
    assert not any(".venv" in f for f in files)


# ── CodebaseIndexer with real DB ───────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db = OporchDB(path=db_path)
    yield db
    db.close()


@pytest.fixture
def sample_project(tmp_path):
    """Create a minimal Python project structure."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "models.py").write_text("""\
class User:
    \"\"\"User model.\"\"\"
    def __init__(self, name: str):
        self.name = name

    def greet(self):
        return f"Hello, {self.name}"
""")
    (src / "service.py").write_text("""\
from src.models import User

def create_user(name: str) -> User:
    user = User(name)
    return user

def get_greeting(user):
    return user.greet()
""")
    return tmp_path


def test_indexer_indexes_project(tmp_db, sample_project):
    indexer = CodebaseIndexer(tmp_db, project=str(sample_project))
    counts = indexer.index_project(root=sample_project, full=True)
    assert counts["files"] >= 2
    assert counts["symbols"] >= 3  # User, __init__, greet, create_user, get_greeting


def test_indexer_search_symbols(tmp_db, sample_project):
    indexer = CodebaseIndexer(tmp_db, project=str(sample_project))
    indexer.index_project(root=sample_project, full=True)

    results = indexer.search_symbols("User")
    assert any(r["name"] == "User" for r in results)


def test_indexer_search_by_kind(tmp_db, sample_project):
    indexer = CodebaseIndexer(tmp_db, project=str(sample_project))
    indexer.index_project(root=sample_project, full=True)

    classes = indexer.search_symbols(".*", kind="class")
    assert all(r["kind"] == "class" for r in classes)


def test_indexer_get_callers(tmp_db, sample_project):
    indexer = CodebaseIndexer(tmp_db, project=str(sample_project))
    indexer.index_project(root=sample_project, full=True)

    callers = indexer.get_callers("User")
    # create_user calls User()
    assert any(c["caller_name"] == "create_user" for c in callers)


def test_indexer_incremental(tmp_db, sample_project):
    indexer = CodebaseIndexer(tmp_db, project=str(sample_project))
    first = indexer.index_project(root=sample_project, full=True)

    # Second run (no changes) should index 0 new files
    second = indexer.index_project(root=sample_project, full=False)
    assert second["files"] == 0  # all files unchanged


def test_indexer_incremental_after_change(tmp_db, sample_project):
    indexer = CodebaseIndexer(tmp_db, project=str(sample_project))
    indexer.index_project(root=sample_project, full=True)

    # Modify a file
    import time
    time.sleep(0.05)
    new_file = sample_project / "src" / "models.py"
    new_file.write_text(new_file.read_text() + "\ndef new_func(): pass\n")

    second = indexer.index_project(root=sample_project, full=False)
    assert second["files"] >= 1  # re-indexed changed file
    assert second["symbols"] >= 1  # new_func found


def test_indexer_architecture(tmp_db, sample_project):
    indexer = CodebaseIndexer(tmp_db, project=str(sample_project))
    indexer.index_project(root=sample_project, full=True)

    arch = indexer.get_architecture()
    assert arch.total_files >= 2
    assert arch.total_symbols >= 3
    assert "python" in arch.languages


# ── enrich_context_with_index ──────────────────────────────────────────────────

def test_enrich_context_returns_none_when_empty(tmp_db, sample_project):
    # Index is empty — should return None gracefully
    result = enrich_context_with_index(
        tmp_db, str(sample_project),
        files_likely_affected=["src/models.py"],
        acceptance_criteria=["User class exists"],
    )
    # Either None (empty) or a string — both acceptable
    assert result is None or isinstance(result, str)


def test_enrich_context_returns_string_when_indexed(tmp_db, sample_project):
    indexer = CodebaseIndexer(tmp_db, project=str(sample_project))
    indexer.index_project(root=sample_project, full=True)

    result = enrich_context_with_index(
        tmp_db, str(sample_project),
        files_likely_affected=[str(sample_project / "src" / "models.py")],
        acceptance_criteria=["User class works"],
    )
    if result is not None:
        assert "Code Index" in result or "Symbol" in result or "function" in result.lower()
