from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from local_tts_reader.domain.models import Chunk, SynthesisProfile
from local_tts_reader.storage.artifacts import validate_wav
from local_tts_reader.tts import mlx_qwen
from local_tts_reader.tts.mlx_qwen import (
    MlxQwenTtsEngine,
    ModelUnavailableError,
    SynthesisLimitError,
    fallback_segments,
    installed_model_revision,
)


@dataclass
class _Result:
    audio: np.ndarray
    sample_rate: int = 24_000
    token_count: int = 32


class _Model:
    sample_rate = 24_000

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_custom_voice(self, **kwargs: object) -> list[_Result]:
        self.calls.append(kwargs)
        return [_Result(np.full(2_400, 0.01, dtype=np.float32))]


class _LimitModel(_Model):
    def generate_custom_voice(self, **kwargs: object) -> list[_Result]:
        self.calls.append(kwargs)
        return [_Result(np.full(2_400, 0.01, dtype=np.float32), token_count=256)]


class _FallbackModel(_Model):
    def generate_custom_voice(self, **kwargs: object) -> list[_Result]:
        self.calls.append(kwargs)
        text = kwargs["text"]
        assert isinstance(text, str)
        if text == "First sentence. Second sentence.":
            return [_Result(np.full(2_400, 0.01, dtype=np.float32), token_count=256)]
        return [_Result(np.full(2_400, 0.01, dtype=np.float32))]


class _TransientFallbackModel(_Model):
    def __init__(self) -> None:
        super().__init__()
        self.remaining_failures = {
            "First sentence. Second sentence.": 1,
            "First sentence.": 1,
        }

    def generate_custom_voice(self, **kwargs: object) -> list[_Result]:
        self.calls.append(kwargs)
        text = kwargs["text"]
        max_tokens = kwargs["max_tokens"]
        assert isinstance(text, str)
        assert isinstance(max_tokens, int)
        if self.remaining_failures.get(text, 0):
            self.remaining_failures[text] -= 1
            return [_Result(np.full(2_400, 0.01, dtype=np.float32), token_count=max_tokens)]
        return [_Result(np.full(2_400, 0.01, dtype=np.float32))]


def test_mlx_adapter_dispatches_custom_voice_and_promotes_wav(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_bytes(b"test-weights")
    model = _Model()
    loaded: list[Path] = []
    engine = MlxQwenTtsEngine(model_loader=lambda path: (loaded.append(path), model)[1])
    profile = SynthesisProfile(
        engine="mlx-audio",
        model=str(model_dir),
        speaker="Aiden",
        language="English",
        instruction="Calm narration.",
    )
    chunk = Chunk(
        chunk_id="chunk",
        document_id="document",
        ordinal=0,
        text="A local sentence.",
        text_hash="text-hash",
        boundary="paragraph",
        pause_after_ms=100,
    )

    artifact = engine.synthesize(chunk, profile, tmp_path / "audio.wav")

    assert loaded == [model_dir]
    assert model.calls == [
        {
            "text": "A local sentence.",
            "speaker": "Aiden",
            "language": "English",
            "instruct": "Calm narration.",
            "max_tokens": 256,
        }
    ]
    assert validate_wav(artifact.path).duration_seconds >= 0.19


def test_mlx_adapter_writes_safe_debug_diagnostics_to_error_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_bytes(b"test-weights")
    error_log = tmp_path / "error.log"
    engine = MlxQwenTtsEngine(model_loader=lambda _: _Model(), debug=True, error_log_path=error_log)
    profile = SynthesisProfile(engine="mlx-audio", model=str(model_dir))
    chunk = Chunk(
        chunk_id="chunk",
        document_id="document",
        ordinal=7,
        text="A local sentence.",
        text_hash="text-hash",
        boundary="paragraph",
        pause_after_ms=100,
    )

    engine.synthesize(chunk, profile, tmp_path / "audio.wav")

    stderr = capsys.readouterr().err
    logged = error_log.read_text(encoding="utf-8")
    assert stderr == logged
    assert '"event": "tts_generation_start"' in logged
    assert '"chunk_ordinal": 7' in logged
    assert "A local sentence." not in logged


def test_mlx_adapter_rejects_generation_that_hits_its_token_limit(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_bytes(b"test-weights")
    model = _LimitModel()
    engine = MlxQwenTtsEngine(model_loader=lambda _: model)
    profile = SynthesisProfile(engine="mlx-audio", model=str(model_dir))
    chunk = Chunk(
        chunk_id="chunk",
        document_id="document",
        ordinal=0,
        text="A local sentence.",
        text_hash="text-hash",
        boundary="paragraph",
        pause_after_ms=100,
    )

    with pytest.raises(SynthesisLimitError, match="generation limit"):
        engine.synthesize(chunk, profile, tmp_path / "audio.wav")

    assert not (tmp_path / "audio.wav").exists()


def test_mlx_adapter_recovers_with_smaller_sentence_requests(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_bytes(b"test-weights")
    model = _FallbackModel()
    engine = MlxQwenTtsEngine(model_loader=lambda _: model)
    profile = SynthesisProfile(engine="mlx-audio", model=str(model_dir))
    chunk = Chunk(
        chunk_id="chunk",
        document_id="document",
        ordinal=0,
        text="First sentence. Second sentence.",
        text_hash="text-hash",
        boundary="paragraph",
        pause_after_ms=100,
    )

    artifact = engine.synthesize(chunk, profile, tmp_path / "audio.wav")

    assert fallback_segments(chunk.text) == ("First sentence.", "Second sentence.")
    assert [call["text"] for call in model.calls] == [
        "First sentence. Second sentence.",
        "First sentence.",
        "Second sentence.",
    ]
    assert [call["max_tokens"] for call in model.calls] == [256, 256, 256]
    assert validate_wav(artifact.path).duration_seconds >= 0.42


def test_fallback_segments_preserve_quoted_words_and_acronyms() -> None:
    text = (
        "GPT-2 (2019) ~ preschooler: “Wow, it can string together a few plausible sentences.” "
        "A very-cherry-picked example of a semicoherent story about unicorns in the Andes it "
        "generated was incredibly impressive at the time."
    )

    parts = fallback_segments(text)

    assert len(parts) > 1
    assert "“Wow," not in parts
    assert any("GPT-2" in part and "“Wow," in part for part in parts)
    assert all(len(part) > 20 for part in parts)
    assert fallback_segments("a") == ()


def test_fallback_segments_pack_whole_clauses_without_mid_thought_cutoffs() -> None:
    text = (
        "GPT-4 (2023) ~ smart high schooler: “Wow, it can write pretty good essays, "
        "solve hard math problems, and preserve a long quoted thought, even when the complete "
        "sentence is deliberately long enough to require clause fallback.”"
    )

    parts = fallback_segments(text)

    assert len(parts) > 1
    assert all(not part.endswith("pretty") for part in parts)
    assert all(part[-1] in ":,.”" for part in parts)


def test_mlx_adapter_retries_a_token_limited_fallback_segment(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_bytes(b"test-weights")
    model = _TransientFallbackModel()
    engine = MlxQwenTtsEngine(model_loader=lambda _: model)
    profile = SynthesisProfile(engine="mlx-audio", model=str(model_dir))
    chunk = Chunk(
        chunk_id="chunk",
        document_id="document",
        ordinal=0,
        text="First sentence. Second sentence.",
        text_hash="text-hash",
        boundary="paragraph",
        pause_after_ms=100,
    )

    engine.synthesize(chunk, profile, tmp_path / "audio.wav")

    assert [call["text"] for call in model.calls] == [
        "First sentence. Second sentence.",
        "First sentence.",
        "First sentence.",
        "Second sentence.",
    ]
    assert "temperature" not in model.calls[1]
    assert model.calls[2]["temperature"] == 0.8
    assert model.calls[2]["repetition_penalty"] == 1.1


def test_missing_model_resolution_is_offline_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []

    def unavailable(*, repo_id: str, local_files_only: bool = False, **_: object) -> str:
        del repo_id
        calls.append(local_files_only)
        raise OSError("not cached")

    monkeypatch.setattr(mlx_qwen, "snapshot_download", unavailable)
    incomplete = mlx_qwen.model_directory(tmp_path / "models", "example/missing")
    incomplete.mkdir(parents=True)
    (incomplete / "config.json").write_text("{}")
    (incomplete / "weights.incomplete").write_text("partial")

    with pytest.raises(ModelUnavailableError, match="models setup"):
        mlx_qwen.resolve_local_model("example/missing", tmp_path / "models")

    assert calls == [True]


def test_installed_revision_prefers_hugging_face_commit(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    tree_dir = model_dir / ".cache" / "huggingface" / "trees"
    tree_dir.mkdir(parents=True)
    commit = "1c6c0ff58c43afa8df571facde2efa077efd85e2"
    (tree_dir / f"{commit}.json").write_text("{}", encoding="utf-8")

    assert installed_model_revision(model_dir) == commit
