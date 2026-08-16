from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from local_tts_reader.domain.models import document_text
from local_tts_reader.ingestion.base import ImportedSource, IngestionError, NeedsOcrError
from local_tts_reader.ingestion.html import HtmlExtractor
from local_tts_reader.ingestion.markdown import MarkdownExtractor
from local_tts_reader.ingestion.pdf import PdfExtractor
from local_tts_reader.ingestion.text import TextExtractor


def _source(path: Path, media_type: str) -> ImportedSource:
    return ImportedSource(
        document_id="fixture",
        path=path,
        original_name=path.name,
        source_hash="fixture-hash",
        media_type=media_type,
    )


def test_text_extractor_preserves_paragraphs(fixture_root: Path) -> None:
    path = fixture_root / "text" / "two_paragraphs.txt"
    document = TextExtractor().extract(_source(path, "text/plain"))

    assert len(document.sections[0].blocks) == 2
    assert "second paragraph" in document_text(document)


def test_text_extractor_detects_non_utf8_input(tmp_path: Path) -> None:
    path = tmp_path / "latin-1.txt"
    path.write_bytes("Caf\xe9 has readable local prose.".encode("latin-1"))

    document = TextExtractor().extract(_source(path, "text/plain"))

    assert document_text(document) == "Caf\xe9 has readable local prose."


def test_markdown_extractor_keeps_labels_and_skips_code(fixture_root: Path) -> None:
    path = fixture_root / "markdown" / "article.md"
    document = MarkdownExtractor().extract(_source(path, "text/markdown"))
    spoken = document_text(document)

    assert "A Local Article" in spoken
    assert "useful label" in spoken
    assert "https://" not in spoken
    assert "not narrated" not in spoken
    assert "skipped_code_blocks:1" in document.warnings


def test_html_extractor_never_includes_navigation_or_script(fixture_root: Path) -> None:
    path = fixture_root / "html" / "article.html"
    document = HtmlExtractor().extract(_source(path, "text/html"))
    spoken = document_text(document)

    assert "meaningful article text" in spoken
    assert "Navigation" not in spoken
    assert "fetch" not in spoken


def test_pdf_extractor_removes_repeated_headers(tmp_path: Path) -> None:
    path = tmp_path / "repeated-header.pdf"
    canvas = Canvas(str(path), pagesize=letter)
    for page in range(1, 4):
        canvas.drawString(72, 760, "Repeated Report Header")
        canvas.drawString(72, 700, f"Meaningful paragraph for page {page}.")
        canvas.drawString(300, 40, str(page))
        canvas.showPage()
    canvas.save()

    document = PdfExtractor().extract(_source(path, "application/pdf"))
    spoken = document_text(document)

    assert "Repeated Report Header" not in spoken
    assert "Meaningful paragraph for page 1" in spoken
    assert "removed_repeated_headers:1" in document.warnings


def test_image_only_pdf_requires_ocr(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    canvas = Canvas(str(path), pagesize=letter)
    canvas.rect(72, 600, 300, 100, fill=1)
    canvas.showPage()
    canvas.save()

    with pytest.raises(NeedsOcrError):
        PdfExtractor().extract(_source(path, "application/pdf"))


def test_password_protected_pdf_is_rejected(tmp_path: Path) -> None:
    plain_path = tmp_path / "plain.pdf"
    encrypted_path = tmp_path / "encrypted.pdf"
    canvas = Canvas(str(plain_path), pagesize=letter)
    canvas.drawString(72, 700, "This text is long enough to be a readable PDF page.")
    canvas.showPage()
    canvas.save()
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(plain_path))
    writer.encrypt("secret")
    with encrypted_path.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(IngestionError, match="encrypted PDF"):
        PdfExtractor().extract(_source(encrypted_path, "application/pdf"))


def test_pdf_page_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "two-pages.pdf"
    canvas = Canvas(str(path), pagesize=letter)
    for page in range(2):
        canvas.drawString(72, 700, f"Readable content on page {page + 1}, beyond a one-page limit.")
        canvas.showPage()
    canvas.save()

    with pytest.raises(IngestionError, match="1-page safety limit"):
        PdfExtractor(max_pages=1).extract(_source(path, "application/pdf"))
