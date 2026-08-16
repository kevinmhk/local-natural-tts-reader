from pathlib import Path

import pytest

from local_tts_reader.ingestion.base import IngestionError
from local_tts_reader.ingestion.registry import detect_media_type, import_source, validate_source


def test_signature_extension_conflict_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "disguised.txt"
    path.write_bytes(b"%PDF-1.7\n")

    with pytest.raises(IngestionError, match="conflicts"):
        detect_media_type(path)


def test_pdf_extension_without_signature_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "not-a-pdf.pdf"
    path.write_text("plain text", encoding="utf-8")

    with pytest.raises(IngestionError, match="no PDF signature"):
        detect_media_type(path)


def test_oversized_source_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    path.write_text("12345", encoding="utf-8")

    with pytest.raises(IngestionError, match="safety limit"):
        validate_source(path, max_bytes=4)


def test_source_import_is_byte_identical_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "article.txt"
    content = b"Immutable source bytes.\n"
    path.write_bytes(content)

    first = import_source(path, tmp_path / "documents", max_bytes=1_000)
    second = import_source(path, tmp_path / "documents", max_bytes=1_000)

    assert first.document_id == second.document_id
    assert first.path.read_bytes() == content
