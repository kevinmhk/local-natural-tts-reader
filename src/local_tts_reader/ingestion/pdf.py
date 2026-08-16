from __future__ import annotations

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

from local_tts_reader.cleaning.pdf import (
    is_page_number,
    join_pdf_lines,
    normalize_repeated_line,
    repeated_page_noise,
)
from local_tts_reader.domain.models import Block, Document, Section, SourceSpan
from local_tts_reader.ingestion.base import ImportedSource, IngestionError, NeedsOcrError


def _metadata_text(value: object) -> str | None:
    return str(value).strip() if value else None


class PdfExtractor:
    """Extract born-digital PDF text with page provenance and conservative noise removal."""

    def __init__(self, max_pages: int = 2_000) -> None:
        self.max_pages = max_pages

    def extract(self, source: ImportedSource) -> Document:
        try:
            reader = PdfReader(source.path)
            if reader.is_encrypted and reader.decrypt("") == 0:
                raise IngestionError("encrypted PDF requires a password and cannot be imported")
            if len(reader.pages) > self.max_pages:
                raise IngestionError(f"PDF exceeds the {self.max_pages}-page safety limit")
            page_lines: list[list[str]] = [
                list((page.extract_text() or "").splitlines()) for page in reader.pages
            ]
        except IngestionError:
            raise
        except (PdfReadError, FileNotDecryptedError, OSError) as error:
            raise IngestionError(f"unable to read PDF: {error}") from error

        readable_chars = sum(len("".join(lines).strip()) for lines in page_lines)
        if readable_chars < 20:
            raise NeedsOcrError("PDF has no useful text layer; OCR is required")

        repeated = repeated_page_noise(page_lines)
        sections: list[Section] = []
        empty_pages = 0
        for page_number, lines in enumerate(page_lines, start=1):
            kept: list[str] = []
            for line in lines:
                normalized = normalize_repeated_line(line)
                if normalized in repeated or is_page_number(line):
                    continue
                kept.append(line)
            paragraphs = join_pdf_lines(kept)
            if not paragraphs:
                empty_pages += 1
                continue
            span = SourceSpan(page_start=page_number, page_end=page_number)
            sections.append(
                Section(
                    blocks=tuple(
                        Block(kind="paragraph", text=paragraph, source_spans=(span,))
                        for paragraph in paragraphs
                    ),
                    source_spans=(span,),
                )
            )
        if not sections:
            raise NeedsOcrError("PDF has no readable text after conservative cleaning")
        warnings: list[str] = []
        if repeated:
            warnings.append(f"removed_repeated_headers:{len(repeated)}")
        if empty_pages:
            warnings.append(f"pages_without_text:{empty_pages}")
        metadata = reader.metadata
        return Document(
            document_id=source.document_id,
            source_name=source.original_name,
            source_hash=source.source_hash,
            media_type=source.media_type,
            title=_metadata_text(metadata.title) if metadata else None,
            author=_metadata_text(metadata.author) if metadata else None,
            sections=tuple(sections),
            warnings=tuple(warnings),
        )
