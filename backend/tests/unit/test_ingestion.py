from __future__ import annotations

import itertools

import pytest

from app.core.errors import PayloadTooLarge, UnsupportedMedia
from app.ingestion.chunking import chunk_sections
from app.ingestion.extract import ExtractionError, Section, extract
from app.ingestion.normalize import normalize_text
from app.ingestion.validation import (
    MARKDOWN,
    PDF,
    TEXT,
    sanitize_filename,
    title_from_filename,
    validate_upload,
)
from tests.helpers import make_pdf

MB = 1024 * 1024


# --- validation -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.txt", "passwd.txt"),
        ("..\\..\\windows\\system.ini.md", "system.ini.md"),
        ("report<script>.pdf", "report_script_.pdf"),
        ("a\x00b\x1f.txt", "ab.txt"),
        ("", "document"),
        (None, "document"),
        ("....", "document"),
    ],
)
def test_sanitize_filename_strips_paths_and_controls(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_filename_is_truncated() -> None:
    assert len(sanitize_filename("x" * 500 + ".txt")) == 200


def test_valid_uploads_are_typed_and_hashed() -> None:
    pdf = validate_upload("a.pdf", make_pdf(["hello"]), MB)
    assert pdf.media_type == PDF
    assert len(pdf.sha256) == 64
    assert validate_upload("notes.TXT", b"hello", MB).media_type == TEXT
    assert validate_upload("guide.markdown", b"# Hi", MB).media_type == MARKDOWN


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("a.exe", b"MZ....", "unsupported_media_type"),
        ("a.docx", b"PK\x03\x04", "unsupported_media_type"),
        ("noext", b"hello", "unsupported_media_type"),
        ("fake.pdf", b"hello, I am text", "content_mismatch"),
        ("zip.txt", b"PK\x03\x04rest", "content_mismatch"),
        ("pdf-as.md", b"%PDF-1.7 ...", "content_mismatch"),
        ("bin.txt", b"abc\x00def", "binary_text"),
        ("latin1.txt", "café".encode("latin-1"), "encoding"),
        ("empty.txt", b"", "empty_file"),
    ],
)
def test_invalid_uploads_are_rejected(name: str, data: bytes, code: str) -> None:
    with pytest.raises(UnsupportedMedia) as info:
        validate_upload(name, data, MB)
    assert info.value.code == code


def test_oversized_upload_is_rejected() -> None:
    with pytest.raises(PayloadTooLarge):
        validate_upload("big.txt", b"a" * (MB + 1), MB)


def test_title_from_filename() -> None:
    assert title_from_filename("travel_policy-2026.pdf") == "travel policy 2026"
    assert title_from_filename("") == "Untitled document"


# --- normalisation ----------------------------------------------------------------------------
def test_normalize_removes_hidden_characters_and_collapses_space() -> None:
    raw = "Ig\u200bnore\u202e this\u00a0\u00a0text\r\n\r\n\r\n\r\nnext  line\tend "
    assert normalize_text(raw) == "Ignore this text\n\nnext line end"


def test_normalize_joins_hyphenated_line_breaks_and_folds_ligatures() -> None:
    assert normalize_text("docu-\nment of\ufb01ce") == "document office"


# --- extraction -------------------------------------------------------------------------------
def test_extract_pdf_keeps_page_numbers() -> None:
    data = make_pdf(["First page text", "Second page text"])
    result = extract(data, PDF, max_pages=10, max_chars=10_000)
    assert result.page_count == 2
    assert [s.page for s in result.sections] == [1, 2]
    assert "Second page" in result.sections[1].text


def test_extract_pdf_page_limit() -> None:
    with pytest.raises(ExtractionError, match="limit"):
        extract(make_pdf(["a", "b", "c"]), PDF, max_pages=2, max_chars=10_000)


def test_extract_rejects_encrypted_pdf() -> None:
    with pytest.raises(ExtractionError, match="Encrypted"):
        extract(make_pdf(["secret"], encrypt="pw"), PDF, max_pages=10, max_chars=10_000)


def test_extract_rejects_malformed_pdf() -> None:
    with pytest.raises(ExtractionError):
        extract(b"%PDF-1.7\n this is not really a pdf", PDF, max_pages=10, max_chars=10_000)


def test_extract_rejects_pdf_without_text() -> None:
    with pytest.raises(ExtractionError, match="No extractable text"):
        extract(make_pdf([""]), PDF, max_pages=10, max_chars=10_000)


def test_extract_markdown_sections_and_code_fences() -> None:
    md = "# Guide\nIntro\n## Install\nRun it\n```\n# not a heading\n```\n## Use\nDo it\n# Other\nX"
    result = extract(md.encode(), MARKDOWN, max_pages=10, max_chars=10_000)
    headings = [s.heading for s in result.sections]
    assert headings == ["Guide", "Guide > Install", "Guide > Use", "Other"]
    assert "# not a heading" in result.sections[1].text


def test_extract_text_character_limit() -> None:
    with pytest.raises(ExtractionError, match="characters"):
        extract(b"a" * 2000, TEXT, max_pages=10, max_chars=1000)


# --- chunking ---------------------------------------------------------------------------------
def test_chunks_respect_size_and_overlap() -> None:
    text = " ".join(f"word{i}." for i in range(2000))
    drafts = chunk_sections([Section(text, page=3)], size=500, overlap=100)
    assert len(drafts) > 5
    assert all(len(d.content) <= 500 for d in drafts)
    assert [d.ordinal for d in drafts] == list(range(len(drafts)))
    assert all(d.page == 3 for d in drafts)
    # The start of each chunk repeats the end of the previous one.
    for prev, cur in itertools.pairwise(drafts):
        assert cur.content.split()[0] in prev.content


def test_chunks_never_cross_sections() -> None:
    sections = [Section("alpha " * 50, page=1), Section("beta " * 50, page=2)]
    drafts = chunk_sections(sections, size=200, overlap=20)
    for d in drafts:
        assert ("alpha" in d.content) != ("beta" in d.content)
        assert d.page == (1 if "alpha" in d.content else 2)


def test_long_unbroken_token_is_split() -> None:
    drafts = chunk_sections([Section("x" * 1000)], size=300, overlap=50)
    assert all(len(d.content) <= 300 for d in drafts)
    assert sum(len(d.content.replace("\n", "")) for d in drafts) >= 1000


def test_small_section_is_one_chunk() -> None:
    drafts = chunk_sections([Section("short text", heading="H")], size=300, overlap=50)
    assert len(drafts) == 1
    assert drafts[0].section == "H"


def test_overlap_must_be_smaller_than_size() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_sections([Section("x")], size=100, overlap=100)
