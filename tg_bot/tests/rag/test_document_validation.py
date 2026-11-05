import pytest

from app.llm.gateway import DocFile
from app.rag.chunking import (
    CorruptDocumentError,
    DocumentTooLargeError,
    EmptyDocumentError,
    UnsupportedDocumentError,
    load_document,
)


def test_pdf_is_rejected_without_success():
    with pytest.raises(UnsupportedDocumentError):
        load_document(DocFile("manual.pdf", b"%PDF-1.7"))


def test_empty_document_is_rejected():
    with pytest.raises(EmptyDocumentError):
        load_document(DocFile("empty.md", b" \n\t"))


def test_corrupt_utf8_is_rejected():
    with pytest.raises(CorruptDocumentError):
        load_document(DocFile("bad.md", b"\xff\xfe"))


def test_size_limits_are_enforced():
    with pytest.raises(DocumentTooLargeError):
        load_document(DocFile("big.md", b"x" * 10), max_bytes=4)
    with pytest.raises(DocumentTooLargeError):
        load_document(DocFile("chars.md", "длинно".encode()), max_chars=3)


def test_markdown_title_and_ids_are_deterministic():
    file = DocFile("docs/Alpha.md", "# Альфа\n\nТекст".encode())

    first = load_document(file)
    second = load_document(file)

    assert first.document_id == second.document_id
    assert first.document_version == second.document_version
    assert first.title == "Альфа"
    assert first.filename == "Alpha.md"
