from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_path


def _default_data_dir() -> Path:
    override = os.getenv("LOCAL_TTS_READER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return user_data_path("LocalNaturalTTSReader", appauthor=False)


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with safe local defaults."""

    data_dir: Path = field(default_factory=_default_data_dir)
    max_file_bytes: int = 100 * 1024 * 1024
    max_pdf_pages: int = 2_000
    chunk_target_chars: int = 1_200
    chunk_hard_limit_chars: int = 1_800
    paragraph_pause_ms: int = 350
    section_pause_ms: int = 800
    min_free_bytes: int = 256 * 1024 * 1024

    def ensure_directories(self) -> None:
        """Create application-owned directories."""
        for child in ("documents", "audio", "exports", "tmp"):
            (self.data_dir / child).mkdir(parents=True, exist_ok=True)
