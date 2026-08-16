from __future__ import annotations

from local_tts_reader.domain.models import AudioArtifact
from local_tts_reader.playback.base import PlaybackResult, StopToken


class FakePlaybackBackend:
    """Non-audible playback recorder for tests and --no-play mode."""

    def __init__(self) -> None:
        self.played: list[AudioArtifact] = []

    def play(self, artifact: AudioArtifact, stop: StopToken) -> PlaybackResult:
        if stop.stopped:
            return PlaybackResult(completed=False, return_code=-1)
        self.played.append(artifact)
        return PlaybackResult(completed=True, return_code=0)

    def stop(self) -> None:
        return None
