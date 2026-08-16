from __future__ import annotations

import math
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from local_tts_reader.domain.models import (
    AudioArtifact,
    Chunk,
    EngineCapabilities,
    LoadedModelInfo,
    SynthesisProfile,
    sha256_text,
)
from local_tts_reader.storage.artifacts import validate_wav


class FakeTtsEngine:
    """Fast deterministic tone generator for tests and offline diagnostics."""

    sample_rate = 24_000

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            modes=("custom_voice",),
            supports_instructions=True,
            supports_streaming=False,
        )

    def validate_profile(self, profile: SynthesisProfile) -> None:
        if profile.engine != "fake":
            raise ValueError("fake engine requires engine='fake'")

    def load(self, profile: SynthesisProfile) -> LoadedModelInfo:
        self.validate_profile(profile)
        return LoadedModelInfo(model=profile.model, engine=profile.engine)

    def synthesize(
        self,
        chunk: Chunk,
        profile: SynthesisProfile,
        destination: Path,
    ) -> AudioArtifact:
        self.load(profile)
        duration = min(0.75, max(0.12, len(chunk.text) / 300))
        samples = int(duration * self.sample_rate)
        time = np.arange(samples, dtype=np.float32) / self.sample_rate
        audio = (0.03 * np.sin(2 * math.pi * 220 * time)).astype(np.float32)
        pause = np.zeros(int(chunk.pause_after_ms * self.sample_rate / 1000), dtype=np.float32)
        audio = np.concatenate((audio, pause))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp.wav"
        try:
            sf.write(temporary, audio, self.sample_rate, format="WAV", subtype="PCM_16")
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
        return None
