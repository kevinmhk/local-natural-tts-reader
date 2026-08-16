from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token

from local_tts_reader.domain.models import Block, Document, Section
from local_tts_reader.ingestion.base import ImportedSource, IngestionError


def _inline_text(token: Token) -> str:
    children = token.children or []
    parts: list[str] = []
    for child in children:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
        elif child.type == "image" and child.content:
            parts.append(child.content)
    return "".join(parts).strip()


def _strip_front_matter(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, min(len(lines), 200)):
            if lines[index].strip() == "---":
                return "\n".join(lines[index + 1 :])
    return text


def parse_markdown_content(text: str) -> tuple[tuple[Section, ...], tuple[str, ...]]:
    """Parse readable Markdown structure while omitting markup destinations and code blocks."""
    tokens = MarkdownIt("commonmark").parse(_strip_front_matter(text))
    sections: list[Section] = []
    heading: str | None = None
    level: int | None = None
    blocks: list[Block] = []
    skipped_code = 0

    def flush() -> None:
        nonlocal blocks
        if heading or blocks:
            sections.append(Section(heading=heading, level=level, blocks=tuple(blocks)))
        blocks = []

    for index, token in enumerate(tokens):
        if token.type in {"fence", "code_block", "html_block"}:
            skipped_code += 1
            continue
        if token.type == "heading_open":
            flush()
            level = int(token.tag[1:]) if token.tag.startswith("h") else 1
            next_token = tokens[index + 1] if index + 1 < len(tokens) else token
            heading = _inline_text(next_token)
            continue
        if token.type == "paragraph_open":
            next_token = tokens[index + 1] if index + 1 < len(tokens) else token
            content = _inline_text(next_token)
            if content:
                blocks.append(Block(kind="paragraph", text=content))
    flush()
    warnings = (f"skipped_code_blocks:{skipped_code}",) if skipped_code else ()
    return tuple(sections), warnings


class MarkdownExtractor:
    """Extract headings and prose from local Markdown."""

    def extract(self, source: ImportedSource) -> Document:
        text = source.path.read_text(encoding="utf-8-sig")
        sections, warnings = parse_markdown_content(text)
        if not sections:
            raise IngestionError("Markdown document contains no readable prose")
        return Document(
            document_id=source.document_id,
            source_name=source.original_name,
            source_hash=source.source_hash,
            media_type=source.media_type,
            sections=sections,
            warnings=warnings,
        )
