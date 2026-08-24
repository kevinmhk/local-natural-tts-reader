from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from local_tts_reader.application.playback_service import PlaybackService
from local_tts_reader.application.synthesis_service import SynthesisService
from local_tts_reader.chunking.chunker import ChunkingConfig, chunk_document
from local_tts_reader.cleaning.common import clean_document
from local_tts_reader.config import Settings
from local_tts_reader.domain.models import (
    AudioArtifact,
    Chunk,
    Document,
    DocumentListEntry,
    DocumentStatus,
    SpeakResult,
    SynthesisProfile,
    WavExportResult,
    document_text,
)
from local_tts_reader.ingestion.registry import extractor_for, import_source
from local_tts_reader.playback.base import PlaybackBackend
from local_tts_reader.storage.artifacts import (
    atomic_write_json,
    atomic_write_text,
    cache_usage,
    concatenate_wavs,
)
from local_tts_reader.storage.database import Database
from local_tts_reader.storage.repositories import Repository
from local_tts_reader.tts.base import TtsEngine


@dataclass(frozen=True, slots=True)
class _FileSet:
    """Existing files selected for a document-scoped deletion operation."""

    paths: tuple[Path, ...]
    bytes: int


@dataclass(frozen=True, slots=True)
class _DocumentDeletionPlan:
    """Resolved local artifacts for one document deletion request."""

    document_id: str
    source_name: str
    document_directory: Path
    document_files: _FileSet
    audio_files: _FileSet
    export_files: _FileSet
    chunk_count: int
    audio_artifact_count: int
    playback_session_count: int
    event_count: int


class ReaderApplication:
    """Single application service used by CLI and tests."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_directories()
        self.database = Database(self.settings.data_dir / "reader.sqlite3")
        self.repository = Repository(self.database)
        self.synthesis = SynthesisService(
            self.repository,
            self.settings.data_dir / "audio",
            self.settings.min_free_bytes,
        )
        self.playback = PlaybackService(self.repository)

    def ingest(self, path: Path) -> Document:
        """Import, extract, clean, chunk, and persist one local document."""
        source = import_source(
            path,
            self.settings.data_dir / "documents",
            self.settings.max_file_bytes,
        )
        if self.repository.document_exists(source.document_id):
            return self.get_document(source.document_id)
        extractor = extractor_for(source.media_type, self.settings.max_pdf_pages)
        extracted = extractor.extract(source)
        cleaned = clean_document(extracted)
        if not document_text(cleaned):
            raise ValueError("document contains no readable text after cleaning")
        config = ChunkingConfig(
            target_chars=self.settings.chunk_target_chars,
            hard_limit_chars=self.settings.chunk_hard_limit_chars,
            paragraph_pause_ms=self.settings.paragraph_pause_ms,
            section_pause_ms=self.settings.section_pause_ms,
        )
        chunks = chunk_document(cleaned, config)
        document_root = self.settings.data_dir / "documents" / source.document_id
        extracted_path = document_root / "extracted" / "document.json"
        cleaned_path = document_root / "cleaned" / "document.json"
        preview_path = document_root / "cleaned" / "preview.txt"
        manifest_path = document_root / "chunks" / "manifest.json"
        atomic_write_json(extracted_path, extracted.model_dump(mode="json"))
        atomic_write_json(cleaned_path, cleaned.model_dump(mode="json"))
        atomic_write_text(preview_path, document_text(cleaned) + "\n")
        atomic_write_json(manifest_path, [chunk.model_dump(mode="json") for chunk in chunks])
        self.repository.save_document(cleaned, cleaned_path, preview_path, chunks)
        return cleaned

    def get_document(self, document_id: str) -> Document:
        """Load a prepared document from its inspectable JSON artifact."""
        return Document.model_validate_json(
            self.repository.get_document_path(document_id).read_text()
        )

    def preview(self, document_id: str) -> tuple[Document, Path, str]:
        """Return metadata, preview path, and exact spoken text."""
        document = self.get_document(document_id)
        path = self.repository.get_preview_path(document_id)
        return document, path, path.read_text(encoding="utf-8").rstrip("\n")

    def get_chunks(self, document_id: str) -> tuple[Chunk, ...]:
        """Return the persistent ordered chunk manifest."""
        return self.repository.get_chunks(document_id)

    def speak(
        self,
        document_id: str,
        profile: SynthesisProfile,
        engine: TtsEngine,
        playback: PlaybackBackend,
        *,
        resume_only: bool = False,
    ) -> SpeakResult:
        """Generate one chunk ahead, play in order, and persist confirmed progress."""
        chunks = self.get_chunks(document_id)
        if not chunks:
            raise ValueError("document has no chunks")
        session = self.repository.get_session(document_id, profile.profile_hash)
        next_ordinal = int(session["next_ordinal"]) if session else 0
        if session and session["state"] == "complete" and not resume_only:
            next_ordinal = 0
        self.repository.save_session(
            document_id,
            profile,
            next_ordinal,
            "synthesizing",
            clear_pause=True,
        )
        generated = 0
        cache_hits = 0
        played = 0

        def ensure(chunk: Chunk) -> tuple[AudioArtifact, bool]:
            return self.synthesis.ensure(chunk, profile, engine)

        future: Future[tuple[AudioArtifact, bool]] | None = None
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-ahead")
        try:
            for index in range(next_ordinal, len(chunks)):
                chunk = chunks[index]
                if future is None:
                    artifact, hit = ensure(chunk)
                else:
                    artifact, hit = future.result()
                    future = None
                generated += int(not hit)
                cache_hits += int(hit)
                if index + 1 < len(chunks):
                    future = executor.submit(ensure, chunks[index + 1])
                result = self.playback.play(
                    document_id,
                    profile.profile_hash,
                    artifact,
                    playback,
                )
                if not result.completed:
                    if future is not None:
                        future.result()
                    self.repository.save_session(document_id, profile, index, "paused")
                    return SpeakResult(
                        state="paused",
                        generated_count=generated,
                        cache_hit_count=cache_hits,
                        played_count=played,
                        next_ordinal=index,
                    )
                played += 1
                next_ordinal = index + 1
                self.repository.save_session(
                    document_id,
                    profile,
                    next_ordinal,
                    "playable" if next_ordinal < len(chunks) else "complete",
                    clear_pause=True,
                )
            return SpeakResult(
                state="complete",
                generated_count=generated,
                cache_hit_count=cache_hits,
                played_count=played,
                next_ordinal=next_ordinal,
            )
        except KeyboardInterrupt:
            playback.stop()
            self.repository.save_session(document_id, profile, next_ordinal, "paused")
            return SpeakResult(
                state="paused",
                generated_count=generated,
                cache_hit_count=cache_hits,
                played_count=played,
                next_ordinal=next_ordinal,
            )
        finally:
            executor.shutdown(wait=True, cancel_futures=False)
            engine.close()

    def resume(
        self,
        document_id: str,
        engine: TtsEngine,
        playback: PlaybackBackend,
    ) -> SpeakResult:
        """Resume the latest saved profile at its last unconfirmed chunk."""
        profile = self.repository.latest_profile(document_id)
        return self.speak(
            document_id,
            profile,
            engine,
            playback,
            resume_only=True,
        )

    def request_pause(self, document_id: str) -> int:
        """Request cooperative pause for all sessions of a document."""
        return self.repository.request_pause(document_id)

    def status(self, document_id: str) -> DocumentStatus:
        """Return a concise persistent status."""
        return self.repository.status(document_id)

    def list_documents(self) -> tuple[DocumentListEntry, ...]:
        """Return local-library metadata for selecting a document by ID."""
        return self.repository.list_documents()

    def document_delete_preview(
        self, document_id: str, *, keep_exports: bool = False
    ) -> dict[str, object]:
        """Return the exact reader-managed artifacts selected for deletion."""
        plan = self._document_deletion_plan(document_id)
        return self._deletion_result("dry_run", plan, keep_exports=keep_exports)

    def delete_document(self, document_id: str, *, keep_exports: bool = False) -> dict[str, object]:
        """Delete one document and only its unshared reader-managed artifacts."""
        plan = self._document_deletion_plan(document_id)
        self.repository.delete_document(document_id)
        removed_document_files = self._remove_document_directory(plan.document_directory)
        removed_audio_files, removed_audio_bytes, shared_audio_files = self._remove_audio_files(
            plan.audio_files.paths
        )
        if keep_exports:
            removed_export_files = 0
            removed_export_bytes = 0
        else:
            removed_export_files, removed_export_bytes = self._remove_files(plan.export_files.paths)
        return {
            "state": "deleted",
            "document_id": plan.document_id,
            "source_name": plan.source_name,
            "removed": {
                "document_directory": {
                    "path": str(plan.document_directory),
                    "files": removed_document_files,
                    "bytes": plan.document_files.bytes,
                },
                "database": {
                    "chunks": plan.chunk_count,
                    "audio_artifacts": plan.audio_artifact_count,
                    "playback_sessions": plan.playback_session_count,
                    "events": plan.event_count,
                },
                "audio_cache": {
                    "files": removed_audio_files,
                    "bytes": removed_audio_bytes,
                    "shared_files_kept": shared_audio_files,
                },
                "exports": {
                    "files": removed_export_files,
                    "bytes": removed_export_bytes,
                    "kept": keep_exports,
                },
            },
        }

    def export_wav(self, document_id: str) -> WavExportResult:
        """Combine one complete cached narration profile into a single WAV file."""
        document = self.get_document(document_id)
        profile = self.repository.latest_profile(document_id)
        chunks = self.get_chunks(document_id)
        artifacts = self.repository.list_audio_for_document(document_id, profile.profile_hash)
        artifacts_by_chunk = {artifact.chunk_id: artifact for artifact in artifacts}
        missing_ordinals = [
            str(chunk.ordinal + 1) for chunk in chunks if chunk.chunk_id not in artifacts_by_chunk
        ]
        if missing_ordinals:
            examples = ", ".join(missing_ordinals[:5])
            raise ValueError(
                "cached audio is incomplete; run 'reader speak' first "
                f"(missing chunk numbers: {examples})"
            )
        ordered_paths = tuple(artifacts_by_chunk[chunk.chunk_id].path for chunk in chunks)
        destination = (
            self.settings.data_dir
            / "exports"
            / (f"{Path(document.source_name).name}-{document_id}-{profile.profile_hash[:12]}.wav")
        )
        info = concatenate_wavs(ordered_paths, destination)
        return WavExportResult(
            document_id=document_id,
            profile_hash=profile.profile_hash,
            path=destination,
            chunk_count=len(chunks),
            duration_seconds=info.duration_seconds,
            sample_rate=info.sample_rate,
            channels=info.channels,
            bytes=destination.stat().st_size,
        )

    def cache_inspect(self) -> dict[str, int]:
        """Return local audio cache usage."""
        count, total_bytes = cache_usage(self.settings.data_dir / "audio")
        return {"files": count, "bytes": total_bytes}

    def cache_prune(self, *, dry_run: bool = True) -> dict[str, int]:
        """Report or delete unreferenced WAV files only."""
        root = self.settings.data_dir / "audio"
        referenced = {path.resolve() for path in self.repository.referenced_audio_paths()}
        candidates = [
            path
            for path in root.rglob("*.wav")
            if path.is_file() and path.resolve() not in referenced
        ]
        total_bytes = sum(path.stat().st_size for path in candidates)
        if not dry_run:
            for path in candidates:
                path.unlink()
        return {"files": len(candidates), "bytes": total_bytes, "deleted": int(not dry_run)}

    def _document_deletion_plan(self, document_id: str) -> _DocumentDeletionPlan:
        metadata = self.repository.document_deletion_metadata(document_id)
        document_root = (self.settings.data_dir / "documents").resolve()
        document_directory = (document_root / document_id).resolve()
        if not document_directory.is_relative_to(document_root):
            raise ValueError("document ID resolves outside the configured document workspace")
        audio_root = (self.settings.data_dir / "audio").resolve()
        audio_paths = tuple(
            path
            for path in metadata["audio_paths"]
            if path.is_file() and path.resolve().is_relative_to(audio_root)
        )
        export_root = self.settings.data_dir / "exports"
        export_paths = tuple(
            path for path in export_root.glob(f"*-{document_id}-*.wav") if path.is_file()
        )
        return _DocumentDeletionPlan(
            document_id=document_id,
            source_name=metadata["source_name"],
            document_directory=document_directory,
            document_files=self._file_set(
                document_directory.rglob("*") if document_directory.is_dir() else ()
            ),
            audio_files=self._file_set(audio_paths),
            export_files=self._file_set(export_paths),
            chunk_count=metadata["chunk_count"],
            audio_artifact_count=metadata["audio_artifact_count"],
            playback_session_count=metadata["playback_session_count"],
            event_count=metadata["event_count"],
        )

    @staticmethod
    def _file_set(paths: Iterable[Path]) -> _FileSet:
        files = tuple(path for path in paths if path.is_file())
        return _FileSet(paths=files, bytes=sum(path.stat().st_size for path in files))

    @staticmethod
    def _remove_document_directory(path: Path) -> int:
        files = (
            tuple(candidate for candidate in path.rglob("*") if candidate.is_file())
            if path.is_dir()
            else ()
        )
        if path.is_dir():
            shutil.rmtree(path)
        return len(files)

    @staticmethod
    def _remove_files(paths: tuple[Path, ...]) -> tuple[int, int]:
        removed = 0
        total_bytes = 0
        for path in paths:
            if path.is_file():
                total_bytes += path.stat().st_size
                path.unlink()
                removed += 1
        return removed, total_bytes

    def _remove_audio_files(self, paths: tuple[Path, ...]) -> tuple[int, int, int]:
        referenced = {path.resolve() for path in self.repository.referenced_audio_paths()}
        candidates = tuple(path for path in paths if path.resolve() not in referenced)
        removed, total_bytes = self._remove_files(candidates)
        return removed, total_bytes, len(paths) - len(candidates)

    @staticmethod
    def _deletion_result(
        state: str, plan: _DocumentDeletionPlan, *, keep_exports: bool
    ) -> dict[str, object]:
        export_files = 0 if keep_exports else len(plan.export_files.paths)
        export_bytes = 0 if keep_exports else plan.export_files.bytes
        return {
            "state": state,
            "document_id": plan.document_id,
            "source_name": plan.source_name,
            "removed": {
                "document_directory": {
                    "path": str(plan.document_directory),
                    "files": len(plan.document_files.paths),
                    "bytes": plan.document_files.bytes,
                },
                "database": {
                    "chunks": plan.chunk_count,
                    "audio_artifacts": plan.audio_artifact_count,
                    "playback_sessions": plan.playback_session_count,
                    "events": plan.event_count,
                },
                "audio_cache": {
                    "files": len(plan.audio_files.paths),
                    "bytes": plan.audio_files.bytes,
                },
                "exports": {
                    "files": export_files,
                    "bytes": export_bytes,
                    "kept": keep_exports,
                },
            },
        }

    def debug_dump(self, document_id: str) -> str:
        """Return safe metadata without full source text."""
        status = self.status(document_id)
        return json.dumps(status.model_dump(mode="json"), sort_keys=True)
