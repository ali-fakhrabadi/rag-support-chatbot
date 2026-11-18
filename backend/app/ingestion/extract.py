"""Text extraction. Nothing in a document is executed: PDF JavaScript, forms, attachments and
embedded files are ignored, and Markdown is treated as plain text (never rendered as HTML)."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PyPdfError

from app.ingestion.normalize import normalize_text
from app.ingestion.validation import MARKDOWN, PDF

log = logging.getLogger("rag.extract")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


class ExtractionError(Exception):
    """The document is malformed, encrypted, too large or has no extractable text."""


@dataclass(frozen=True)
class Section:
    text: str
    page: int | None = None
    heading: str | None = None


@dataclass(frozen=True)
class Extracted:
    sections: list[Section]
    page_count: int | None


def extract(data: bytes, media_type: str, *, max_pages: int, max_chars: int) -> Extracted:
    if media_type == PDF:
        result = _extract_pdf(data, max_pages)
    else:
        text = data.decode("utf-8-sig")
        if len(text) > max_chars:
            raise ExtractionError(f"Document has more than {max_chars} characters")
        sections = _split_markdown(text) if media_type == MARKDOWN else [Section(text)]
        result = Extracted(sections, None)

    sections = [Section(normalize_text(s.text), s.page, s.heading) for s in result.sections]
    sections = [s for s in sections if s.text]
    total = sum(len(s.text) for s in sections)
    if total == 0:
        raise ExtractionError("No extractable text found (scanned PDFs need OCR, not supported)")
    if total > max_chars:
        raise ExtractionError(f"Document has more than {max_chars} characters")
    return Extracted(sections, result.page_count)


def _extract_pdf(data: bytes, max_pages: int) -> Extracted:
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ExtractionError("Encrypted PDFs are not supported")
        page_count = len(reader.pages)
        if page_count > max_pages:
            raise ExtractionError(f"PDF has {page_count} pages; the limit is {max_pages}")
        sections = []
        for number, page in enumerate(reader.pages, start=1):
            sections.append(Section(page.extract_text() or "", page=number))
        return Extracted(sections, page_count)
    except ExtractionError:
        raise
    except (PdfReadError, PyPdfError, ValueError, KeyError, TypeError, RecursionError) as exc:
        log.info("pdf extraction failed: %s", type(exc).__name__)
        raise ExtractionError("The PDF is malformed or cannot be read") from exc


def _split_markdown(text: str) -> list[Section]:
    """Split on ATX headings and keep the heading path as the section name."""
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    buffer: list[str] = []
    has_body = False  # a heading followed by nothing is not worth a chunk of its own
    in_fence = False

    def flush() -> None:
        nonlocal has_body
        body = "\n".join(buffer).strip()
        if body and has_body:
            sections.append(Section(body, heading=" > ".join(h for _, h in path) or None))
        buffer.clear()
        has_body = False

    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            path[:] = [(lvl, h) for lvl, h in path if lvl < level]
            path.append((level, match.group(2)[:120]))
            buffer.append(match.group(2))
        else:
            buffer.append(line)
            has_body = has_body or bool(line.strip())
    flush()
    return sections
