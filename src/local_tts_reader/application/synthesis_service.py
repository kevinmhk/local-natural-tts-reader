from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from local_tts_reader.domain.models import AudioArtifact, Chunk, SynthesisProfile
from local_tts_reader.storage.artifacts import (
    NarrationSilenceError,
    audio_path,
    validate_narration_wav,
)
from local_tts_reader.storage.repositories import Repository
from local_tts_reader.tts.base import TtsEngine

_MAX_NARRATION_ATTEMPTS = 3


class SynthesisService:
    """Resolve cached audio or generate one atomic chunk artifact."""

    def __init__(self, repository: Repository, audio_root: Path, min_free_bytes: int) -> None:
        self.repository = repository
        self.audio_root = audio_root
        self.min_free_bytes = min_free_bytes

    @staticmethod
    def cache_key(chunk: Chunk, profile: SynthesisProfile) -> str:
        """Hash every input known to affect one chunk's speech."""
        return hashlib.sha256(f"{chunk.text_hash}:{profile.profile_hash}".encode()).hexdigest()

    def ensure(
        self,
        chunk: Chunk,
        profile: SynthesisProfile,
        engine: TtsEngine,
    ) -> tuple[AudioArtifact, bool]:
        """Return a valid cached artifact or generate and persist it."""
        key = self.cache_key(chunk, profile)
        cached = self.repository.get_audio(key)
        if cached is not None:
            try:
                validate_narration_wav(cached.path)
            except (OSError, ValueError, RuntimeError):
                pass
            else:
                self.repository.mark_chunk_ready(chunk.chunk_id)
                return cached, True
        free_bytes = shutil.disk_usage(self.audio_root).free
        if free_bytes < self.min_free_bytes:
            raise OSError(
                f"insufficient free disk space: {free_bytes} bytes available; "
                f"{self.min_free_bytes} required"
            )
        destination = audio_path(self.audio_root, key)
        candidate = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        for attempt in range(1, _MAX_NARRATION_ATTEMPTS + 1):
            try:
                artifact = engine.synthesize(chunk, profile, candidate)
                validate_narration_wav(artifact.path)
                artifact.path.replace(destination)
            except NarrationSilenceError as error:
                if attempt == _MAX_NARRATION_ATTEMPTS:
                    raise NarrationSilenceError(
                        f"chunk {chunk.ordinal + 1} (ordinal {chunk.ordinal}) failed "
                        f"after {attempt} attempts: {error}"
                    ) from error
            else:
                artifact = artifact.model_copy(update={"cache_key": key, "path": destination})
                self.repository.save_audio(artifact)
                self.repository.mark_chunk_ready(chunk.chunk_id)
                return artifact, False
            finally:
                candidate.unlink(missing_ok=True)
        raise AssertionError("at least one narration attempt is required")
