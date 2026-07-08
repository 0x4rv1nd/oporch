"""Secret redaction utilities.

Strips API-key-shaped strings from text before writing to event logs
or decision ledgers.  PRD Section 7 / 22 / Gap #7.
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
]

_REDACTED = "[REDACTED]"


def redact_secrets(text: str) -> str:
    """Replace secret-shaped substrings with ``[REDACTED]``.

    Designed to be conservative — it may over-redact long hex/base64
    strings, but that is preferable to leaking real credentials.
    """
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_REDACTED, result)
    return result
