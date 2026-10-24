"""The action catalogue, autonomy levels and the ActionPolicy resolver.

``docs/05_APPROVAL_MATRIX.md`` is the human-readable source of truth. This
module encodes the same rows as code so the ceilings cannot be edited by a
config file, an API caller or a database writer. A database row may lower
autonomy (an owner can always be more cautious); it may never raise it above
``ActionSpec.max_level`` and may never unlock a LOCKED action.

Level meanings:
- A0: a human does it. The system may prepare data only -> ``deny``.
- A1: AI drafts, a human approves every instance -> ``needs_approval``.
- A2: AI acts, a human reviews after the fact -> ``auto`` with review.
- A3: AI acts autonomously -> ``auto``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from travelos.approval.errors import PolicyViolationError, UnknownActionError


class AutonomyLevel(StrEnum):
    """Autonomy levels, ordered A0 < A1 < A2 < A3."""

    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"

    @property
    def rank(self) -> int:
        return int(self.value[1])

    def one_below(self) -> AutonomyLevel:
        """The next lower level, clamped at A0."""

        return AutonomyLevel(f"A{max(0, self.rank - 1)}")


class Decision(StrEnum):
    """What the ActionPolicy layer allows right now."""

    AUTO = "auto"
    NEEDS_APPROVAL = "needs_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """One row of the approval matrix, as an enforceable ceiling."""

    action_type: str
    default_level: AutonomyLevel
    max_level: AutonomyLevel
    locked: bool = False
    owner_only: bool = False
    four_eyes: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if self.max_level.rank < self.default_level.rank:
            raise ValueError(f"{self.action_type}: max_level is below default_level")
        if self.locked and self.max_level is not self.default_level:
            raise ValueError(f"{self.action_type}: a LOCKED action must have max == default")

    @property
    def may_graduate(self) -> bool:
        return not self.locked and self.max_level.rank > self.default_level.rank


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The resolver's answer. Includes enough context to audit the decision."""

    action_type: str
    decision: Decision
    level: AutonomyLevel
    configured_level: AutonomyLevel
    max_level: AutonomyLevel
    locked: bool
    owner_only: bool
    requires_four_eyes: bool
    review_after: bool
    reason: str

    @property
    def may_act(self) -> bool:
        return self.decision is Decision.AUTO


def _a(
    action_type: str,
    default: str,
    maximum: str | None = None,
    *,
    locked: bool = False,
    owner_only: bool = False,
    four_eyes: bool = False,
    notes: str = "",
) -> ActionSpec:
    return ActionSpec(
        action_type=action_type,
        default_level=AutonomyLevel(default),
        max_level=AutonomyLevel(maximum or default),
        locked=locked,
        owner_only=owner_only,
        four_eyes=four_eyes,
        notes=notes,
    )


# Every action from docs/05_APPROVAL_MATRIX.md. Order follows the document.
_SPECS: tuple[ActionSpec, ...] = (
    # Internal, no customer effect.
    _a("crm.capture_lead", "A3", notes="Internal CRM writes only"),
    _a("inquiry.parse", "A3", notes="Result is shown to reviewers"),
    _a("supplier.search_package", "A3", notes="Internal supplier search"),
    _a("market.competitor_collection", "A3", notes="Per-site flag; internal"),
    _a("pricing.calculate", "A3", notes="Deterministic code; quote approval covers output"),
    _a("payment.record_webhook", "A3", notes="Signed gateway webhook, reconciled daily"),
    _a("booking.revalidate", "A3", notes="Blocks on tolerance breach"),
    _a("ads.auto_pause", "A3", notes="Safety action only"),
    _a("analytics.digest", "A3", notes="Reports and digests"),
    _a("campaign.send", "A3", notes="Only inside an approved envelope"),
    _a("billing.ledger_update", "A2", "A3", notes="Daily reconciliation"),
    _a("documents.send", "A2", notes="Consistency check and hold window first"),
    _a("postsales.review_request", "A2", "A3", notes="Consented contacts only"),
    # Customer-facing drafts that need per-instance approval.
    _a(
        "message.instant_ack",
        "A1",
        "A3",
        notes="A3 in live mode once the owner approves the template; A1 in shadow/test",
    ),
    _a("message.clarifying_questions", "A1", "A2", notes="Graduate after 95%/100 cases"),
    _a("message.sales_reply", "A1", "A2", notes="FAQ-type only may graduate"),
    _a("quote.discount", "A1", notes="Below floor is owner-only"),
    _a("quote.send", "A1", "A2", notes="Simple products under a value threshold"),
    _a("quote.follow_up", "A1", "A2", notes="Approved cadence templates"),
    _a("payment.create_link", "A1", "A2"),
    _a("billing.invoice_generation", "A1", "A2", notes="Accountant; gapless numbering"),
    _a("documents.visa_guidance", "A1", notes="Authoritative source, cited"),
    _a("support.disruption", "A1", notes="Urgent; pages staff immediately"),
    _a("support.faq", "A1", "A2"),
    _a("support.complaint", "A1", notes="Human leads complaints and disputes"),
    _a("postsales.occasion_message", "A1", "A2", notes="Consented contacts only"),
    _a("postsales.referral_reward", "A1"),
    _a("social.post_evergreen", "A1", "A2", notes="No price, offer or claim"),
    _a("social.post_priced", "A1", notes="Price accuracy check mandatory"),
    _a("social.reply", "A1", "A2", notes="Simple thanks/FAQ may graduate"),
    _a("seo.publish", "A1", notes="Fact-check and sources required"),
    _a("ads.campaign_edit", "A1"),
    _a("analytics.apply_recommendation", "A1"),
    _a("privacy.customer_export", "A1"),
    # LOCKED actions: catalog-fixed, cannot be raised or unlocked.
    _a("booking.confirm", "A1", locked=True, notes="LOCKED at A1; human ticketing"),
    _a(
        "booking.cancel_amend_refund",
        "A1",
        locked=True,
        notes="LOCKED; AI computes charges, a human executes",
    ),
    _a(
        "campaign.audience",
        "A1",
        locked=True,
        notes="LOCKED for new audiences",
    ),
    _a(
        "marketing.publish_testimonial",
        "A1",
        locked=True,
        notes="LOCKED; consent_ref required",
    ),
    _a(
        "privacy.customer_delete",
        "A1",
        locked=True,
        owner_only=True,
        notes="LOCKED; owner executes deletion",
    ),
    # A0: the system never performs these, even with an approval request.
    _a(
        "quote.below_floor",
        "A0",
        locked=True,
        owner_only=True,
        notes="LOCKED; owner-only pricing decision",
    ),
    _a(
        "payment.supplier_payment",
        "A0",
        locked=True,
        owner_only=True,
        four_eyes=True,
        notes="LOCKED; humans move money",
    ),
    _a(
        "billing.tax_filing",
        "A0",
        locked=True,
        owner_only=True,
        four_eyes=True,
        notes="LOCKED; AI prepares an export only",
    ),
    _a(
        "ads.budget_increase",
        "A0",
        locked=True,
        owner_only=True,
        notes="LOCKED; hard caps in code",
    ),
    _a(
        "outreach.cold_contact",
        "A0",
        locked=True,
        owner_only=True,
        notes="LOCKED OFF; owner enables per list with a legal basis",
    ),
    _a(
        "admin.change_autonomy",
        "A0",
        locked=True,
        owner_only=True,
        notes="LOCKED; owner-only policy change",
    ),
    _a(
        "admin.change_kill_switch",
        "A0",
        locked=True,
        owner_only=True,
        notes="LOCKED; owner-only, audited before/after",
    ),
)

CATALOG: Mapping[str, ActionSpec] = {spec.action_type: spec for spec in _SPECS}

if len(CATALOG) != len(_SPECS):  # pragma: no cover - import-time invariant
    raise RuntimeError("duplicate action type in the approval catalogue")


def get_spec(action_type: str) -> ActionSpec:
    """Return the spec or raise ``UnknownActionError`` (fail closed)."""

    try:
        return CATALOG[action_type]
    except KeyError as exc:
        raise UnknownActionError(action_type) from exc


def validate_level(spec: ActionSpec, level: AutonomyLevel) -> None:
    """Raise when an owner setting would cross a catalogue ceiling or lock."""

    if spec.locked and level is not spec.default_level:
        raise PolicyViolationError(
            spec.action_type,
            level.value,
            spec.default_level.value,
            "LOCKED action",
        )
    if level.rank > spec.max_level.rank:
        raise PolicyViolationError(
            spec.action_type,
            level.value,
            spec.max_level.value,
            "above maximum level",
        )


def effective_level(
    spec: ActionSpec,
    configured: AutonomyLevel | None,
) -> tuple[AutonomyLevel, bool]:
    """Clamp a stored level to the catalogue. Returns ``(level, was_clamped)``."""

    if spec.locked:
        return spec.default_level, configured is not None and configured is not spec.default_level
    candidate = configured or spec.default_level
    if candidate.rank > spec.max_level.rank:
        return spec.max_level, True
    return candidate, False


def resolve(
    action_type: str,
    *,
    level: AutonomyLevel | str | None = None,
    value_paise: int = 0,
    policy_requires_four_eyes: bool = False,
    threshold_paise: int = 0,
) -> PolicyDecision:
    """Resolve the action's effective decision.

    ``level`` is the owner's stored setting (``None`` means "catalogue
    default"). ``value_paise`` and ``threshold_paise`` decide four-eyes for
    high-value instances. A four-eyes requirement always turns an automatic
    decision into one that needs two humans, because two reviewers cannot be
    replaced by a machine.
    """

    if value_paise < 0 or threshold_paise < 0:
        raise ValueError("money values must not be negative")
    spec = get_spec(action_type)
    configured = AutonomyLevel(level) if isinstance(level, str) else level
    effective, clamped = effective_level(spec, configured)
    four_eyes = (
        spec.four_eyes
        or policy_requires_four_eyes
        or (threshold_paise > 0 and value_paise >= threshold_paise)
    )

    if effective is AutonomyLevel.A0:
        decision = Decision.DENY
        reason = "A0: a human performs this action"
    elif effective is AutonomyLevel.A1:
        decision = Decision.NEEDS_APPROVAL
        reason = "A1: a human approves every instance"
    elif four_eyes:
        decision = Decision.NEEDS_APPROVAL
        reason = "four-eyes: two distinct approvers are required"
    else:
        decision = Decision.AUTO
        reason = (
            "A2: acts, reviewed after the fact"
            if effective is AutonomyLevel.A2
            else "A3: acts autonomously"
        )

    if clamped:
        reason = f"{reason} (stored level clamped to catalogue ceiling)"

    return PolicyDecision(
        action_type=action_type,
        decision=decision,
        level=effective,
        configured_level=configured or spec.default_level,
        max_level=spec.max_level,
        locked=spec.locked,
        owner_only=spec.owner_only,
        requires_four_eyes=four_eyes,
        review_after=decision is Decision.AUTO and effective is AutonomyLevel.A2,
        reason=reason,
    )
