from __future__ import annotations

from local_tts_reader.chunking.chunker import ChunkingConfig, chunk_document
from local_tts_reader.domain.models import Block, Document, Section, document_text


def test_chunking_is_deterministic_and_lossless() -> None:
    document = Document(
        document_id="doc-1",
        source_name="essay.txt",
        source_hash="abc",
        media_type="text/plain",
        title="Essay",
        sections=(
            Section(
                heading="Opening",
                level=1,
                blocks=(
                    Block(kind="paragraph", text="First sentence. Second sentence."),
                    Block(kind="paragraph", text="A final paragraph."),
                ),
            ),
        ),
    )
    config = ChunkingConfig(target_chars=35, hard_limit_chars=48)

    first = chunk_document(document, config)
    second = chunk_document(document, config)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert all(len(chunk.text) <= config.hard_limit_chars for chunk in first)
    assert "\n\n".join(chunk.text for chunk in first) == document_text(document)


def test_default_chunking_policy_is_short_for_qwen_tts() -> None:
    config = ChunkingConfig()

    assert config.target_chars == 280
    assert config.hard_limit_chars == 360
    assert config.version == "2"


def test_oversized_sentence_uses_a_forced_unicode_safe_boundary() -> None:
    text = "word " * 60
    document = Document(
        document_id="doc-2",
        source_name="long.txt",
        source_hash="def",
        media_type="text/plain",
        sections=(Section(blocks=(Block(kind="paragraph", text=text.strip()),)),),
    )

    chunks = chunk_document(document, ChunkingConfig(target_chars=40, hard_limit_chars=50))

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 50 for chunk in chunks)
    assert any("forced_boundary" in chunk.warnings for chunk in chunks)
