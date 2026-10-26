"""Sales conversation drafting (task 011).

The first slice drafts customer replies with reviewed templates and a claim guard.
It never calls an LLM, never sends a message and never states a price,
availability or policy claim without a source. Every draft is stored encrypted
and submitted for approval.
"""

from travelos.conversation.guard import (
    Claim,
    ClaimKind,
    GuardReport,
    UnsupportedClaimError,
    scan_reply,
)
from travelos.conversation.service import ReplyDraft, SalesReplyService
from travelos.conversation.templates import (
    ACKNOWLEDGEMENT_TEMPLATES,
    MessageTemplate,
    TemplateNotApprovedError,
    TemplateRegistry,
)

__all__ = [
    "ACKNOWLEDGEMENT_TEMPLATES",
    "Claim",
    "ClaimKind",
    "GuardReport",
    "MessageTemplate",
    "ReplyDraft",
    "SalesReplyService",
    "TemplateNotApprovedError",
    "TemplateRegistry",
    "UnsupportedClaimError",
    "scan_reply",
]
