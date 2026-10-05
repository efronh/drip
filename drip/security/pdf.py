"""PDF text extraction. Anything we cannot read, we cannot inspect, so callers fail closed."""

import io
import logging
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logging.getLogger("pypdf").setLevel(logging.ERROR)

MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Extraction:
    text: str
    pages: int
    error: str | None = None    # "too_large" | "not_pdf" | "malformed" | "encrypted"


def extract_text(data: bytes) -> Extraction:
    if len(data) > MAX_BYTES:
        return Extraction("", 0, "too_large")
    if not data.lstrip()[:5].startswith(b"%PDF-"):
        return Extraction("", 0, "not_pdf")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            return Extraction("", len(reader.pages), "encrypted")
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError, IndexError, OSError):
        return Extraction("", 0, "malformed")
    return Extraction("\n".join(pages).strip(), len(pages))
