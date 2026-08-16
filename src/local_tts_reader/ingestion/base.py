from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from local_tts_reader.domain.models import Document


class IngestionError(ValueError):
    """Base error for a rejected or unreadable input."""


class NeedsOcrError(IngestionError):
    """Raised when a PDF contains no useful text layer."""


@dataclass(frozen=True, slots=True)
class ImportedSource:
    """Immutable source copy prepared for extraction."""

    document_id: str
    path: Path
    original_name: str
    source_hash: str
    media_type: str


class Extractor(Protocol):
    """Contract implemented by every local format extractor."""

    def extract(self, source: ImportedSource) -> Document:
        """Extract a format-independent document."""
        ...
