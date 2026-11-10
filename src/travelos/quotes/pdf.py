"""Lazy WeasyPrint PDF adapter (task 016b2b).

WeasyPrint is an optional extra: the HTML renderer must work in a plain
installation, and a missing PDF engine must fail with a clear error instead of an
import traceback at module load. Nothing here persists a PDF.
"""

from __future__ import annotations

from travelos.quotes.errors import QuoteDocumentError, QuotePdfUnavailableError

_PDF_MAGIC = b"%PDF"


def render_pdf(html_text: str) -> bytes:
    """Turn one self-contained HTML document into PDF bytes.

    The HTML is passed as a string, never a file or URL, so there is no base URL
    to resolve and no local path can be read.
    """

    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise QuotePdfUnavailableError(
            "PDF rendering needs the optional 'pdf' extra (WeasyPrint)"
        ) from exc

    data: bytes | None = HTML(string=html_text).write_pdf()
    if not data or not data.startswith(_PDF_MAGIC):
        raise QuoteDocumentError("WeasyPrint did not return a PDF document")
    return data


__all__ = ["render_pdf"]
