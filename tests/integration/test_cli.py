from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_tts_reader.cli import app


def test_cli_fake_reader_workflow(
    tmp_path: Path, fixture_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCAL_TTS_READER_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()

    imported = runner.invoke(app, ["ingest", str(fixture_root / "text" / "two_paragraphs.txt")])
    assert imported.exit_code == 0, imported.output
    document_id = json.loads(imported.output)["document_id"]

    preview = runner.invoke(app, ["preview", document_id, "--stdout"])
    assert preview.exit_code == 0, preview.output
    assert "The first paragraph" in preview.output

    spoken = runner.invoke(
        app,
        ["speak", document_id, "--engine", "fake", "--model", "fake-tone", "--no-play"],
    )
    assert spoken.exit_code == 0, spoken.output
    assert json.loads(spoken.output)["state"] == "complete"

    status = runner.invoke(app, ["status", document_id])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["state"] == "complete"
