from __future__ import annotations

import re

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?\u3002\uff01\uff1f])\s+")
_CLAUSE = re.compile(r".+?(?:[;:,\uff1b\uff0c]\s*|$)", re.DOTALL)


def split_sentences(text: str) -> list[str]:
    """Split prose at common Western and CJK sentence endings."""
    return [part.strip() for part in _SENTENCE_BOUNDARY.split(text) if part.strip()]


def split_clauses(text: str) -> list[str]:
    """Split one oversized sentence at clause punctuation."""
    return [match.group(0).strip() for match in _CLAUSE.finditer(text) if match.group(0).strip()]


def hard_split(text: str, limit: int) -> list[str]:
    """Split Unicode text without exceeding a hard character limit."""
    parts: list[str] = []
    remaining = text.strip()
    while len(remaining) > limit:
        break_at = remaining.rfind(" ", 0, limit + 1)
        if break_at < limit // 2:
            break_at = limit
        parts.append(remaining[:break_at].strip())
        remaining = remaining[break_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts
