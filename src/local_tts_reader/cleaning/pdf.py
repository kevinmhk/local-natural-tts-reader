from __future__ import annotations

import re
from collections import Counter

_PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d+(?:\s+of\s+\d+)?$", re.IGNORECASE)


def normalize_repeated_line(line: str) -> str:
    """Normalize a page-region line for repeated-noise detection."""
    return re.sub(r"\s+", " ", line).strip().casefold()


def repeated_page_noise(page_lines: list[list[str]]) -> set[str]:
    """Find lines repeated in the first or last region of at least three pages."""
    if len(page_lines) < 3:
        return set()
    counter: Counter[str] = Counter()
    for lines in page_lines:
        nonempty = [line for line in lines if line.strip()]
        candidates = {normalize_repeated_line(line) for line in nonempty[:2] + nonempty[-2:]}
        counter.update(
            candidate for candidate in candidates if candidate and not _PAGE_NUMBER.match(candidate)
        )
    threshold = max(3, (len(page_lines) * 3 + 4) // 5)
    return {line for line, count in counter.items() if count >= threshold}


def is_page_number(line: str) -> bool:
    """Return whether a whole line is strictly a page marker."""
    return bool(_PAGE_NUMBER.fullmatch(line.strip()))


def join_pdf_lines(lines: list[str]) -> list[str]:
    """Join wrapped PDF lines and conservatively undo end-of-line hyphenation."""
    paragraphs: list[str] = []
    current = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            if current:
                paragraphs.append(current.strip())
                current = ""
            continue
        if current.endswith("-") and line[:1].islower():
            current = current[:-1] + line
        elif current:
            current += " " + line
        else:
            current = line
    if current:
        paragraphs.append(current.strip())
    return paragraphs
