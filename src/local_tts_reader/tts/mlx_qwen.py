from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import numpy as np
import soundfile as sf
from huggingface_hub import snapshot_download

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


def default_max_tokens(text: str) -> int:
    """Return a conservative Qwen3-TTS token budget for one short text chunk."""
    word_count = len(text.split())
    return min(1_024, max(256, 64 + (word_count * 8)))


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

    def __init__(self, models_root: Path | None = None, model_loader: Any | None = None) -> None:
        self.models_root = models_root
        self._model_loader = model_loader
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
        instruction = profile.instruction
        if profile.speed != 1.0:
            instruction = f"{instruction} Speak at approximately {profile.speed:.2f}x normal pace."
        generation = dict(profile.generation)
        max_tokens = generation.setdefault("max_tokens", default_max_tokens(chunk.text))
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError("generation.max_tokens must be a positive integer")
        generator = self._model.generate_custom_voice(
            text=chunk.text,
            speaker=profile.speaker,
            language=profile.language,
            instruct=instruction,
            **generation,
        )
        results = list(generator)
        if not results:
            raise RuntimeError("MLX-Audio returned no audio")
        if any(
            isinstance(getattr(result, "token_count", None), int)
            and result.token_count >= max_tokens
            for result in results
        ):
            raise SynthesisLimitError(
                "Qwen3-TTS reached its generation limit before completing the passage; "
                "use shorter chunks and retry"
            )
        arrays = [np.asarray(result.audio, dtype=np.float32).reshape(-1) for result in results]
        sample_rate = int(
            getattr(results[0], "sample_rate", 0)
            or getattr(self._model, "sample_rate", 0)
            or 24_000
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

    def close(self) -> None:
        self._model = None
        self._loaded_reference = None
