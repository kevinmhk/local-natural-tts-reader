from __future__ import annotations

import os
from pathlib import Path

import pytest

from local_tts_reader.domain.models import Chunk, SynthesisProfile
from local_tts_reader.storage.artifacts import validate_wav
from local_tts_reader.tts.mlx_qwen import MlxQwenTtsEngine


@pytest.mark.hardware
@pytest.mark.skipif(
    not os.getenv("LOCAL_TTS_READER_MODEL_PATH"),
    reason="LOCAL_TTS_READER_MODEL_PATH is not set; hardware test never downloads",
)
def test_installed_qwen_model_generates_valid_wav(tmp_path: Path) -> None:
    model_path = os.environ["LOCAL_TTS_READER_MODEL_PATH"]
    engine = MlxQwenTtsEngine()
    profile = SynthesisProfile(
        engine="mlx-audio",
        model=model_path,
        speaker="Aiden",
        language="English",
        instruction="Calm, clear narration.",
        generation={"max_tokens": 512, "temperature": 0.8},
    )
    chunk = Chunk(
        chunk_id="hardware-chunk",
        document_id="hardware-document",
        ordinal=0,
        text="The local reader is ready.",
        text_hash="hardware-text",
        boundary="paragraph",
        pause_after_ms=100,
    )

    artifact = engine.synthesize(chunk, profile, tmp_path / "qwen.wav")

    info = validate_wav(artifact.path)
    assert info.duration_seconds > 0.2
    assert info.sample_rate > 0
    engine.close()
