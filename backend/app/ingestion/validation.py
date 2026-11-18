"""Upload validation: allowed types, content sniffing, size limits and safe display names.

Uploaded bytes are never written to a user-chosen path; the original filename is only kept as a
sanitised display name.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from app.core.errors import PayloadTooLarge, UnsupportedMedia

PDF = "application/pdf"
TEXT = "text/plain"
MARKDOWN = "text/markdown"

_EXTENSIONS = {".pdf": PDF, ".txt": TEXT, ".md": MARKDOWN, ".markdown": MARKDOWN}
# Signatures of binary formats that must never be accepted as text.
_BINARY_SIGNATURES = (
    b"%PDF-",
    b"PK\x03\x04",  # zip, docx, xlsx, jar
    b"MZ",  # windows executables
    b"\x7fELF",
    b"\x1f\x8b",  # gzip
    b"Rar!",
    b"7z\xbc\xaf",
    b"\xd0\xcf\x11\xe0",  # legacy office
)
_SAFE_NAME = re.compile(r"[^\w .()\-\[\]]+", re.UNICODE)


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    media_type: str
    sha256: str
    size_bytes: int


def sanitize_filename(name: str | None) -> str:
    """Keep only the final path component and harmless characters."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = unicodedata.normalize("NFKC", base)
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C")
    base = _SAFE_NAME.sub("_", base).strip(" .")
    return base[:200] or "document"


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot > 0 else ""


def validate_upload(filename: str | None, data: bytes, max_bytes: int) -> ValidatedUpload:
    if len(data) == 0:
        raise UnsupportedMedia("The file is empty", code="empty_file")
    if len(data) > max_bytes:
        raise PayloadTooLarge(f"The file exceeds the {max_bytes // (1024 * 1024)} MB limit")
    safe = sanitize_filename(filename)
    media_type = _EXTENSIONS.get(_extension(safe))
    if media_type is None:
        raise UnsupportedMedia("Only PDF, TXT and Markdown files are accepted")

    head = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n")
    if media_type == PDF:
        if not head.startswith(b"%PDF-"):
            raise UnsupportedMedia("The file is not a valid PDF", code="content_mismatch")
    else:
        if any(head.startswith(sig) for sig in _BINARY_SIGNATURES):
            raise UnsupportedMedia(
                "The file content does not match its extension", code="content_mismatch"
            )
        if b"\x00" in data:
            raise UnsupportedMedia("Text files must not contain binary data", code="binary_text")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedMedia("Text files must be UTF-8", code="encoding") from exc

    return ValidatedUpload(
        filename=safe,
        media_type=media_type,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def title_from_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return re.sub(r"[_\-]+", " ", stem).strip()[:300] or "Untitled document"
