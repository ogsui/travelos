"""Reviewed acknowledgement templates (task 011a).

No model writes these. A template only becomes usable after the owner has
reviewed the exact wording and set ``owner_approved=True``; until then the
service refuses to draft with it, even at A1, because an unreviewed template is
still unreviewed customer-facing text.

The shipped English acknowledgement is a neutral placeholder. Brand voice,
Hindi/Hinglish wording and the objection/FAQ playbook are owner inputs listed in
``docs/11_BUSINESS_RULES_INTERVIEW.md``; add reviewed templates instead of
letting a model invent tone.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

TemplateKind = Literal["acknowledgement"]
SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"en", "hi", "hinglish"})
DEFAULT_LANGUAGE: Final = "en"


class TemplateNotApprovedError(Exception):
    """The owner has not reviewed this template, so it cannot be drafted."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"template {key!r} is not owner-approved")


class TemplateNotFoundError(Exception):
    """No approved template exists for the requested kind and language."""

    def __init__(self, kind: str, language: str) -> None:
        self.kind = kind
        self.language = language
        super().__init__(f"no {kind} template for language {language!r}")


@dataclass(frozen=True, slots=True)
class MessageTemplate:
    """One reviewed customer-facing template.

    ``owner_approved`` is the owner's sign-off, not a technical flag: only the
    owner may set it after reviewing the exact body. ``language`` is ``en``,
    ``hi`` or ``hinglish``.
    """

    key: str
    kind: TemplateKind
    language: str
    body: str
    owner_approved: bool
    version: int = 1
    notes: str = ""


ACKNOWLEDGEMENT_TEMPLATES: Final[tuple[MessageTemplate, ...]] = (
    MessageTemplate(
        key="ack_received_en_v1",
        kind="acknowledgement",
        language="en",
        body=(
            "Namaste! Thank you for contacting us. We have received your enquiry "
            "and a travel consultant will review it and reply shortly."
        ),
        owner_approved=False,
        notes=(
            "TODO(owner): replace with the reviewed brand-voice acknowledgement "
            "and set owner_approved=True. Add hi/hinglish versions after review."
        ),
    ),
)


class TemplateRegistry:
    """Immutable template set with exact-language selection and a safe fallback."""

    def __init__(self, templates: Iterable[MessageTemplate]) -> None:
        items = tuple(templates)
        if not items:
            raise ValueError("a template registry needs at least one template")
        seen: set[str] = set()
        for template in items:
            self._validate(template)
            if template.key in seen:
                raise ValueError(f"duplicate template key: {template.key!r}")
            seen.add(template.key)
        self._templates = items

    @staticmethod
    def _validate(template: MessageTemplate) -> None:
        if not template.key.strip():
            raise ValueError("template key is required")
        if template.kind != "acknowledgement":
            raise ValueError(f"unsupported template kind: {template.kind!r}")
        if template.language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"unsupported template language: {template.language!r}")
        if not template.body.strip():
            raise ValueError(f"template {template.key!r} has an empty body")
        if template.version < 1:
            raise ValueError(f"template {template.key!r} has an invalid version")

    @property
    def templates(self) -> tuple[MessageTemplate, ...]:
        return self._templates

    def get(self, key: str) -> MessageTemplate:
        for template in self._templates:
            if template.key == key:
                return template
        raise TemplateNotFoundError("template", key)

    def acknowledgement(self, language: str) -> MessageTemplate:
        """Exact language first, then English; never a machine translation."""

        selected = language.strip().lower()
        if selected not in SUPPORTED_LANGUAGES:
            selected = DEFAULT_LANGUAGE
        for template in self._templates:
            if template.kind == "acknowledgement" and template.language == selected:
                return template
        for template in self._templates:
            if template.kind == "acknowledgement" and template.language == DEFAULT_LANGUAGE:
                return template
        raise TemplateNotFoundError("acknowledgement", selected)

    def require_approved(self, template: MessageTemplate) -> MessageTemplate:
        if not template.owner_approved:
            raise TemplateNotApprovedError(template.key)
        return template


def select_acknowledgement(templates: Sequence[MessageTemplate], language: str) -> MessageTemplate:
    """Convenience wrapper for callers that hold a plain sequence."""

    return TemplateRegistry(templates).acknowledgement(language)
