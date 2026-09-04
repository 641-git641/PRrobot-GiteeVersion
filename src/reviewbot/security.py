from __future__ import annotations

import re

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s`]+"),
    re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*)[^\s,;`]+"),
    re.compile(r"\b(?:sk|ds)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN [^-]+-----[\s\S]*?-----END [^-]+-----"),
)


def redact_sensitive_text(value: str) -> str:
    result = value
    for pattern in _SENSITIVE_PATTERNS:
        result = pattern.sub(_replace_match, result)
    return result


def _replace_match(match: re.Match[str]) -> str:
    groups = match.groups()
    if groups:
        return f"{groups[0]}[REDACTED]"
    return "[REDACTED]"
