"""Synthetic data seeding lives here (task 007 adds data generators).

Task 005 seeds the autonomy policy catalogue: every action from
docs/05_APPROVAL_MATRIX.md gets a row at its catalogue default, and any row
above its ceiling (or any LOCKED row that differs from its default) is
repaired. Existing safer owner changes are left alone. Live mode is refused.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError

from travelos.approval.service import AutonomyService, SeedReport
from travelos.core.config import Mode, Settings, get_settings
from travelos.core.db import build_engine, build_session_factory, session_scope
from travelos.core.logging import configure_logging

logger = logging.getLogger("travelos.admin.seed")


def seed_autonomy_policies(settings: Settings) -> SeedReport:
    """Apply the idempotent policy seed against the configured database."""

    engine = build_engine(settings.effective_database_url)
    try:
        factory = build_session_factory(engine)
        with session_scope(factory) as db:
            return AutonomyService().seed(db)
    finally:
        engine.dispose()


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    if settings.mode is Mode.LIVE:
        logger.error("seed refused: synthetic data must never be loaded in live mode")
        return 1

    try:
        report = seed_autonomy_policies(settings)
    except SQLAlchemyError as exc:
        logger.warning("seed: database unavailable; policies were not seeded (%s)", exc)
        return 0

    logger.info(
        "seed: autonomy policies inserted=%s repaired=%s; task 007 adds the synthetic dataset",
        report.inserted,
        report.repaired,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
