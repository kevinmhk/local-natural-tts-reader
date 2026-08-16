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
    installed_model_revision,
)


@dataclass
class _Result:
    audio: np.ndarray
    sample_rate: int = 24_000


class _Model:
    sample_rate = 24_000

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_custom_voice(self, **kwargs: object) -> list[_Result]:
        self.calls.append(kwargs)
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
        }
    ]
    assert validate_wav(artifact.path).duration_seconds >= 0.19


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
