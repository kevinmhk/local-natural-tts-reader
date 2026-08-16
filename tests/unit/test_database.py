from pathlib import Path

from local_tts_reader.storage.database import SCHEMA_VERSION, Database


def test_schema_initialization_is_replay_safe(tmp_path: Path) -> None:
    path = tmp_path / "reader.sqlite3"
    Database(path)
    database = Database(path)

    with database.connect() as connection:
        versions = connection.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()

    assert [row["version"] for row in versions] == [SCHEMA_VERSION]
