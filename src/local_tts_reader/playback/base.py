from __future__ import annotations

import threading
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from local_tts_reader.domain.models import AudioArtifact


class StopToken:
    """Thread-safe cooperative playback cancellation."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def stop(self) -> None:
        self._event.set()

    @property
    def stopped(self) -> bool:
        return self._event.is_set()


class PlaybackResult(BaseModel):
    """Outcome of one chunk playback."""

    model_config = ConfigDict(frozen=True)
    completed: bool
    return_code: int


class PlaybackBackend(Protocol):
    """Local audio playback port."""

    def play(self, artifact: AudioArtifact, stop: StopToken) -> PlaybackResult: ...

    def stop(self) -> None: ...
