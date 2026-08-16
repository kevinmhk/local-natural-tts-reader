from __future__ import annotations

import re
import unicodedata

from local_tts_reader.domain.models import Block, Document, Section

_LIGATURES = str.maketrans(
    {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}
)


def normalize_spoken_text(text: str) -> str:
    """Normalize presentation artifacts without rewriting meaning."""
    value = unicodedata.normalize("NFC", text)
    value = value.translate(_LIGATURES)
    value = value.replace("\u00a0", " ").replace("\u200b", "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[\t ]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def clean_document(document: Document) -> Document:
    """Apply common pure cleaning rules to all document blocks."""
    sections: list[Section] = []
    for section in document.sections:
        heading = normalize_spoken_text(section.heading or "") or None
        blocks = tuple(
            Block(
                kind=block.kind,
                text=cleaned,
                source_spans=block.source_spans,
            )
            for block in section.blocks
            if (cleaned := normalize_spoken_text(block.text))
        )
        if heading or blocks:
            sections.append(
                Section(
                    heading=heading,
                    level=section.level,
                    blocks=blocks,
                    source_spans=section.source_spans,
                )
            )
    return document.model_copy(update={"sections": tuple(sections), "pipeline_version": "1"})
