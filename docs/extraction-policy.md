# Extraction Policy

## Purpose

The reader produces a faithful, inspectable narration input. It removes presentation noise
but does not summarize, translate, paraphrase, or use an LLM to rewrite source text. The exact
cleaned text is stored as `cleaned/preview.txt` and should be reviewed for important or
layout-heavy documents.

## Supported formats

- TXT: detect a byte-order mark or use `charset-normalizer`, preserving blank-line paragraphs.
- Markdown: retain headings, paragraphs, list-item prose, block quotes, link labels, and inline
  code content; omit front matter, code blocks, raw destinations, and raw HTML.
- Local HTML: use Trafilatura main-content extraction without fetching external resources;
  omit scripts, styles, navigation, comments, tables, links, and images by default. A visible
  text fallback emits `html_fallback_extraction`.
- Born-digital PDF: extract each page through pypdf, retain page provenance, remove strictly
  detected repeated header/footer lines and isolated page numbers, and conservatively join
  wrapped lines.

## Deterministic normalization

All formats normalize line endings and Unicode to NFC, replace non-breaking and zero-width
spacing artifacts, convert common typographic ligatures to letter sequences, and collapse
horizontal presentation whitespace. Punctuation, quotations, parentheticals, and ordinary
citations remain because they affect meaning and prosody.

Chunking prefers section, paragraph, sentence, and clause boundaries in that order. The
initial target is 1,200 characters and the hard limit is 1,800 characters. A last-resort hard
split is tagged `forced_boundary`; no overlapping text is introduced.

## PDF limitations and warnings

PDF stores visual placement rather than dependable semantic structure. Multi-column text,
tables, captions, formulas, footnotes, and floating figures can have an incorrect reading
order even when extraction succeeds. Repeated page noise is removed only when it appears in
the same edge region on at least three pages and at least 60 percent of eligible pages.

An image-only or near-empty PDF raises `needs_ocr` and is not marked ready. An encrypted PDF,
unsupported type, empty file, conflicting PDF signature, excessive file size, or excessive
page count fails with an actionable error. The immutable source copy remains available for
diagnosis.
