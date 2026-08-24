from __future__ import annotations

import json
import platform
import shutil
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from local_tts_reader.application.pipeline import ReaderApplication
from local_tts_reader.config import ConfigurationError, Settings
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
documents_app = typer.Typer(no_args_is_help=True, help="Find imported local documents.")
export_app = typer.Typer(no_args_is_help=True, help="Create completed local audio exports.")
app.add_typer(models_app, name="models")
app.add_typer(cache_app, name="cache")
app.add_typer(documents_app, name="documents")
app.add_typer(export_app, name="export")


def _settings(ctx: typer.Context) -> Settings:
    settings = ctx.obj
    if not isinstance(settings, Settings):
        raise RuntimeError("reader settings were not initialized")
    return settings


def _application(ctx: typer.Context) -> ReaderApplication:
    return ReaderApplication(_settings(ctx))


def _echo_model(value: object) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


def _interactive_stdin() -> bool:
    """Return whether destructive confirmation can safely prompt the user."""
    return sys.stdin.isatty()


@app.callback()
def configure(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="Path to a TOML configuration file (created with defaults when absent).",
        ),
    ] = None,
) -> None:
    """Load the user configuration before executing a reader command."""
    try:
        ctx.obj = Settings.load(config)
    except ConfigurationError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check the local Apple-silicon runtime without downloading anything."""
    settings = _settings(ctx)
    settings.ensure_directories()
    model_available = True
    model_detail: str
    try:
        model_detail = str(
            resolve_local_model(settings.default_model, settings.data_dir / "models")
        )
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
        "config_path": str(settings.config_path) if settings.config_path else None,
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
def ingest(ctx: typer.Context, path: Path) -> None:
    """Import, clean, and chunk one supported local file."""
    try:
        document = _application(ctx).ingest(path)
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
def preview(
    ctx: typer.Context, document_id: str, stdout: bool = typer.Option(False, "--stdout")
) -> None:
    """Show extraction metadata and optionally exact spoken text."""
    try:
        document, path, text = _application(ctx).preview(document_id)
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
def status(ctx: typer.Context, document_id: str) -> None:
    """Show persistent preparation, cache, and playback progress."""
    try:
        _echo_model(_application(ctx).status(document_id))
    except KeyError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error


@documents_app.command("list")
def documents_list(ctx: typer.Context) -> None:
    """List imported documents with playback and cached-audio metadata."""
    entries = _application(ctx).list_documents()
    _echo_model(
        {
            "count": len(entries),
            "documents": [entry.model_dump(mode="json") for entry in entries],
        }
    )


@documents_app.command("delete")
def documents_delete(
    ctx: typer.Context,
    document_id: str,
    dry_run: bool = typer.Option(False, "--dry-run"),
    keep_exports: bool = typer.Option(False, "--keep-exports"),
) -> None:
    """Interactively delete one document and its reader-managed artifacts."""
    application = _application(ctx)
    try:
        preview = application.document_delete_preview(document_id, keep_exports=keep_exports)
        if dry_run:
            _echo_model(preview)
            return
        if not _interactive_stdin():
            raise RuntimeError(
                "destructive deletion requires an interactive terminal; "
                "use 'reader documents delete DOCUMENT_ID --dry-run' to preview"
            )
        typer.echo(
            f"Document: {preview['source_name']} ({document_id})\n"
            "Permanently delete this document and its reader-managed artifacts?\n"
            f"Preview first with: reader documents delete {document_id} --dry-run"
        )
        if not typer.confirm("Continue", default=False):
            _echo_model({"state": "cancelled", "document_id": document_id})
            return
        result = application.delete_document(document_id, keep_exports=keep_exports)
        if typer.confirm("Prune every unreferenced WAV in the global cache now", default=True):
            result["global_cache_prune"] = application.cache_prune(dry_run=False)
        else:
            result["global_cache_prune"] = {"deleted": 0, "skipped": True}
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    _echo_model(result)


@export_app.command("wav")
def export_wav(ctx: typer.Context, document_id: str) -> None:
    """Combine a complete cached narration into one WAV file."""
    try:
        _echo_model(_application(ctx).export_wav(document_id))
    except (KeyError, OSError, ValueError) as error:
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
    ctx: typer.Context,
    document_id: str,
    engine: str = typer.Option("mlx-audio", "--engine"),
    model: str | None = typer.Option(None, "--model"),
    model_revision: str | None = typer.Option(None, "--model-revision"),
    voice: str | None = typer.Option(None, "--voice"),
    language: str | None = typer.Option(None, "--language"),
    instruction: str | None = typer.Option(None, "--instruction"),
    speed: float | None = typer.Option(None, "--speed", min=0.5, max=2.0),
    no_play: bool = typer.Option(False, "--no-play"),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Print safe MLX generation diagnostics to stderr without document text.",
    ),
) -> None:
    """Generate ahead, play in order, and persist confirmed progress."""
    application = _application(ctx)
    settings = application.settings
    try:
        model = model or settings.default_model
        voice = voice or settings.default_voice
        language = language or settings.default_language
        instruction = instruction or settings.default_instruction
        speed = speed if speed is not None else settings.default_speed
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
            else MlxQwenTtsEngine(
                application.settings.data_dir / "models",
                debug=debug,
                error_log_path=application.settings.data_dir / "error.log",
            )
        )
        playback = FakePlaybackBackend() if no_play else AfplayBackend()
        result = application.speak(document_id, profile, tts, playback)
    except (KeyError, ModelUnavailableError, OSError, RuntimeError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    _echo_model(result)


@app.command()
def pause(ctx: typer.Context, document_id: str) -> None:
    """Request a cooperative pause without skipping the current chunk."""
    count = _application(ctx).request_pause(document_id)
    _echo_model({"document_id": document_id, "sessions_paused": count})


@app.command()
def resume(
    ctx: typer.Context, document_id: str, no_play: bool = typer.Option(False, "--no-play")
) -> None:
    """Resume the latest profile from the last unconfirmed chunk."""
    application = _application(ctx)
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
    ctx: typer.Context,
    model: str | None = typer.Option(None, "--model"),
    revision: str | None = typer.Option(None, "--revision"),
) -> None:
    """Explicitly download Qwen weights for later offline use."""
    settings = _settings(ctx)
    model_id = model or settings.default_model
    destination = setup_model(model_id, settings.data_dir / "models", revision)
    _echo_model({"model": model_id, "path": str(destination), "downloaded": True})


@models_app.command("verify")
def models_verify(ctx: typer.Context, model: str | None = typer.Option(None, "--model")) -> None:
    """Verify that model weights are available without network access."""
    settings = _settings(ctx)
    model_id = model or settings.default_model
    try:
        resolved = resolve_local_model(model_id, settings.data_dir / "models")
    except ModelUnavailableError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    _echo_model({"model": model_id, "local_path": str(resolved), "available": True})


@cache_app.command("inspect")
def cache_inspect(ctx: typer.Context) -> None:
    """Report audio cache file count and bytes."""
    _echo_model(_application(ctx).cache_inspect())


@cache_app.command("prune")
def cache_prune(
    ctx: typer.Context, dry_run: bool = typer.Option(True, "--dry-run/--delete")
) -> None:
    """Report unreferenced audio; delete only with --delete."""
    _echo_model(_application(ctx).cache_prune(dry_run=dry_run))


def main() -> None:
    """Run the Typer command-line application."""
    app()


if __name__ == "__main__":
    main()
