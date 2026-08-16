from __future__ import annotations

import threading
import time

from local_tts_reader.domain.models import AudioArtifact
from local_tts_reader.playback.base import PlaybackBackend, PlaybackResult, StopToken
from local_tts_reader.storage.repositories import Repository


class PlaybackService:
    """Play one artifact while observing cross-process pause requests."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def play(
        self,
        document_id: str,
        profile_hash: str,
        artifact: AudioArtifact,
        backend: PlaybackBackend,
    ) -> PlaybackResult:
        stop = StopToken()
        finished = threading.Event()

        def watch_pause() -> None:
            while not finished.wait(0.1):
                if self.repository.pause_requested(document_id, profile_hash):
                    stop.stop()
                    return

        watcher = threading.Thread(target=watch_pause, daemon=True)
        watcher.start()
        try:
            return backend.play(artifact, stop)
        finally:
            finished.set()
            watcher.join(timeout=1)
            time.sleep(0)
