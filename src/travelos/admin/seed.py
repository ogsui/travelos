"""Synthetic data seeding lives here (task 007 adds generators)."""

import logging

from travelos.core.config import Mode, get_settings
from travelos.core.logging import configure_logging

logger = logging.getLogger("travelos.admin.seed")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    if settings.mode is Mode.LIVE:
        logger.error("seed refused: synthetic data must never be loaded in live mode")
        return 1

    logger.info("seed: no generators yet; task 007 adds the synthetic dataset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
