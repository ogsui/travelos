"""Approval engine and autonomy policies (task 005).

Any module asks this package whether it may act, and if not, raises an
approval request that a human decides. The catalogue in :mod:`matrix` is the
authoritative list of actions and their hard ceilings; owner settings in
``autonomy_policies`` can only lower autonomy, never raise it past the cap or
unlock a LOCKED action.
"""

from travelos.approval.matrix import (
    CATALOG,
    ActionSpec,
    AutonomyLevel,
    Decision,
    PolicyDecision,
    get_spec,
    resolve,
)
from travelos.approval.service import (
    ApprovalService,
    AutonomyService,
    Demotion,
    SeedReport,
    Submission,
)

__all__ = [
    "CATALOG",
    "ActionSpec",
    "ApprovalService",
    "AutonomyLevel",
    "AutonomyService",
    "Decision",
    "Demotion",
    "PolicyDecision",
    "SeedReport",
    "Submission",
    "get_spec",
    "resolve",
]
