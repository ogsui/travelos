"""Sales conversation drafting and human handoff (task 011).

The module drafts customer replies with reviewed templates and a claim guard,
and decides when a conversation must go to a human. It never calls an LLM,
never sends a message and never states a price, availability or policy claim
without a source. Every draft is stored encrypted and submitted for approval;
every handoff creates a dashboard task with an encrypted summary.
"""

from travelos.conversation.guard import (
    Claim,
    ClaimKind,
    GuardReport,
    UnsupportedClaimError,
    scan_reply,
)
from travelos.conversation.handoff import (
    ConversationHandoffService,
    HandoffAssessment,
    HandoffResult,
)
from travelos.conversation.models import ConversationHandoff
from travelos.conversation.service import ReplyDraft, SalesReplyService
from travelos.conversation.summary import ConversationFacts, build_summary
from travelos.conversation.templates import (
    ACKNOWLEDGEMENT_TEMPLATES,
    MessageTemplate,
    TemplateNotApprovedError,
    TemplateRegistry,
)
from travelos.conversation.triggers import (
    HandoffPolicy,
    HandoffTrigger,
    TriggerEvidence,
    detect_triggers,
)

__all__ = [
    "ACKNOWLEDGEMENT_TEMPLATES",
    "Claim",
    "ClaimKind",
    "ConversationFacts",
    "ConversationHandoff",
    "ConversationHandoffService",
    "GuardReport",
    "HandoffAssessment",
    "HandoffPolicy",
    "HandoffResult",
    "HandoffTrigger",
    "MessageTemplate",
    "ReplyDraft",
    "SalesReplyService",
    "TemplateNotApprovedError",
    "TemplateRegistry",
    "TriggerEvidence",
    "UnsupportedClaimError",
    "build_summary",
    "detect_triggers",
    "scan_reply",
]
