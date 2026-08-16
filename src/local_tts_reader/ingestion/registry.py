from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from local_tts_reader.ingestion.base import Extractor, ImportedSource, IngestionError
from local_tts_reader.ingestion.html import HtmlExtractor
from local_tts_reader.ingestion.markdown import MarkdownExtractor
from local_tts_reader.ingestion.pdf import PdfExtractor
from local_tts_reader.ingestion.text import TextExtractor

_MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
}


def hash_file(path: Path) -> str:
    """Hash a file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def detect_media_type(path: Path) -> str:
    """Detect one supported local media type from extension and signature."""
    media_type = _MEDIA_TYPES.get(path.suffix.casefold())
    prefix = path.read_bytes()[:8]
    if prefix.startswith(b"%PDF-"):
        if media_type not in {None, "application/pdf"}:
            raise IngestionError("file extension conflicts with PDF content")
        return "application/pdf"
    if media_type == "application/pdf":
        raise IngestionError("file has a PDF extension but no PDF signature")
    if media_type is None:
        raise IngestionError(f"unsupported file type: {path.suffix or '(no extension)'}")
    return media_type


def validate_source(path: Path, max_bytes: int) -> Path:
    """Resolve and validate a local regular file."""
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise IngestionError("source must be a regular file")
    size = resolved.stat().st_size
    if size <= 0:
        raise IngestionError("source file is empty")
    if size > max_bytes:
        raise IngestionError(f"source exceeds the {max_bytes}-byte safety limit")
    return resolved


def extractor_for(media_type: str, max_pdf_pages: int) -> Extractor:
    """Return the format adapter for a detected media type."""
    if media_type == "text/plain":
        return TextExtractor()
    if media_type == "text/markdown":
        return MarkdownExtractor()
    if media_type == "text/html":
        return HtmlExtractor()
    if media_type == "application/pdf":
        return PdfExtractor(max_pages=max_pdf_pages)
    raise IngestionError(f"unsupported media type: {media_type}")


def import_source(
    path: Path,
    documents_root: Path,
    max_bytes: int,
) -> ImportedSource:
    """Validate and atomically copy an immutable source into its workspace."""
    resolved = validate_source(path, max_bytes)
    media_type = detect_media_type(resolved)
    source_hash = hash_file(resolved)
    document_id = hashlib.sha256(f"{source_hash}:import-v1:{media_type}".encode()).hexdigest()[:20]
    source_dir = documents_root / document_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    destination = source_dir / resolved.name
    if not destination.exists():
        temporary = source_dir / f".{resolved.name}.{uuid.uuid4().hex}.tmp"
        shutil.copyfile(resolved, temporary)
        if hash_file(temporary) != source_hash:
            temporary.unlink(missing_ok=True)
            raise IngestionError("source changed while it was copied")
        temporary.replace(destination)
    return ImportedSource(
        document_id=document_id,
        path=destination,
        original_name=resolved.name,
        source_hash=source_hash,
        media_type=media_type,
    )
