from __future__ import annotations

import shutil
import subprocess
import time

from local_tts_reader.domain.models import AudioArtifact
from local_tts_reader.playback.base import PlaybackResult, StopToken


class AfplayBackend:
    """macOS playback through an owned afplay child process."""

    def __init__(self) -> None:
        executable = shutil.which("afplay")
        if executable is None:
            raise RuntimeError("macOS afplay is unavailable")
        self.executable = executable
        self._process: subprocess.Popen[bytes] | None = None

    def play(self, artifact: AudioArtifact, stop: StopToken) -> PlaybackResult:
        self._process = subprocess.Popen(
            [self.executable, str(artifact.path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            while self._process.poll() is None:
                if stop.stopped:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                    return PlaybackResult(completed=False, return_code=-1)
                time.sleep(0.05)
            return_code = self._process.returncode or 0
            return PlaybackResult(completed=return_code == 0, return_code=return_code)
        finally:
            self._process = None

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
