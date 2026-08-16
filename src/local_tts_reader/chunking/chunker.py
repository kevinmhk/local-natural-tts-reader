from __future__ import annotations

from dataclasses import dataclass

from local_tts_reader.chunking.sentences import hard_split, split_clauses, split_sentences
from local_tts_reader.domain.models import Chunk, Document, sha256_text


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Deterministic chunk-size and pacing policy."""

    target_chars: int = 1_200
    hard_limit_chars: int = 1_800
    paragraph_pause_ms: int = 350
    section_pause_ms: int = 800
    version: str = "1"

    def __post_init__(self) -> None:
        if self.target_chars <= 0 or self.hard_limit_chars < self.target_chars:
            raise ValueError("hard_limit_chars must be greater than or equal to target_chars")


@dataclass(frozen=True, slots=True)
class _Passage:
    text: str
    boundary: str
    pause_ms: int
    section_title: str | None
    forced: bool = False


def _split_oversized(passage: _Passage, config: ChunkingConfig) -> list[_Passage]:
    candidates = split_sentences(passage.text)
    if len(candidates) == 1:
        candidates = split_clauses(passage.text)
    result: list[_Passage] = []
    for candidate in candidates:
        if len(candidate) <= config.hard_limit_chars:
            result.append(
                _Passage(
                    text=candidate,
                    boundary="sentence",
                    pause_ms=passage.pause_ms,
                    section_title=passage.section_title,
                )
            )
            continue
        result.extend(
            _Passage(
                text=part,
                boundary="forced",
                pause_ms=passage.pause_ms,
                section_title=passage.section_title,
                forced=True,
            )
            for part in hard_split(candidate, config.hard_limit_chars)
        )
    return result


def _passages(document: Document, config: ChunkingConfig) -> list[_Passage]:
    passages: list[_Passage] = []
    for section in document.sections:
        if section.heading:
            passages.append(
                _Passage(
                    text=section.heading,
                    boundary="section",
                    pause_ms=config.section_pause_ms,
                    section_title=section.heading,
                )
            )
        for block in section.blocks:
            passages.append(
                _Passage(
                    text=block.text,
                    boundary="paragraph",
                    pause_ms=config.paragraph_pause_ms,
                    section_title=section.heading,
                )
            )
    expanded: list[_Passage] = []
    for passage in passages:
        if len(passage.text) <= config.hard_limit_chars:
            expanded.append(passage)
        else:
            expanded.extend(_split_oversized(passage, config))
    return expanded


def chunk_document(document: Document, config: ChunkingConfig | None = None) -> tuple[Chunk, ...]:
    """Create stable, ordered chunks without losing or repeating spoken text."""
    policy = config or ChunkingConfig()
    packed: list[_Passage] = []
    current: _Passage | None = None
    for passage in _passages(document, policy):
        combined = f"{current.text}\n\n{passage.text}" if current else passage.text
        if current and len(combined) <= policy.target_chars:
            current = _Passage(
                text=combined,
                boundary=passage.boundary,
                pause_ms=passage.pause_ms,
                section_title=current.section_title or passage.section_title,
                forced=current.forced or passage.forced,
            )
            continue
        if current:
            packed.append(current)
        current = passage
    if current:
        packed.append(current)

    chunks: list[Chunk] = []
    for ordinal, passage in enumerate(packed):
        text_hash = sha256_text(passage.text)
        identity = sha256_text(f"{document.source_hash}:{policy.version}:{ordinal}:{text_hash}")
        chunks.append(
            Chunk(
                chunk_id=identity,
                document_id=document.document_id,
                ordinal=ordinal,
                text=passage.text,
                text_hash=text_hash,
                boundary=passage.boundary,
                pause_after_ms=passage.pause_ms,
                section_title=passage.section_title,
                warnings=("forced_boundary",) if passage.forced else (),
            )
        )
    return tuple(chunks)
