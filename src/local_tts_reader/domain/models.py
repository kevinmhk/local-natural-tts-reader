from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    """Base class for immutable domain values."""

    model_config = ConfigDict(frozen=True)


class SourceSpan(FrozenModel):
    """Location in the imported source from which text originated."""

    page_start: int | None = None
    page_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None


class Block(FrozenModel):
    """One readable structural block."""

    kind: str
    text: str
    source_spans: tuple[SourceSpan, ...] = ()


class Section(FrozenModel):
    """A heading and its ordered readable blocks."""

    heading: str | None = None
    level: int | None = None
    blocks: tuple[Block, ...] = ()
    source_spans: tuple[SourceSpan, ...] = ()


class Document(FrozenModel):
    """Format-independent extracted document."""

    document_id: str
    source_name: str
    source_hash: str
    media_type: str
    title: str | None = None
    author: str | None = None
    sections: tuple[Section, ...] = ()
    warnings: tuple[str, ...] = ()
    pipeline_version: str = "1"


class Chunk(FrozenModel):
    """A deterministic passage submitted to a TTS engine."""

    chunk_id: str
    document_id: str
    ordinal: int
    text: str
    text_hash: str
    boundary: str
    pause_after_ms: int
    section_title: str | None = None
    source_spans: tuple[SourceSpan, ...] = ()
    warnings: tuple[str, ...] = ()


class SynthesisProfile(FrozenModel):
    """Complete identity of speech-affecting settings."""

    engine: str = "mlx_audio_qwen3"
    mode: str = "custom_voice"
    model: str = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit"
    model_revision: str | None = None
    speaker: str = "Aiden"
    language: str = "English"
    instruction: str = (
        "Calm, clear long-form narration with restrained expression and natural pauses."
    )
    speed: float = Field(default=1.0, gt=0.5, le=2.0)
    generation: dict[str, Any] = Field(default_factory=dict)
    adapter_version: str = "1"

    def identity_json(self) -> str:
        """Return stable JSON used for cache identity."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @property
    def profile_hash(self) -> str:
        """Return the stable speech profile hash."""
        return hashlib.sha256(self.identity_json().encode()).hexdigest()


class AudioArtifact(FrozenModel):
    """Validated audio generated for one chunk and profile."""

    cache_key: str
    chunk_id: str
    profile_hash: str
    path: Path
    duration_seconds: float
    sample_rate: int


class SpeakResult(FrozenModel):
    """Summary of one synthesis/playback run."""

    state: str
    generated_count: int = 0
    cache_hit_count: int = 0
    played_count: int = 0
    next_ordinal: int = 0


class DocumentStatus(FrozenModel):
    """User-visible status summary."""

    document_id: str
    state: str
    chunk_count: int
    ready_count: int
    next_ordinal: int


class EngineCapabilities(FrozenModel):
    """Features exposed by a TTS engine."""

    modes: tuple[str, ...]
    supports_instructions: bool
    supports_streaming: bool


class LoadedModelInfo(FrozenModel):
    """Resolved model information after engine loading."""

    model: str
    engine: str


def sha256_text(value: str) -> str:
    """Hash UTF-8 text deterministically."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def document_text(document: Document) -> str:
    """Render the exact ordered text that will be chunked and spoken."""
    passages: list[str] = []
    for section in document.sections:
        if section.heading and section.heading.strip():
            passages.append(section.heading.strip())
        passages.extend(block.text.strip() for block in section.blocks if block.text.strip())
    return "\n\n".join(passages)
