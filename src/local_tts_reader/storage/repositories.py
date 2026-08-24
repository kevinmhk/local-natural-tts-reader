from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from local_tts_reader.domain.models import (
    AudioArtifact,
    Chunk,
    Document,
    DocumentListEntry,
    DocumentStatus,
    SynthesisProfile,
)
from local_tts_reader.storage.database import Database


class SessionRecord(TypedDict):
    """Fields needed to resume one playback profile."""

    next_ordinal: int
    state: str
    pause_requested: int


class Repository:
    """Typed persistence operations used by application services."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def document_exists(self, document_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return row is not None

    def save_document(
        self,
        document: Document,
        document_path: Path,
        preview_path: Path,
        chunks: tuple[Chunk, ...],
    ) -> None:
        """Persist one prepared document and replace its deterministic chunks."""
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO documents(
                    document_id, source_hash, source_name, media_type, title,
                    warnings_json, document_path, preview_path, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready')
                ON CONFLICT(document_id) DO UPDATE SET
                    title = excluded.title,
                    warnings_json = excluded.warnings_json,
                    document_path = excluded.document_path,
                    preview_path = excluded.preview_path,
                    state = 'ready'
                """,
                (
                    document.document_id,
                    document.source_hash,
                    document.source_name,
                    document.media_type,
                    document.title,
                    json.dumps(document.warnings),
                    str(document_path),
                    str(preview_path),
                ),
            )
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document.document_id,))
            connection.executemany(
                """
                INSERT INTO chunks(
                    chunk_id, document_id, ordinal, text, text_hash, boundary,
                    pause_after_ms, section_title, warnings_json, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.document_id,
                        chunk.ordinal,
                        chunk.text,
                        chunk.text_hash,
                        chunk.boundary,
                        chunk.pause_after_ms,
                        chunk.section_title,
                        json.dumps(chunk.warnings),
                    )
                    for chunk in chunks
                ],
            )

    def get_document_path(self, document_id: str) -> Path:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT document_path FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown document: {document_id}")
        return Path(row["document_path"])

    def get_preview_path(self, document_id: str) -> Path:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT preview_path FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown document: {document_id}")
        return Path(row["preview_path"])

    def get_chunks(self, document_id: str) -> tuple[Chunk, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chunks WHERE document_id = ? ORDER BY ordinal", (document_id,)
            ).fetchall()
        return tuple(
            Chunk(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                ordinal=row["ordinal"],
                text=row["text"],
                text_hash=row["text_hash"],
                boundary=row["boundary"],
                pause_after_ms=row["pause_after_ms"],
                section_title=row["section_title"],
                warnings=tuple(json.loads(row["warnings_json"])),
            )
            for row in rows
        )

    def mark_chunk_ready(self, chunk_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE chunks SET state = 'ready' WHERE chunk_id = ?", (chunk_id,))

    def get_audio(self, cache_key: str) -> AudioArtifact | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM audio_artifacts WHERE cache_key = ?", (cache_key,)
            ).fetchone()
        if row is None:
            return None
        return AudioArtifact(
            cache_key=row["cache_key"],
            chunk_id=row["chunk_id"],
            profile_hash=row["profile_hash"],
            path=Path(row["path"]),
            duration_seconds=row["duration_seconds"],
            sample_rate=row["sample_rate"],
        )

    def save_audio(self, artifact: AudioArtifact) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO audio_artifacts(
                    cache_key, chunk_id, profile_hash, path, duration_seconds, sample_rate
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.cache_key,
                    artifact.chunk_id,
                    artifact.profile_hash,
                    str(artifact.path),
                    artifact.duration_seconds,
                    artifact.sample_rate,
                ),
            )

    def list_audio_for_document(
        self,
        document_id: str,
        profile_hash: str,
    ) -> tuple[AudioArtifact, ...]:
        """Return one profile's cached document audio in chunk order."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT audio_artifacts.*
                FROM chunks
                JOIN audio_artifacts ON audio_artifacts.chunk_id = chunks.chunk_id
                WHERE chunks.document_id = ? AND audio_artifacts.profile_hash = ?
                ORDER BY chunks.ordinal
                """,
                (document_id, profile_hash),
            ).fetchall()
        return tuple(
            AudioArtifact(
                cache_key=row["cache_key"],
                chunk_id=row["chunk_id"],
                profile_hash=row["profile_hash"],
                path=Path(row["path"]),
                duration_seconds=row["duration_seconds"],
                sample_rate=row["sample_rate"],
            )
            for row in rows
        )

    def get_session(self, document_id: str, profile_hash: str) -> SessionRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM playback_sessions
                WHERE document_id = ? AND profile_hash = ?
                """,
                (document_id, profile_hash),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            next_ordinal=int(row["next_ordinal"]),
            state=str(row["state"]),
            pause_requested=int(row["pause_requested"]),
        )

    def latest_profile(self, document_id: str) -> SynthesisProfile:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT profile_json FROM playback_sessions
                WHERE document_id = ? ORDER BY updated_at DESC LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"document has no playback session: {document_id}")
        return SynthesisProfile.model_validate_json(row["profile_json"])

    def save_session(
        self,
        document_id: str,
        profile: SynthesisProfile,
        next_ordinal: int,
        state: str,
        *,
        clear_pause: bool = False,
    ) -> None:
        pause = 0 if clear_pause else None
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO playback_sessions(
                    document_id, profile_hash, profile_json, next_ordinal, state,
                    pause_requested, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(document_id, profile_hash) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    next_ordinal = excluded.next_ordinal,
                    state = excluded.state,
                    pause_requested = COALESCE(?, playback_sessions.pause_requested),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document_id,
                    profile.profile_hash,
                    profile.model_dump_json(),
                    next_ordinal,
                    state,
                    pause,
                ),
            )

    def request_pause(self, document_id: str) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE playback_sessions
                SET pause_requested = 1, state = 'paused', updated_at = CURRENT_TIMESTAMP
                WHERE document_id = ?
                """,
                (document_id,),
            )
        return cursor.rowcount

    def pause_requested(self, document_id: str, profile_hash: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT pause_requested FROM playback_sessions
                WHERE document_id = ? AND profile_hash = ?
                """,
                (document_id, profile_hash),
            ).fetchone()
        return bool(row and row["pause_requested"])

    def status(self, document_id: str) -> DocumentStatus:
        with self.database.connect() as connection:
            document = connection.execute(
                "SELECT state FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            counts = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN state = 'ready' THEN 1 ELSE 0 END) AS ready
                FROM chunks WHERE document_id = ?
                """,
                (document_id,),
            ).fetchone()
            session = connection.execute(
                """
                SELECT state, next_ordinal FROM playback_sessions
                WHERE document_id = ? ORDER BY updated_at DESC LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        if document is None:
            raise KeyError(f"unknown document: {document_id}")
        state = session["state"] if session else document["state"]
        return DocumentStatus(
            document_id=document_id,
            state=state,
            chunk_count=int(counts["total"] or 0),
            ready_count=int(counts["ready"] or 0),
            next_ordinal=int(session["next_ordinal"] if session else 0),
        )

    def list_documents(self) -> tuple[DocumentListEntry, ...]:
        """Return imported-document metadata without loading source or preview text."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                WITH chunk_counts AS (
                    SELECT document_id,
                           COUNT(*) AS chunk_count,
                           SUM(CASE WHEN state = 'ready' THEN 1 ELSE 0 END) AS ready_chunk_count
                    FROM chunks
                    GROUP BY document_id
                ),
                audio_counts AS (
                    SELECT chunks.document_id,
                           COUNT(audio_artifacts.cache_key) AS cached_audio_count,
                           COALESCE(SUM(audio_artifacts.duration_seconds), 0)
                               AS cached_audio_seconds
                    FROM chunks
                    JOIN audio_artifacts ON audio_artifacts.chunk_id = chunks.chunk_id
                    GROUP BY chunks.document_id
                )
                SELECT documents.document_id,
                       documents.source_name,
                       documents.title,
                       documents.media_type,
                       documents.created_at,
                       documents.warnings_json,
                       COALESCE(
                           (
                               SELECT playback_sessions.state
                               FROM playback_sessions
                               WHERE playback_sessions.document_id = documents.document_id
                               ORDER BY playback_sessions.updated_at DESC,
                                        playback_sessions.rowid DESC
                               LIMIT 1
                           ),
                           documents.state
                       ) AS state,
                       COALESCE(chunk_counts.chunk_count, 0) AS chunk_count,
                       COALESCE(chunk_counts.ready_chunk_count, 0) AS ready_chunk_count,
                       COALESCE(
                           (
                               SELECT playback_sessions.next_ordinal
                               FROM playback_sessions
                               WHERE playback_sessions.document_id = documents.document_id
                               ORDER BY playback_sessions.updated_at DESC,
                                        playback_sessions.rowid DESC
                               LIMIT 1
                           ),
                           0
                       ) AS next_ordinal,
                       COALESCE(audio_counts.cached_audio_count, 0) AS cached_audio_count,
                       COALESCE(audio_counts.cached_audio_seconds, 0) AS cached_audio_seconds
                FROM documents
                LEFT JOIN chunk_counts ON chunk_counts.document_id = documents.document_id
                LEFT JOIN audio_counts ON audio_counts.document_id = documents.document_id
                ORDER BY documents.created_at DESC, documents.document_id DESC
                """
            ).fetchall()
        return tuple(
            DocumentListEntry(
                document_id=row["document_id"],
                source_name=row["source_name"],
                title=row["title"],
                media_type=row["media_type"],
                imported_at=row["created_at"],
                state=row["state"],
                chunk_count=int(row["chunk_count"]),
                ready_chunk_count=int(row["ready_chunk_count"]),
                next_ordinal=int(row["next_ordinal"]),
                cached_audio_count=int(row["cached_audio_count"]),
                cached_audio_seconds=float(row["cached_audio_seconds"]),
                warning_count=len(json.loads(row["warnings_json"])),
            )
            for row in rows
        )

    def referenced_audio_paths(self) -> set[Path]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT path FROM audio_artifacts").fetchall()
        return {Path(row["path"]) for row in rows}
