# Test Fixtures

The committed TXT, Markdown, and HTML fixtures are deliberately small and contain structural
content that must be included as well as navigation, destinations, front matter, and code that
must be excluded.

PDF tests generate temporary fixtures with ReportLab so their content is reproducible:

- a three-page born-digital PDF with a repeated header and page numbers;
- an image-only PDF with no text layer, which must raise `NeedsOcrError`;
- a password-protected PDF, which must be rejected without exposing content; and
- a two-page PDF used to prove the configured page safety limit.

Generated test PDFs are written only below Pytest's temporary directory. They are not private
documents and do not require network access.
