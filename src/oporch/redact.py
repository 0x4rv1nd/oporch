"""Secret redaction utilities (v2 hardening, PRD §10).

Strips secret-shaped strings from any text before it is written to the
persistent SQLite tables (events / decisions / agent_memory / work unit
payloads) or legacy JSONL mirrors. Also provides :func:`is_sensitive_path`
so repo-summary scans and memory ingestion can skip credential files.
"""

from __future__ import annotations

import re

# Patterns that look like secrets / API keys / tokens.
# Each pattern is compiled once and reused.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Generic bearer tokens  (Bearer <token>)
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    # Common API key formats: sk-..., pk-..., api-..., key-... (≥20 chars after prefix)
    re.compile(r"\b(?:sk|pk|api|key|token|secret|password)[-_][A-Za-z0-9\-._]{20,}\b", re.IGNORECASE),
    # Hex strings that look like secrets (≥32 hex chars, standalone)
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    # Base64-ish blobs ≥40 chars (letters+digits+/+=, no spaces)
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,3}\b"),
    # AWS-style keys: AKIA...
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    # Generic "key=<value>" or "key: <value>" where value looks secret-ish
    re.compile(r"(?:api_key|apikey|secret_key|auth_token|access_token)\s*[:=]\s*\S+", re.IGNORECASE),
    # --- v2 extensions (§10) ---
    # Connection strings with credentials: scheme://user:password@host
    re.compile(
        r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^@\s/]+@"
    ),
    # PEM private key blocks
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    # JWTs: header.payload.signature
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    # GitHub tokens
    re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    # Slack tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # .env-style assignments of sensitive variable names
    re.compile(
        r"^\s*[A-Z_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|ACCESS_KEY)[A-Z_]*"
        r"\s*=\s*\S+",
        re.MULTILINE,
    ),
]

_REDACTED = "[REDACTED]"

# File names / patterns that must never be scanned into context or memory.
_SENSITIVE_PATH_RE = re.compile(
    r"(?:^|[/\\])(?:"
    r"\.env.*|.*\.env\b"                 # .env, .env.local, prod.env
    r"|.*\.pem\b|.*\.key\b|.*\.p12\b"    # certs & keys
    r"|id_rsa.*|id_ed25519.*|id_ecdsa.*"  # ssh identities
    r"|.*credentials?.*\..+|credentials.*|\.aws[/\\].*"  # cloud creds
    r"|.*secret.*|\.netrc\b|\.npmrc\b"
    r")$",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """Replace secret-shaped substrings with ``[REDACTED]``.

    Designed to be conservative — it may over-redact long hex/base64
    strings, but that is preferable to leaking real credentials.
    """
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_REDACTED, result)
    return result


def is_sensitive_path(path: str) -> bool:
    """True when a file path looks like it holds credentials.

    Used by repo-summary scans and agent-memory ingestion so secrets never
    enter agent context in the first place (§10).
    """
    if not path:
        return False
    normalized = path.replace("\\\\", "/")
    return bool(_SENSITIVE_PATH_RE.search(normalized))


def filter_sensitive_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if not is_sensitive_path(p)]
