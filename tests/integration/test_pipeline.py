from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from local_tts_reader.application.pipeline import ReaderApplication
from local_tts_reader.config import Settings
from local_tts_reader.domain.models import AudioArtifact, SpeakResult, SynthesisProfile
from local_tts_reader.playback.base import PlaybackResult, StopToken
from local_tts_reader.playback.fake import FakePlaybackBackend
from local_tts_reader.tts.fake import FakeTtsEngine


def test_pipeline_reimports_and_reuses_cached_audio(tmp_path: Path, fixture_root: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    app = ReaderApplication(settings)
    source = fixture_root / "text" / "two_paragraphs.txt"

    first_document = app.ingest(source)
    second_document = app.ingest(source)
    assert second_document.document_id == first_document.document_id

    profile = SynthesisProfile(engine="fake", model="fake-tone")
    first = app.speak(
        first_document.document_id,
        profile,
        FakeTtsEngine(),
        FakePlaybackBackend(),
    )
    second = app.speak(
        first_document.document_id,
        profile,
        FakeTtsEngine(),
        FakePlaybackBackend(),
    )

    assert first.generated_count > 0
    assert second.generated_count == 0
    assert second.cache_hit_count == len(app.get_chunks(first_document.document_id))
    assert app.status(first_document.document_id).state == "complete"


def test_documents_list_includes_import_and_audio_metadata(
    tmp_path: Path, fixture_root: Path
) -> None:
    app = ReaderApplication(Settings(data_dir=tmp_path / "data"))
    document = app.ingest(fixture_root / "text" / "two_paragraphs.txt")

    before_speech = app.list_documents()

    assert len(before_speech) == 1
    assert before_speech[0].document_id == document.document_id
    assert before_speech[0].source_name == "two_paragraphs.txt"
    assert before_speech[0].media_type == "text/plain"
    assert before_speech[0].imported_at
    assert before_speech[0].state == "ready"
    assert before_speech[0].chunk_count == len(app.get_chunks(document.document_id))
    assert before_speech[0].ready_chunk_count == 0
    assert before_speech[0].cached_audio_count == 0
    assert before_speech[0].cached_audio_seconds == 0
    assert before_speech[0].warning_count == 0

    app.speak(
        document.document_id,
        SynthesisProfile(engine="fake", model="fake-tone"),
        FakeTtsEngine(),
        FakePlaybackBackend(),
    )

    after_speech = app.list_documents()[0]
    assert after_speech.state == "complete"
    assert after_speech.ready_chunk_count == after_speech.chunk_count
    assert after_speech.cached_audio_count == after_speech.chunk_count
    assert after_speech.cached_audio_seconds > 0


def test_export_wav_concatenates_cached_chunks_in_order(tmp_path: Path) -> None:
    source = tmp_path / "chapters.txt"
    source.write_text(
        "First passage introduces the topic.\n\n"
        "Second passage continues the explanation.\n\n"
        "Third passage closes the chapter.\n"
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        chunk_target_chars=20,
        chunk_hard_limit_chars=40,
        min_free_bytes=0,
    )
    app = ReaderApplication(settings)
    document = app.ingest(source)
    profile = SynthesisProfile(engine="fake", model="fake-tone")
    app.speak(document.document_id, profile, FakeTtsEngine(), FakePlaybackBackend())

    result = app.export_wav(document.document_id)
    artifacts = app.repository.list_audio_for_document(document.document_id, profile.profile_hash)
    expected = np.concatenate(
        [sf.read(artifact.path, dtype="int16", always_2d=True)[0] for artifact in artifacts]
    )
    actual, sample_rate = sf.read(result.path, dtype="int16", always_2d=True)

    assert len(artifacts) == len(app.get_chunks(document.document_id))
    assert result.chunk_count == len(artifacts)
    assert result.duration_seconds > 0
    assert result.path.is_file()
    assert (
        result.path.name == f"chapters.txt-{document.document_id}-{profile.profile_hash[:12]}.wav"
    )
    assert sample_rate == result.sample_rate
    np.testing.assert_array_equal(actual, expected)


def test_profile_change_invalidates_audio_only(tmp_path: Path, fixture_root: Path) -> None:
    app = ReaderApplication(Settings(data_dir=tmp_path / "data"))
    document = app.ingest(fixture_root / "text" / "two_paragraphs.txt")
    first = SynthesisProfile(engine="fake", model="fake-tone", speaker="Aiden")
    second = first.model_copy(update={"speaker": "Ryan"})

    app.speak(document.document_id, first, FakeTtsEngine(), FakePlaybackBackend())
    changed = app.speak(document.document_id, second, FakeTtsEngine(), FakePlaybackBackend())

    assert changed.generated_count == len(app.get_chunks(document.document_id))
    assert changed.cache_hit_count == 0


def test_corrupt_cached_audio_is_regenerated(tmp_path: Path, fixture_root: Path) -> None:
    app = ReaderApplication(Settings(data_dir=tmp_path / "data"))
    document = app.ingest(fixture_root / "text" / "two_paragraphs.txt")
    profile = SynthesisProfile(engine="fake", model="fake-tone")
    app.speak(document.document_id, profile, FakeTtsEngine(), FakePlaybackBackend())
    cached_paths = sorted(app.repository.referenced_audio_paths())
    assert cached_paths
    cached_paths[0].write_bytes(b"not a WAV")

    replay = app.speak(
        document.document_id,
        profile,
        FakeTtsEngine(),
        FakePlaybackBackend(),
    )

    assert replay.generated_count == 1
    assert replay.cache_hit_count == len(app.get_chunks(document.document_id)) - 1


class _BlockingPlayback:
    def __init__(self) -> None:
        self.started = threading.Event()

    def play(self, artifact: AudioArtifact, stop: StopToken) -> PlaybackResult:
        del artifact
        self.started.set()
        while not stop.stopped:
            time.sleep(0.01)
        return PlaybackResult(completed=False, return_code=-1)

    def stop(self) -> None:
        return None


def test_pause_then_resume_does_not_skip_a_chunk(tmp_path: Path) -> None:
    source = tmp_path / "long.txt"
    source.write_text("First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n")
    settings = Settings(
        data_dir=tmp_path / "data",
        chunk_target_chars=20,
        chunk_hard_limit_chars=40,
        min_free_bytes=0,
    )
    app = ReaderApplication(settings)
    document = app.ingest(source)
    profile = SynthesisProfile(engine="fake", model="fake-tone")
    blocking = _BlockingPlayback()
    result_holder: list[SpeakResult] = []

    thread = threading.Thread(
        target=lambda: result_holder.append(
            app.speak(document.document_id, profile, FakeTtsEngine(), blocking)
        )
    )
    thread.start()
    assert blocking.started.wait(timeout=3)
    assert app.request_pause(document.document_id) == 1
    thread.join(timeout=5)

    assert not thread.is_alive()
    paused = result_holder[0]
    assert paused.state == "paused"
    assert paused.next_ordinal == 0

    resumed = app.resume(document.document_id, FakeTtsEngine(), FakePlaybackBackend())
    assert resumed.state == "complete"
    assert resumed.next_ordinal == len(app.get_chunks(document.document_id))
