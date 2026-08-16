from __future__ import annotations

from pathlib import Path

from local_tts_reader.domain.models import Chunk, SynthesisProfile
from local_tts_reader.storage.artifacts import validate_wav
from local_tts_reader.tts.fake import FakeTtsEngine


def test_fake_tts_writes_a_valid_deterministic_wav(tmp_path: Path) -> None:
    engine = FakeTtsEngine()
    chunk = Chunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        ordinal=0,
        text="A short local sentence.",
        text_hash="hash",
        boundary="paragraph",
        pause_after_ms=350,
    )
    profile = SynthesisProfile(engine="fake", model="fake-tone")

    artifact = engine.synthesize(chunk, profile, tmp_path / "audio.wav")

    info = validate_wav(artifact.path)
    assert info.duration_seconds > 0
    assert info.sample_rate == 24_000
