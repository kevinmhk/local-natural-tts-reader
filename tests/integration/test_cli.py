from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from local_tts_reader.cli import app
from local_tts_reader.config import Settings


def test_cli_fake_reader_workflow(tmp_path: Path, fixture_root: Path) -> None:
    config_path = tmp_path / "config.toml"
    Settings(data_dir=tmp_path / "data").write(config_path)
    runner = CliRunner()

    imported = runner.invoke(
        app,
        ["--config", str(config_path), "ingest", str(fixture_root / "text" / "two_paragraphs.txt")],
    )
    assert imported.exit_code == 0, imported.output
    document_id = json.loads(imported.output)["document_id"]

    listed = runner.invoke(app, ["--config", str(config_path), "documents", "list"])
    assert listed.exit_code == 0, listed.output
    library = json.loads(listed.output)
    assert library["count"] == 1
    assert library["documents"] == [
        {
            "cached_audio_count": 0,
            "cached_audio_seconds": 0.0,
            "chunk_count": 1,
            "document_id": document_id,
            "imported_at": library["documents"][0]["imported_at"],
            "media_type": "text/plain",
            "next_ordinal": 0,
            "ready_chunk_count": 0,
            "source_name": "two_paragraphs.txt",
            "state": "ready",
            "title": None,
            "warning_count": 0,
        }
    ]

    preview = runner.invoke(app, ["--config", str(config_path), "preview", document_id, "--stdout"])
    assert preview.exit_code == 0, preview.output
    assert "The first paragraph" in preview.output

    spoken = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "speak",
            document_id,
            "--engine",
            "fake",
            "--model",
            "fake-tone",
            "--no-play",
        ],
    )
    assert spoken.exit_code == 0, spoken.output
    assert json.loads(spoken.output)["state"] == "complete"

    exported = runner.invoke(
        app,
        ["--config", str(config_path), "export", "wav", document_id],
    )
    assert exported.exit_code == 0, exported.output
    export_result = json.loads(exported.output)
    assert export_result["document_id"] == document_id
    assert export_result["chunk_count"] == 1
    assert Path(export_result["path"]).is_file()
    assert Path(export_result["path"]).name == (
        f"two_paragraphs.txt-{document_id}-{export_result['profile_hash'][:12]}.wav"
    )

    status = runner.invoke(app, ["--config", str(config_path), "status", document_id])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["state"] == "complete"

    listed_after_speech = runner.invoke(app, ["--config", str(config_path), "documents", "list"])
    assert listed_after_speech.exit_code == 0, listed_after_speech.output
    entry = json.loads(listed_after_speech.output)["documents"][0]
    assert entry["state"] == "complete"
    assert entry["ready_chunk_count"] == entry["chunk_count"]
    assert entry["cached_audio_count"] == entry["chunk_count"]
    assert entry["cached_audio_seconds"] > 0
