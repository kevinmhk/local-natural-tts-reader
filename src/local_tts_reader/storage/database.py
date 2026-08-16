from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1


class Database:
    """Small SQLite database with replay-safe schema initialization."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        """Open one configured connection."""
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit a transaction or roll it back on error."""
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Apply the current additive schema safely."""
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    title TEXT,
                    warnings_json TEXT NOT NULL,
                    document_path TEXT NOT NULL,
                    preview_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    boundary TEXT NOT NULL,
                    pause_after_ms INTEGER NOT NULL,
                    section_title TEXT,
                    warnings_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    UNIQUE(document_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS audio_artifacts (
                    cache_key TEXT PRIMARY KEY,
                    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    profile_hash TEXT NOT NULL,
                    path TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    sample_rate INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS playback_sessions (
                    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                    profile_hash TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    next_ordinal INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'ready',
                    pause_requested INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(document_id, profile_hash)
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT,
                    stage TEXT NOT NULL,
                    category TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_version(version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
