from __future__ import annotations

from bs4 import BeautifulSoup
from trafilatura import extract

from local_tts_reader.domain.models import Block, Document, Section
from local_tts_reader.ingestion.base import ImportedSource, IngestionError
from local_tts_reader.ingestion.markdown import parse_markdown_content


class HtmlExtractor:
    """Extract local HTML main content without fetching external resources."""

    def extract(self, source: ImportedSource) -> Document:
        raw = source.path.read_bytes()
        html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        markdown = extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=False,
            include_links=False,
            include_images=False,
            favor_precision=True,
        )
        warnings: tuple[str, ...] = ()
        if markdown:
            sections, parse_warnings = parse_markdown_content(markdown)
            warnings = parse_warnings
        else:
            for tag in soup(["script", "style", "nav", "aside", "footer", "noscript"]):
                tag.decompose()
            blocks = tuple(
                Block(kind="paragraph", text=tag.get_text(" ", strip=True))
                for tag in soup.select("h1, h2, h3, p, li, blockquote")
                if tag.get_text(" ", strip=True)
            )
            sections = (Section(blocks=blocks),) if blocks else ()
            warnings = ("html_fallback_extraction",)
        if not sections:
            raise IngestionError("HTML document contains no readable main content")
        return Document(
            document_id=source.document_id,
            source_name=source.original_name,
            source_hash=source.source_hash,
            media_type=source.media_type,
            title=title,
            sections=sections,
            warnings=warnings,
        )
