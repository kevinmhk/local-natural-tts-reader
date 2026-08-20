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

    status = runner.invoke(app, ["--config", str(config_path), "status", document_id])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["state"] == "complete"
