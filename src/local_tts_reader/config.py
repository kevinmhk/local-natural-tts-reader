from __future__ import annotations

import json
import tomllib
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit"
DEFAULT_VOICE = "Aiden"
DEFAULT_LANGUAGE = "English"
DEFAULT_INSTRUCTION = (
    "Calm, clear long-form narration with restrained expression and natural pauses."
)
DEFAULT_SPEED = 1.0
DEFAULT_CHUNK_TARGET_CHARS = 280
DEFAULT_CHUNK_HARD_LIMIT_CHARS = 360
LEGACY_CHUNK_TARGET_CHARS = 1_200
LEGACY_CHUNK_HARD_LIMIT_CHARS = 1_800


class ConfigurationError(ValueError):
    """Raised when the reader configuration cannot be loaded safely."""


def default_data_dir() -> Path:
    """Return the human-visible default workspace for one local reader."""
    return Path.home() / ".local-natural-tts-reader"


def default_config_path() -> Path:
    """Return the default TOML configuration path."""
    return default_data_dir() / "config.toml"


def _resolved_path(path: Path) -> Path:
    return path.expanduser().resolve()


def _string(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return value


def _integer(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{key} must be an integer")
    return value


def _number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigurationError(f"{key} must be a number")
    return float(value)


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings loaded from one human-editable TOML file."""

    data_dir: Path = field(default_factory=default_data_dir)
    default_model: str = DEFAULT_MODEL
    default_voice: str = DEFAULT_VOICE
    default_language: str = DEFAULT_LANGUAGE
    default_instruction: str = DEFAULT_INSTRUCTION
    default_speed: float = DEFAULT_SPEED
    max_file_bytes: int = 100 * 1024 * 1024
    max_pdf_pages: int = 2_000
    chunk_target_chars: int = DEFAULT_CHUNK_TARGET_CHARS
    chunk_hard_limit_chars: int = DEFAULT_CHUNK_HARD_LIMIT_CHARS
    paragraph_pause_ms: int = 350
    section_pause_ms: int = 800
    min_free_bytes: int = 256 * 1024 * 1024
    config_path: Path | None = None

    def __post_init__(self) -> None:
        if self.max_file_bytes <= 0:
            raise ConfigurationError("max_file_bytes must be greater than zero")
        if self.max_pdf_pages <= 0:
            raise ConfigurationError("max_pdf_pages must be greater than zero")
        if self.chunk_target_chars <= 0:
            raise ConfigurationError("chunk_target_chars must be greater than zero")
        if self.chunk_hard_limit_chars < self.chunk_target_chars:
            raise ConfigurationError(
                "chunk_hard_limit_chars must be greater than or equal to chunk_target_chars"
            )
        if self.paragraph_pause_ms < 0 or self.section_pause_ms < 0:
            raise ConfigurationError("pause durations must not be negative")
        if self.min_free_bytes < 0:
            raise ConfigurationError("min_free_bytes must not be negative")
        if not 0.5 < self.default_speed <= 2.0:
            raise ConfigurationError("default_speed must be greater than 0.5 and at most 2.0")

    @classmethod
    def load(cls, config_path: Path | None = None) -> Settings:
        """Load TOML settings, creating a documented default file when absent."""
        explicit_path = config_path is not None
        path = _resolved_path(config_path or default_config_path())
        fallback_data_dir = path.parent if explicit_path else default_data_dir()
        if not path.exists():
            settings = cls(data_dir=fallback_data_dir, config_path=path)
            settings.write(path)
            return settings
        if not path.is_file():
            raise ConfigurationError(f"configuration path is not a file: {path}")
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ConfigurationError(f"unable to read configuration {path}: {error}") from error
        if not isinstance(raw, dict):
            raise ConfigurationError("configuration root must be a TOML table")
        allowed = {
            "data_dir",
            "default_model",
            "default_voice",
            "default_language",
            "default_instruction",
            "default_speed",
            "max_file_bytes",
            "max_pdf_pages",
            "chunk_target_chars",
            "chunk_hard_limit_chars",
            "paragraph_pause_ms",
            "section_pause_ms",
            "min_free_bytes",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ConfigurationError(f"unknown configuration key: {unknown[0]}")
        defaults = cls(data_dir=fallback_data_dir, config_path=path)
        settings = cls(
            data_dir=_resolved_path(
                Path(_string(raw.get("data_dir", str(defaults.data_dir)), "data_dir"))
            ),
            default_model=_string(
                raw.get("default_model", defaults.default_model), "default_model"
            ),
            default_voice=_string(
                raw.get("default_voice", defaults.default_voice), "default_voice"
            ),
            default_language=_string(
                raw.get("default_language", defaults.default_language), "default_language"
            ),
            default_instruction=_string(
                raw.get("default_instruction", defaults.default_instruction),
                "default_instruction",
            ),
            default_speed=_number(
                raw.get("default_speed", defaults.default_speed), "default_speed"
            ),
            max_file_bytes=_integer(
                raw.get("max_file_bytes", defaults.max_file_bytes), "max_file_bytes"
            ),
            max_pdf_pages=_integer(
                raw.get("max_pdf_pages", defaults.max_pdf_pages), "max_pdf_pages"
            ),
            chunk_target_chars=_integer(
                raw.get("chunk_target_chars", defaults.chunk_target_chars), "chunk_target_chars"
            ),
            chunk_hard_limit_chars=_integer(
                raw.get("chunk_hard_limit_chars", defaults.chunk_hard_limit_chars),
                "chunk_hard_limit_chars",
            ),
            paragraph_pause_ms=_integer(
                raw.get("paragraph_pause_ms", defaults.paragraph_pause_ms), "paragraph_pause_ms"
            ),
            section_pause_ms=_integer(
                raw.get("section_pause_ms", defaults.section_pause_ms), "section_pause_ms"
            ),
            min_free_bytes=_integer(
                raw.get("min_free_bytes", defaults.min_free_bytes), "min_free_bytes"
            ),
            config_path=path,
        )
        if (
            settings.chunk_target_chars == LEGACY_CHUNK_TARGET_CHARS
            and settings.chunk_hard_limit_chars == LEGACY_CHUNK_HARD_LIMIT_CHARS
        ):
            settings = replace(
                settings,
                chunk_target_chars=DEFAULT_CHUNK_TARGET_CHARS,
                chunk_hard_limit_chars=DEFAULT_CHUNK_HARD_LIMIT_CHARS,
            )
            settings.write(path)
        return settings

    def write(self, config_path: Path | None = None) -> Path:
        """Write this settings object as a commented, editable TOML configuration."""
        path = _resolved_path(config_path or self.config_path or default_config_path())
        displayed_data_dir = (
            "~/.local-natural-tts-reader"
            if self.data_dir == default_data_dir()
            else str(self.data_dir)
        )
        content = "\n".join(
            (
                "# Local Natural TTS Reader configuration.",
                "# Edit this file, then run `reader doctor` to verify the result.",
                "",
                "# All sources, previews, SQLite state, generated WAVs, and reader-managed models.",
                f"data_dir = {json.dumps(displayed_data_dir)}",
                "",
                "# Default Qwen3-TTS CustomVoice narration profile.",
                f"default_model = {json.dumps(self.default_model)}",
                f"default_voice = {json.dumps(self.default_voice)}",
                f"default_language = {json.dumps(self.default_language)}",
                f"default_instruction = {json.dumps(self.default_instruction)}",
                f"default_speed = {self.default_speed}",
                "",
                "# Safety, chunking, and pacing limits. Keep Qwen3-TTS chunks short.",
                f"max_file_bytes = {self.max_file_bytes}",
                f"max_pdf_pages = {self.max_pdf_pages}",
                f"chunk_target_chars = {self.chunk_target_chars}",
                f"chunk_hard_limit_chars = {self.chunk_hard_limit_chars}",
                f"paragraph_pause_ms = {self.paragraph_pause_ms}",
                f"section_pause_ms = {self.section_pause_ms}",
                f"min_free_bytes = {self.min_free_bytes}",
                "",
            )
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(content, encoding="utf-8", newline="\n")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def ensure_directories(self) -> None:
        """Create application-owned directories below the configured workspace."""
        for child in ("documents", "audio", "exports", "tmp"):
            (self.data_dir / child).mkdir(parents=True, exist_ok=True)
