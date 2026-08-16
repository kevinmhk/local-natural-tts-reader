from __future__ import annotations

import json
import platform
import shutil
import sys
from importlib.util import find_spec
from pathlib import Path

import typer
from pydantic import BaseModel

from local_tts_reader.application.pipeline import ReaderApplication
from local_tts_reader.config import Settings
from local_tts_reader.domain.models import SynthesisProfile
from local_tts_reader.ingestion.base import IngestionError
from local_tts_reader.playback.afplay import AfplayBackend
from local_tts_reader.playback.fake import FakePlaybackBackend
from local_tts_reader.tts.fake import FakeTtsEngine
from local_tts_reader.tts.mlx_qwen import (
    MlxQwenTtsEngine,
    ModelUnavailableError,
    installed_model_revision,
    resolve_local_model,
    setup_model,
)

app = typer.Typer(no_args_is_help=True, help="Private local document narration.")
models_app = typer.Typer(no_args_is_help=True, help="Explicit local model management.")
cache_app = typer.Typer(no_args_is_help=True, help="Inspect and prune generated audio.")
app.add_typer(models_app, name="models")
app.add_typer(cache_app, name="cache")

DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit"


def _application() -> ReaderApplication:
    return ReaderApplication(Settings())


def _echo_model(value: object) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


@app.command()
def doctor() -> None:
    """Check the local Apple-silicon runtime without downloading anything."""
    settings = Settings()
    settings.ensure_directories()
    model_available = True
    model_detail: str
    try:
        model_detail = str(resolve_local_model(DEFAULT_MODEL, settings.data_dir / "models"))
    except ModelUnavailableError as error:
        model_available = False
        model_detail = str(error)
    disk = shutil.disk_usage(settings.data_dir)
    checks = {
        "python": platform.python_version(),
        "python_312": sys.version_info[:2] == (3, 12),
        "architecture": platform.machine(),
        "apple_silicon": platform.machine() == "arm64",
        "macos": platform.system() == "Darwin",
        "mlx_audio_installed": find_spec("mlx_audio") is not None,
        "model_available": model_available,
        "model_detail": model_detail,
        "afplay": shutil.which("afplay"),
        "ffmpeg_optional": shutil.which("ffmpeg"),
        "data_dir": str(settings.data_dir),
        "data_dir_writable": settings.data_dir.exists(),
        "disk_free_bytes": disk.free,
        "disk_minimum_met": disk.free >= settings.min_free_bytes,
    }
    _echo_model(checks)
    if (
        not checks["python_312"]
        or not checks["apple_silicon"]
        or not checks["macos"]
        or not checks["afplay"]
        or not checks["disk_minimum_met"]
    ):
        raise typer.Exit(1)


@app.command()
def ingest(path: Path) -> None:
    """Import, clean, and chunk one supported local file."""
    try:
        document = _application().ingest(path)
    except (IngestionError, OSError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    _echo_model(
        {
            "document_id": document.document_id,
            "source_name": document.source_name,
            "title": document.title,
            "warnings": document.warnings,
        }
    )


@app.command()
def preview(document_id: str, stdout: bool = typer.Option(False, "--stdout")) -> None:
    """Show extraction metadata and optionally exact spoken text."""
    try:
        document, path, text = _application().preview(document_id)
    except KeyError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    _echo_model(
        {
            "document_id": document.document_id,
            "title": document.title,
            "warnings": document.warnings,
            "preview_path": str(path),
        }
    )
    if stdout:
        typer.echo("\n" + text)


@app.command()
def status(document_id: str) -> None:
    """Show persistent preparation, cache, and playback progress."""
    try:
        _echo_model(_application().status(document_id))
    except KeyError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error


def _profile(
    engine: str,
    model: str,
    model_revision: str | None,
    voice: str,
    language: str,
    instruction: str,
    speed: float,
) -> SynthesisProfile:
    return SynthesisProfile(
        engine=engine,
        model=model,
        model_revision=model_revision,
        speaker=voice,
        language=language,
        instruction=instruction,
        speed=speed,
    )


@app.command()
def speak(
    document_id: str,
    engine: str = typer.Option("mlx-audio", "--engine"),
    model: str = typer.Option(DEFAULT_MODEL, "--model"),
    model_revision: str | None = typer.Option(None, "--model-revision"),
    voice: str = typer.Option("Aiden", "--voice"),
    language: str = typer.Option("English", "--language"),
    instruction: str = typer.Option(
        "Calm, clear long-form narration with restrained expression and natural pauses.",
        "--instruction",
    ),
    speed: float = typer.Option(1.0, "--speed", min=0.5, max=2.0),
    no_play: bool = typer.Option(False, "--no-play"),
) -> None:
    """Generate ahead, play in order, and persist confirmed progress."""
    application = _application()
    try:
        if engine != "fake" and model_revision is None:
            model_path = resolve_local_model(model, application.settings.data_dir / "models")
            model_revision = installed_model_revision(model_path)
        profile = _profile(
            engine,
            model,
            model_revision,
            voice,
            language,
            instruction,
            speed,
        )
        tts = (
            FakeTtsEngine()
            if engine == "fake"
            else MlxQwenTtsEngine(application.settings.data_dir / "models")
        )
        playback = FakePlaybackBackend() if no_play else AfplayBackend()
        result = application.speak(document_id, profile, tts, playback)
    except (KeyError, ModelUnavailableError, OSError, RuntimeError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    _echo_model(result)


@app.command()
def pause(document_id: str) -> None:
    """Request a cooperative pause without skipping the current chunk."""
    count = _application().request_pause(document_id)
    _echo_model({"document_id": document_id, "sessions_paused": count})


@app.command()
def resume(document_id: str, no_play: bool = typer.Option(False, "--no-play")) -> None:
    """Resume the latest profile from the last unconfirmed chunk."""
    application = _application()
    try:
        profile = application.repository.latest_profile(document_id)
        tts = (
            FakeTtsEngine()
            if profile.engine == "fake"
            else MlxQwenTtsEngine(application.settings.data_dir / "models")
        )
        playback = FakePlaybackBackend() if no_play else AfplayBackend()
        result = application.resume(document_id, tts, playback)
    except (KeyError, ModelUnavailableError, OSError, RuntimeError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    _echo_model(result)


@models_app.command("setup")
def models_setup(
    model: str = typer.Option(DEFAULT_MODEL, "--model"),
    revision: str | None = typer.Option(None, "--revision"),
) -> None:
    """Explicitly download Qwen weights for later offline use."""
    settings = Settings()
    destination = setup_model(model, settings.data_dir / "models", revision)
    _echo_model({"model": model, "path": str(destination), "downloaded": True})


@models_app.command("verify")
def models_verify(model: str = typer.Option(DEFAULT_MODEL, "--model")) -> None:
    """Verify that model weights are available without network access."""
    settings = Settings()
    try:
        resolved = resolve_local_model(model, settings.data_dir / "models")
    except ModelUnavailableError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    _echo_model({"model": model, "local_path": str(resolved), "available": True})


@cache_app.command("inspect")
def cache_inspect() -> None:
    """Report audio cache file count and bytes."""
    _echo_model(_application().cache_inspect())


@cache_app.command("prune")
def cache_prune(dry_run: bool = typer.Option(True, "--dry-run/--delete")) -> None:
    """Report unreferenced audio; delete only with --delete."""
    _echo_model(_application().cache_prune(dry_run=dry_run))


def main() -> None:
    """Run the Typer command-line application."""
    app()


if __name__ == "__main__":
    main()
