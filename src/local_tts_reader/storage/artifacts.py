from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import soundfile as sf


@dataclass(frozen=True, slots=True)
class WavInfo:
    """Validated WAV metadata."""

    duration_seconds: float
    sample_rate: int
    channels: int


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text and expose it only after completion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    """Write stable, human-readable JSON atomically."""
    atomic_write_text(path, json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")


def validate_wav(path: Path) -> WavInfo:
    """Reject missing, empty, or undecodable WAV artifacts."""
    if not path.is_file() or path.stat().st_size <= 44:
        raise ValueError(f"audio artifact is missing or empty: {path}")
    info = sf.info(path)
    if info.frames <= 0 or info.samplerate <= 0 or info.channels <= 0:
        raise ValueError(f"audio artifact has invalid metadata: {path}")
    return WavInfo(
        duration_seconds=info.frames / info.samplerate,
        sample_rate=info.samplerate,
        channels=info.channels,
    )


def audio_path(audio_root: Path, cache_key: str) -> Path:
    """Return a content-addressed WAV path."""
    return audio_root / cache_key[:2] / f"{cache_key}.wav"


def cache_usage(audio_root: Path) -> tuple[int, int]:
    """Return file count and total bytes in the local audio cache."""
    files = [path for path in audio_root.rglob("*.wav") if path.is_file()]
    return len(files), sum(path.stat().st_size for path in files)
