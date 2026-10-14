"""Extract text and split passages while preserving exact source locations."""

import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from backend.config import Settings
from backend.errors import AppError

SUPPORTED = {".txt", ".md", ".pdf"}


@dataclass(frozen=True)
class PageText:
    text: str
    page: int | None = None


@dataclass(frozen=True)
class Passage:
    text: str
    page: int | None
    start: int
    end: int
    line_start: int
    line_end: int


def safe_filename(filename: str) -> str:
    """Names are display metadata; disk paths are generated UUIDs."""
    name = re.split(r"[/\\]", unicodedata.normalize("NFKC", filename))[-1]
    name = "".join(c for c in name if c.isprintable()).strip().strip(".")
    if not name or len(name) > 180:
        raise AppError("invalid_filename", "Use a filename between 1 and 180 characters.")
    if Path(name).suffix.lower() not in SUPPORTED:
        raise AppError("unsupported_type", "Upload a PDF, TXT, or Markdown (.md) document.", 415)
    return name


def extract_pages(content: bytes, filename: str, settings: Settings) -> list[PageText]:
    if not content:
        raise AppError("empty_file", "This document is empty.")
    if len(content) > settings.max_upload_bytes:
        raise AppError("file_too_large", "Documents must be 10 MB or smaller.", 413)
    pages: list[PageText] = []
    if Path(filename).suffix.lower() == ".pdf":
        if not content.lstrip().startswith(b"%PDF-"):
            raise AppError("invalid_pdf", "This file does not contain a valid PDF header.")
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            if reader.is_encrypted:
                raise AppError("encrypted_pdf", "Remove the PDF password before uploading it.")
            if len(reader.pages) > settings.max_pages:
                raise AppError(
                    "too_many_pages", f"PDFs must have at most {settings.max_pages} pages."
                )
            total = 0
            for number, page in enumerate(reader.pages, start=1):
                stream = page.get_contents()
                if stream is not None and len(stream.get_data()) > 5 * 1024 * 1024:
                    raise AppError(
                        "complex_pdf", "A PDF page is too complex. Export a simpler PDF."
                    )
                text = (page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n")
                total += len(text)
                if total > settings.max_text_chars:
                    raise AppError("text_too_large", "The extracted document text is too large.")
                pages.append(PageText(text=text, page=number))
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "invalid_pdf", "This PDF could not be read. Try exporting it again."
            ) from exc
    else:
        try:
            text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError as exc:
            raise AppError(
                "invalid_encoding", "Save this text document using UTF-8 encoding."
            ) from exc
        if any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise AppError("binary_file", "This looks like a binary file, not a text document.")
        if len(text) > settings.max_text_chars:
            raise AppError("text_too_large", "The document contains too much text.")
        pages.append(PageText(text=text))
    if not any(p.text.strip() for p in pages):
        raise AppError("no_text", "No readable text was found. Scanned PDFs need OCR first.")
    return pages


def split_passages(pages: list[PageText], size: int = 700, overlap: int = 120) -> list[Passage]:
    """Prefer paragraph boundaries, retain overlap, and never cross PDF pages."""
    if size < 20 or not 0 <= overlap < size:
        raise ValueError("Invalid passage size or overlap.")
    result = []
    for page in pages:
        text = page.text
        cursor = 0
        while cursor < len(text):
            end = min(cursor + size, len(text))
            if end < len(text):
                paragraphs = [m.start() for m in re.finditer(r"\n\s*\n", text[cursor:end])]
                candidates = [b for b in paragraphs if b > size // 2]
                if not candidates:
                    boundaries = [m.start() for m in re.finditer(r"\s", text[cursor:end])]
                    candidates = [b for b in boundaries if b > size // 2]
                if candidates:
                    end = cursor + candidates[-1]
            raw = text[cursor:end]
            start = cursor + len(raw) - len(raw.lstrip())
            actual_end = end - (len(raw) - len(raw.rstrip()))
            if start < actual_end:
                result.append(
                    Passage(
                        text=text[start:actual_end],
                        page=page.page,
                        start=start,
                        end=actual_end,
                        line_start=text.count("\n", 0, start) + 1,
                        line_end=text.count("\n", 0, actual_end) + 1,
                    )
                )
            if end == len(text):
                break
            next_cursor = max(cursor + 1, end - overlap)
            # Avoid starting the overlap in the middle of a word.
            while next_cursor < end and not text[next_cursor].isspace():
                next_cursor += 1
            cursor = next_cursor
    return result
