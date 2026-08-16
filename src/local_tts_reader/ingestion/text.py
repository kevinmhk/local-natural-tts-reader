from __future__ import annotations

import re

from charset_normalizer import from_bytes

from local_tts_reader.domain.models import Block, Document, Section, SourceSpan
from local_tts_reader.ingestion.base import ImportedSource, IngestionError


def sections_from_plain_text(text: str) -> tuple[Section, ...]:
    """Convert blank-line-separated prose into one structured section."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = tuple(
        Block(kind="paragraph", text=paragraph.strip())
        for paragraph in re.split(r"\n\s*\n", normalized)
        if paragraph.strip()
    )
    return (Section(blocks=blocks),) if blocks else ()


class TextExtractor:
    """Decode and structure a local plain-text file."""

    def extract(self, source: ImportedSource) -> Document:
        raw = source.path.read_bytes()
        match = from_bytes(raw).best()
        if match is None:
            raise IngestionError("unable to determine text encoding")
        text = str(match)
        warnings: list[str] = []
        if match.chaos > 0.2:
            warnings.append(f"low_encoding_confidence:{match.encoding}")
        sections = sections_from_plain_text(text)
        if not sections:
            raise IngestionError("document contains no readable text")
        first_span = SourceSpan(line_start=1, line_end=text.count("\n") + 1)
        sections = tuple(
            section.model_copy(update={"source_spans": (first_span,)}) for section in sections
        )
        return Document(
            document_id=source.document_id,
            source_name=source.original_name,
            source_hash=source.source_hash,
            media_type=source.media_type,
            sections=sections,
            warnings=tuple(warnings),
        )
