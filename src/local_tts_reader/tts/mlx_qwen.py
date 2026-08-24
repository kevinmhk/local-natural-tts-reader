from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import unicodedata
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import numpy as np
import soundfile as sf
from huggingface_hub import snapshot_download

from local_tts_reader.chunking.sentences import split_clauses, split_sentences
from local_tts_reader.domain.models import (
    AudioArtifact,
    Chunk,
    EngineCapabilities,
    LoadedModelInfo,
    SynthesisProfile,
    sha256_text,
)
from local_tts_reader.storage.artifacts import validate_wav


class ModelUnavailableError(RuntimeError):
    """Raised when normal runtime cannot find already downloaded weights."""


class SynthesisLimitError(RuntimeError):
    """Raised when Qwen3-TTS does not terminate before its output limit."""


_FALLBACK_HARD_LIMIT_CHARS = 120
_FALLBACK_MIN_PHRASE_CHARS = 32
_FALLBACK_PAUSE_MS = 120
_FALLBACK_GENERATION_ATTEMPTS = 3


def default_max_tokens(text: str, *, minimum: int = 256) -> int:
    """Return a conservative Qwen3-TTS token budget for one short text chunk."""
    word_count = len(text.split())
    return min(1_024, max(minimum, 128 + (word_count * 12)))


def _pack_clauses(parts: tuple[str, ...], source_length: int) -> tuple[str, ...]:
    """Pack whole clauses into phrase-sized requests without arbitrary word cuts."""
    limit = max(64, min(_FALLBACK_HARD_LIMIT_CHARS, (source_length + 1) // 2))
    groups: list[list[str]] = []
    current: list[str] = []
    for part in parts:
        candidate = " ".join((*current, part))
        if current and len(candidate) > limit:
            groups.append(current)
            current = [part]
        else:
            current.append(part)
    if current:
        groups.append(current)
    if len(groups) > 1 and len(" ".join(groups[-1])) < _FALLBACK_MIN_PHRASE_CHARS:
        groups[-2].extend(groups.pop())
    return tuple(" ".join(group) for group in groups)


def fallback_segments(text: str) -> tuple[str, ...]:
    """Return complete sentences or whole-clause groups for a bounded retry."""
    sentences = tuple(split_sentences(text))
    if len(sentences) > 1:
        return sentences
    clauses = tuple(split_clauses(text))
    if len(clauses) > 1:
        parts = _pack_clauses(clauses, len(text))
        if len(parts) > 1:
            return parts
    return ()


def model_directory(models_root: Path, model_id: str) -> Path:
    """Map one repository ID to an application-owned local directory."""
    return models_root / model_id.replace("/", "--")


def setup_model(model_id: str, models_root: Path, revision: str | None = None) -> Path:
    """Explicitly download one model for later offline inference."""
    destination = model_directory(models_root, model_id)
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=model_id, revision=revision, local_dir=destination)
    if not _model_is_complete(destination):
        raise ModelUnavailableError(f"downloaded model is incomplete: {destination}")
    return destination


def _model_is_complete(path: Path) -> bool:
    if not path.is_dir() or not (path / "config.json").is_file():
        return False
    if any(path.rglob("*.incomplete")):
        return False
    index_path = path / "model.safetensors.index.json"
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            names = set(index["weight_map"].values())
        except (KeyError, TypeError, ValueError, OSError):
            return False
        return bool(names) and all((path / name).is_file() for name in names)
    return any(
        weight.is_file() and weight.stat().st_size > 0 for weight in path.glob("*.safetensors")
    )


def resolve_local_model(model: str, models_root: Path | None = None) -> Path:
    """Resolve only existing local weights; never trigger a network request."""
    direct = Path(model).expanduser()
    if _model_is_complete(direct):
        return direct.resolve()
    if models_root is not None:
        managed = model_directory(models_root, model)
        if _model_is_complete(managed):
            return managed
    try:
        cached = snapshot_download(repo_id=model, local_files_only=True)
    except Exception as error:
        raise ModelUnavailableError(
            f"model is not installed locally: {model}; run 'reader models setup' first"
        ) from error
    cached_path = Path(cached)
    if not _model_is_complete(cached_path):
        raise ModelUnavailableError(f"cached model is incomplete: {model}")
    return cached_path


def installed_model_revision(path: Path) -> str:
    """Return the installed Hugging Face commit or a stable local manifest identity."""
    tree_root = path / ".cache" / "huggingface" / "trees"
    commits = sorted(
        candidate.stem
        for candidate in tree_root.glob("*.json")
        if len(candidate.stem) == 40
        and all(character in "0123456789abcdef" for character in candidate.stem)
    )
    if len(commits) == 1:
        return commits[0]
    digest = hashlib.sha256()
    for candidate in sorted(
        (*path.glob("*.json"), *path.glob("*.safetensors")),
        key=lambda item: item.name,
    ):
        digest.update(candidate.name.encode())
        digest.update(str(candidate.stat().st_size).encode())
        if candidate.suffix == ".json":
            digest.update(candidate.read_bytes())
    return f"local-{digest.hexdigest()}"


class MlxQwenTtsEngine:
    """Offline-only MLX-Audio adapter for Qwen3-TTS CustomVoice."""

    def __init__(
        self,
        models_root: Path | None = None,
        model_loader: Any | None = None,
        *,
        debug: bool = False,
        error_log_path: Path | None = None,
    ) -> None:
        self.models_root = models_root
        self._model_loader = model_loader
        self._debug_enabled = debug
        self._error_log_path = error_log_path
        self._debug_chunk: dict[str, object] = {}
        self._generation_attempt = 0
        self._model: Any | None = None
        self._loaded_reference: Path | None = None

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            modes=("custom_voice",),
            supports_instructions=True,
            supports_streaming=True,
        )

    def validate_profile(self, profile: SynthesisProfile) -> None:
        if profile.engine not in {"mlx-audio", "mlx_audio_qwen3"}:
            raise ValueError("MLX Qwen engine requires engine='mlx-audio'")
        if profile.mode != "custom_voice":
            raise ValueError("the MVP enables only Qwen3-TTS CustomVoice mode")
        if not profile.speaker.strip() or not profile.language.strip():
            raise ValueError("speaker and language are required")

    def load(self, profile: SynthesisProfile) -> LoadedModelInfo:
        self.validate_profile(profile)
        reference = resolve_local_model(profile.model, self.models_root)
        if self._model is None or self._loaded_reference != reference:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            if self._model_loader is None:
                try:
                    module = import_module("mlx_audio.tts.utils")
                    loader = cast(Callable[[Path], Any], module.load_model)
                except (ImportError, AttributeError) as error:
                    raise RuntimeError(
                        "MLX-Audio is not installed; run 'uv sync --extra mlx'"
                    ) from error
            else:
                loader = self._model_loader
            self._model = loader(reference)
            self._loaded_reference = reference
        return LoadedModelInfo(model=str(reference), engine="mlx-audio")

    def synthesize(
        self,
        chunk: Chunk,
        profile: SynthesisProfile,
        destination: Path,
    ) -> AudioArtifact:
        self.load(profile)
        assert self._model is not None
        self._debug_chunk = {
            "chunk_id": chunk.chunk_id,
            "chunk_ordinal": chunk.ordinal,
        }
        instruction = profile.instruction
        if profile.speed != 1.0:
            instruction = f"{instruction} Speak at approximately {profile.speed:.2f}x normal pace."
        try:
            arrays, sample_rate = self._synthesize_text(chunk.text, profile, instruction)
        except SynthesisLimitError:
            if fallback_segments(chunk.text):
                arrays, sample_rate = self._synthesize_with_fallback(
                    chunk.text, profile, instruction
                )
            else:
                arrays, sample_rate = self._synthesize_fallback_part(
                    chunk.text, profile, instruction
                )
        pause = np.zeros(int(chunk.pause_after_ms * sample_rate / 1000), dtype=np.float32)
        audio = np.concatenate((*arrays, pause))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp.wav"
        try:
            sf.write(temporary, audio, sample_rate, format="WAV", subtype="PCM_16")
            info = validate_wav(temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        cache_key = sha256_text(f"{chunk.text_hash}:{profile.profile_hash}")
        return AudioArtifact(
            cache_key=cache_key,
            chunk_id=chunk.chunk_id,
            profile_hash=profile.profile_hash,
            path=destination,
            duration_seconds=info.duration_seconds,
            sample_rate=info.sample_rate,
        )

    def _synthesize_text(
        self,
        text: str,
        profile: SynthesisProfile,
        instruction: str,
        *,
        max_tokens: int | None = None,
        request_kind: str = "primary",
        generation_overrides: dict[str, object] | None = None,
    ) -> tuple[tuple[np.ndarray, ...], int]:
        """Generate one request and reject output that reaches its token limit."""
        assert self._model is not None
        generation = dict(profile.generation)
        if generation_overrides:
            generation.update(generation_overrides)
        if max_tokens is None:
            max_tokens = self._max_tokens(text, generation.get("max_tokens"))
        generation["max_tokens"] = max_tokens
        self._generation_attempt += 1
        attempt = self._generation_attempt
        started_at = time.perf_counter()
        self._diagnostic(
            "generation_start",
            attempt=attempt,
            characters=len(text),
            words=len(text.split()),
            max_tokens=max_tokens,
            request_kind=request_kind,
            repetition_penalty=generation.get("repetition_penalty", 1.05),
            temperature=generation.get("temperature", 0.9),
        )
        generator = self._model.generate_custom_voice(
            text=text,
            speaker=profile.speaker,
            language=profile.language,
            instruct=instruction,
            **generation,
        )
        results = list(generator)
        if not results:
            self._diagnostic(
                "generation_empty",
                persist=True,
                attempt=attempt,
                elapsed_seconds=round(time.perf_counter() - started_at, 3),
                request_kind=request_kind,
            )
            raise RuntimeError("MLX-Audio returned no audio")
        token_counts = [getattr(result, "token_count", None) for result in results]
        if any(isinstance(count, int) and count >= max_tokens for count in token_counts):
            self._diagnostic(
                "generation_limit",
                persist=True,
                attempt=attempt,
                elapsed_seconds=round(time.perf_counter() - started_at, 3),
                max_tokens=max_tokens,
                request_kind=request_kind,
                token_counts=token_counts,
            )
            raise SynthesisLimitError(
                "Qwen3-TTS reached its generation limit before completing the passage"
            )
        arrays = tuple(np.asarray(result.audio, dtype=np.float32).reshape(-1) for result in results)
        sample_rate = int(
            getattr(results[0], "sample_rate", 0)
            or getattr(self._model, "sample_rate", 0)
            or 24_000
        )
        self._diagnostic(
            "generation_complete",
            attempt=attempt,
            audio_samples=sum(array.size for array in arrays),
            elapsed_seconds=round(time.perf_counter() - started_at, 3),
            request_kind=request_kind,
            sample_rate=sample_rate,
            token_counts=token_counts,
        )
        return arrays, sample_rate

    def _synthesize_with_fallback(
        self,
        text: str,
        profile: SynthesisProfile,
        instruction: str,
        max_tokens: int | None = None,
    ) -> tuple[tuple[np.ndarray, ...], int]:
        """Recursively subdivide only a request that reached Qwen's token limit."""
        retry_max_tokens = max_tokens or self._max_tokens(
            text, profile.generation.get("max_tokens")
        )
        parts = fallback_segments(text)
        if not parts:
            self._diagnostic(
                "generation_terminal_limit",
                persist=True,
                characters=len(text),
                max_tokens=retry_max_tokens,
                text_hash=sha256_text(text),
                unicode_categories=sorted({unicodedata.category(character) for character in text}),
                words=len(text.split()),
            )
            raise SynthesisLimitError(
                "Qwen3-TTS reached its generation limit for a natural fallback passage; "
                "inspect the extracted text and retry with a different model profile"
            )
        arrays: list[np.ndarray] = []
        sample_rate: int | None = None
        for index, part in enumerate(parts):
            part_arrays, part_sample_rate = self._synthesize_fallback_part(
                part, profile, instruction, retry_max_tokens
            )
            if sample_rate is None:
                sample_rate = part_sample_rate
            elif part_sample_rate != sample_rate:
                raise RuntimeError("Qwen3-TTS fallback segments returned inconsistent sample rates")
            arrays.extend(part_arrays)
            if index + 1 < len(parts):
                arrays.append(
                    np.zeros(int(_FALLBACK_PAUSE_MS * part_sample_rate / 1000), dtype=np.float32)
                )
        assert sample_rate is not None
        return tuple(arrays), sample_rate

    def _synthesize_fallback_part(
        self,
        text: str,
        profile: SynthesisProfile,
        instruction: str,
        max_tokens: int | None = None,
    ) -> tuple[tuple[np.ndarray, ...], int]:
        """Retry a split passage before splitting it again or reporting a terminal failure."""
        retry_max_tokens = max_tokens or self._max_tokens(
            text, profile.generation.get("max_tokens")
        )
        for attempt_index in range(_FALLBACK_GENERATION_ATTEMPTS):
            try:
                return self._synthesize_text(
                    text,
                    profile,
                    instruction,
                    max_tokens=retry_max_tokens,
                    request_kind="fallback",
                    generation_overrides=self._fallback_generation_overrides(
                        profile, attempt_index
                    ),
                )
            except SynthesisLimitError:
                continue
        return self._synthesize_with_fallback(text, profile, instruction, retry_max_tokens)

    @staticmethod
    def _fallback_generation_overrides(
        profile: SynthesisProfile, attempt_index: int
    ) -> dict[str, object]:
        """Increase anti-repetition pressure only after an ordinary fallback attempt fails."""
        if attempt_index == 0:
            return {}
        configured_temperature = profile.generation.get("temperature", 0.9)
        configured_penalty = profile.generation.get("repetition_penalty", 1.05)
        if not isinstance(configured_temperature, int | float) or isinstance(
            configured_temperature, bool
        ):
            configured_temperature = 0.9
        if not isinstance(configured_penalty, int | float) or isinstance(configured_penalty, bool):
            configured_penalty = 1.05
        temperature_ceiling = 0.8 if attempt_index == 1 else 0.7
        penalty_floor = 1.1 if attempt_index == 1 else 1.2
        return {
            "temperature": min(float(configured_temperature), temperature_ceiling),
            "repetition_penalty": max(float(configured_penalty), penalty_floor),
        }

    @staticmethod
    def _max_tokens(text: str, configured: object) -> int:
        """Use an explicit cap or derive a conservative Qwen3-TTS token budget."""
        if configured is not None and (
            isinstance(configured, bool) or not isinstance(configured, int) or configured <= 0
        ):
            raise ValueError("generation.max_tokens must be a positive integer")
        automatic = default_max_tokens(text)
        if configured is None:
            return automatic
        return configured

    def _diagnostic(self, event: str, *, persist: bool = False, **fields: object) -> None:
        """Emit source-safe diagnostics to stderr and the configured error log when requested."""
        if not self._debug_enabled and not persist:
            return
        payload = {
            "event": f"tts_{event}",
            "timestamp": datetime.now(UTC).isoformat(),
            **self._debug_chunk,
            **fields,
        }
        encoded = json.dumps(payload, sort_keys=True)
        if self._debug_enabled:
            print(encoded, file=sys.stderr, flush=True)
        if self._error_log_path is None:
            return
        try:
            self._error_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._error_log_path.open("a", encoding="utf-8", newline="\n") as log_file:
                log_file.write(f"{encoded}\n")
        except OSError:
            return

    def close(self) -> None:
        self._model = None
        self._loaded_reference = None
