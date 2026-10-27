"""Synthetic data seeding.

Task 005 seeds the autonomy policy catalogue: every action from
docs/05_APPROVAL_MATRIX.md gets a row at its catalogue default, and any row
above its ceiling (or any LOCKED row that differs from its default) is
repaired. Existing safer owner changes are left alone.

Task 007 generates the deterministic dataset, writes it under ``data/`` and
loads the CRM portion into PostgreSQL. Writing the files happens before the
database work, so a developer without PostgreSQL still gets usable fixtures.
Live mode is refused before any file or database write.
"""

import logging
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from travelos.approval.service import AutonomyService, SeedReport
from travelos.core.config import Mode, Settings, get_settings
from travelos.core.db import build_engine, build_session_factory, session_scope
from travelos.core.logging import configure_logging
from travelos.synthetic import generate_dataset, write_dataset
from travelos.synthetic.generator import SyntheticDataset
from travelos.synthetic.loader import SeedCrmReport, seed_crm

logger = logging.getLogger("travelos.admin.seed")

REPO_ROOT = Path(__file__).resolve().parents[3]


def data_root(settings: Settings) -> Path:
    """Repository ``data/`` by default; ``DATA_DIR`` when set (containers)."""

    if settings.data_dir:
        return Path(settings.data_dir)
    return REPO_ROOT / "data"


def seed_autonomy_policies(settings: Settings) -> SeedReport:
    """Apply the idempotent policy seed against the configured database."""

    engine = build_engine(settings.effective_database_url, connect_timeout=5)
    try:
        factory = build_session_factory(engine)
        with session_scope(factory) as db:
            return AutonomyService().seed(db)
    finally:
        engine.dispose()


def seed_synthetic_crm(settings: Settings, dataset: SyntheticDataset) -> SeedCrmReport:
    """Load the CRM rows from ``dataset`` into the configured database."""

    engine = build_engine(settings.effective_database_url, connect_timeout=5)
    try:
        factory = build_session_factory(engine)
        with session_scope(factory) as db:
            return seed_crm(db, dataset)
    finally:
        engine.dispose()


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    if settings.mode is not Mode.TEST:
        logger.error("seed refused: synthetic data may only be loaded in MODE=test")
        return 1

    dataset = generate_dataset(settings.synthetic_seed)
    root = data_root(settings)
    try:
        manifest = write_dataset(
            dataset,
            root / "synthetic",
            eval_directory=root / "eval",
        )
    except OSError as exc:
        logger.error("seed: could not write synthetic fixtures: %s", exc)
        return 1
    logger.info(
        "seed: wrote %s dataset files (seed=%s, generator_version=%s)",
        len(manifest["files"]),
        dataset.seed,
        dataset.generator_version,
    )

    try:
        report = seed_autonomy_policies(settings)
        crm_report = seed_synthetic_crm(settings, dataset)
    except SQLAlchemyError as exc:
        logger.warning(
            "seed: database unavailable; fixtures were written but policies and CRM "
            "rows were not seeded (%s)",
            exc,
        )
        return 0

    logger.info(
        "seed: autonomy policies inserted=%s repaired=%s; CRM inserted=%s",
        report.inserted,
        report.repaired,
        crm_report.total,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
