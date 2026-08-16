from __future__ import annotations

from pathlib import Path
from typing import Protocol

from local_tts_reader.domain.models import (
    AudioArtifact,
    Chunk,
    EngineCapabilities,
    LoadedModelInfo,
    SynthesisProfile,
)


class TtsEngine(Protocol):
    """Speech engine port used by application services."""

    def capabilities(self) -> EngineCapabilities: ...

    def validate_profile(self, profile: SynthesisProfile) -> None: ...

    def load(self, profile: SynthesisProfile) -> LoadedModelInfo: ...

    def synthesize(
        self,
        chunk: Chunk,
        profile: SynthesisProfile,
        destination: Path,
    ) -> AudioArtifact: ...

    def close(self) -> None: ...
