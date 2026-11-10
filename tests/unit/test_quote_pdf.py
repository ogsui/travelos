"""PDF adapter (task 016b2b).

WeasyPrint is an optional extra. These tests use a fake module so the suite needs
no rendering engine, and pin the two failure modes: an absent adapter and a
response that is not a PDF.
"""

from __future__ import annotations

import sys
import types

import pytest

from travelos.quotes.errors import QuoteDocumentError, QuotePdfUnavailableError
from travelos.quotes.pdf import render_pdf

HTML_TEXT = "<html><body><p>Synthetic</p></body></html>"


def test_pdf_rendering_without_weasyprint_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "weasyprint", None)

    with pytest.raises(QuotePdfUnavailableError, match="pdf"):
        render_pdf(HTML_TEXT)


def test_pdf_rendering_passes_the_html_string_to_weasyprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    class FakeHTML:
        def __init__(self, *, string: str) -> None:
            seen.append(string)

        def write_pdf(self) -> bytes:
            return b"%PDF-1.7 synthetic"

    module = types.ModuleType("weasyprint")
    module.HTML = FakeHTML  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "weasyprint", module)

    assert render_pdf(HTML_TEXT) == b"%PDF-1.7 synthetic"
    assert seen == [HTML_TEXT]


def test_a_response_that_is_not_a_pdf_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeHTML:
        def __init__(self, *, string: str) -> None:
            del string

        def write_pdf(self) -> bytes:
            return b""

    module = types.ModuleType("weasyprint")
    module.HTML = FakeHTML  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "weasyprint", module)

    with pytest.raises(QuoteDocumentError, match="PDF"):
        render_pdf(HTML_TEXT)
