"""Render stored quotes into customer documents (task 016b2b).

The service joins three things: the owner's presentation rules, the stored quote
and the pure renderer. It never calculates a price, chooses a revision, sends
anything or stores a document. HTML is returned to the caller; PDF bytes are
returned when the optional adapter is installed, and are never written anywhere
because revisions are append-only and no object store is configured.

Every render writes one append-only ``quote.rendered`` audit record naming the
revision numbers and the presented source hash, so the document can be traced to
the exact rows and owner wording it was built from. No customer contact data is
copied into the audit.

The caller owns the transaction; the service flushes the audit row so it exists
inside that transaction. Rendering is a read plus an audit trace, not a state
change and not a send: approval, consent and channel gates are 016c.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.quotes.documents import build_group_document, build_quote_document
from travelos.quotes.errors import QuotePresentationError
from travelos.quotes.pdf import render_pdf
from travelos.quotes.presentation import (
    DEFAULT_PRESENTATION_PATH,
    PresentationLoadResult,
    QuotePresentation,
    load_quote_presentation,
)
from travelos.quotes.render import QuoteDocument, render_quote_html


@dataclass(frozen=True, slots=True)
class RenderedQuote:
    """One rendered document; ``pdf`` is present only when it was requested."""

    document: QuoteDocument
    html: str
    pdf: bytes | None = None


class QuoteRenderer:
    """Builds and renders customer documents for one tenant."""

    def __init__(
        self,
        db: Session,
        *,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        audit: AuditWriter | None = None,
        presentation_path: Path | str = DEFAULT_PRESENTATION_PATH,
        pdf_renderer: Callable[[str], bytes] = render_pdf,
    ) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._audit = audit or AuditWriter()
        self._presentation_path = presentation_path
        self._pdf_renderer = pdf_renderer

    def render_quote(
        self,
        quote_id: UUID,
        *,
        now: datetime,
        actor: Actor | None = None,
        as_pdf: bool = False,
    ) -> RenderedQuote:
        presentation, source_hash = self._presentation()
        document = build_quote_document(
            self._db,
            quote_id,
            presentation=presentation,
            now=now,
            tenant_id=self._tenant_id,
        )
        return self._finish(
            document,
            subject_type="quote",
            actor=actor,
            as_pdf=as_pdf,
            source_hash=source_hash,
        )

    def render_group(
        self,
        group_id: UUID,
        *,
        now: datetime,
        actor: Actor | None = None,
        as_pdf: bool = False,
    ) -> RenderedQuote:
        presentation, source_hash = self._presentation()
        document = build_group_document(
            self._db,
            group_id,
            presentation=presentation,
            now=now,
            tenant_id=self._tenant_id,
        )
        return self._finish(
            document,
            subject_type="quote_group",
            actor=actor,
            as_pdf=as_pdf,
            source_hash=source_hash,
        )

    def _presentation(self) -> tuple[QuotePresentation, str]:
        result = load_quote_presentation(self._presentation_path)
        if result.needs_config or result.presentation is None:
            raise QuotePresentationError(_gap_message(result))
        if result.source_hash is None:  # pragma: no cover - OK always carries a hash
            raise QuotePresentationError("quote presentation has no source hash")
        return result.presentation, result.source_hash

    def _finish(
        self,
        document: QuoteDocument,
        *,
        subject_type: str,
        actor: Actor | None,
        as_pdf: bool,
        source_hash: str,
    ) -> RenderedQuote:
        html_text = render_quote_html(document)
        pdf = self._pdf_renderer(html_text) if as_pdf else None
        self._audit.record(
            self._db,
            action="quote.rendered",
            actor=actor or Actor.system(),
            subject_type=subject_type,
            subject_id=document.id,
            after={
                "kind": document.kind,
                "option_count": len(document.options),
                "revision_numbers": [option.revision_number for option in document.options],
                "total_paise": [option.total.paise for option in document.options],
                "expires_at": document.expires_at,
                "format": "pdf" if as_pdf else "html",
                "presentation_hash": source_hash,
            },
        )
        self._db.flush()
        return RenderedQuote(document=document, html=html_text, pdf=pdf)


def require_presentation(path: Path | str = DEFAULT_PRESENTATION_PATH) -> QuotePresentation:
    """Load the owner's presentation rules, refusing an incomplete config.

    Sending needs the same fail-closed check as rendering: a missing brand,
    terms version or payment schedule must stop the pipeline rather than produce
    invented wording. ``NEEDS_CONFIG`` gap fields are named in the message.
    """

    result = load_quote_presentation(path)
    if result.needs_config or result.presentation is None:
        raise QuotePresentationError(_gap_message(result))
    return result.presentation


def _gap_message(result: PresentationLoadResult) -> str:
    fields = ", ".join(gap.field for gap in result.gaps[:5])
    hidden = len(result.gaps) - 5
    more = f" (+{hidden} more)" if hidden > 0 else ""
    return f"quote presentation needs owner configuration: {fields}{more}"


__all__ = ["QuoteRenderer", "RenderedQuote", "require_presentation"]
