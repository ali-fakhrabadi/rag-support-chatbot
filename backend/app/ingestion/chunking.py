"""Chunking with configurable size and overlap.

Chunks never cross a section (PDF page or Markdown heading), so every chunk has one page number
or section path that a citation can point to. Paragraphs are packed greedily; paragraphs larger
than the chunk size are split at sentence boundaries, then at word boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.extract import Section

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    content: str
    page: int | None
    section: str | None
    char_start: int
    char_end: int


def _pieces(text: str, size: int) -> list[str]:
    """Split text into pieces no longer than ``size``, preferring natural boundaries."""
    out: list[str] = []
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if len(paragraph) <= size:
            out.append(paragraph)
            continue
        for sentence in _SENTENCE.split(paragraph):
            if len(sentence) <= size:
                out.append(sentence)
                continue
            words, current = sentence.split(" "), ""
            for word in words:
                while len(word) > size:  # a single unbroken token longer than a chunk
                    if current:
                        out.append(current)
                        current = ""
                    out.append(word[:size])
                    word = word[size:]
                candidate = f"{current} {word}".strip()
                if len(candidate) > size:
                    out.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                out.append(current)
    return out


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return "" if overlap <= 0 else text
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail


def chunk_sections(sections: list[Section], size: int, overlap: int) -> list[ChunkDraft]:
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    drafts: list[ChunkDraft] = []
    offset = 0
    for section in sections:
        current = ""
        start_in_section = 0
        cursor = 0  # approximate character position inside the section
        # Pieces leave room for the overlap carried from the previous chunk and the separator.
        for piece in _pieces(section.text, max(1, size - overlap - 2)):
            joined = f"{current}\n\n{piece}" if current else piece
            if current and len(joined) > size:
                drafts.append(
                    ChunkDraft(
                        len(drafts),
                        current,
                        section.page,
                        section.heading,
                        offset + start_in_section,
                        offset + start_in_section + len(current),
                    )
                )
                carry = _tail(current, overlap)
                start_in_section = max(0, cursor - len(carry))
                current = f"{carry}\n\n{piece}" if carry else piece
            else:
                if not current:
                    start_in_section = cursor
                current = joined
            cursor += len(piece) + 2
        if current:
            drafts.append(
                ChunkDraft(
                    len(drafts),
                    current,
                    section.page,
                    section.heading,
                    offset + start_in_section,
                    offset + start_in_section + len(current),
                )
            )
        offset += len(section.text) + 2
    return drafts
