"""DatabaseFlagSource reads feature_flags rows and fails closed."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.features import DEFAULT_SCOPE, DatabaseFlagSource
from travelos.core.models import FeatureFlag

pytestmark = pytest.mark.integration


def make_source(db: Session) -> DatabaseFlagSource:
    return DatabaseFlagSource(sessionmaker(bind=db.get_bind()))


def lookup(source: DatabaseFlagSource) -> bool | None:
    return source.lookup(tenant_id=DEFAULT_TENANT_ID, key="email", scope=DEFAULT_SCOPE)


def add_flag(db: Session, value: dict[str, object]) -> None:
    db.add(FeatureFlag(key="email", scope=DEFAULT_SCOPE, value=value))
    db.flush()


def test_missing_row_returns_none(db: Session) -> None:
    assert lookup(make_source(db)) is None


def test_enabled_row_returns_true(db: Session) -> None:
    add_flag(db, {"enabled": True})

    assert lookup(make_source(db)) is True


def test_disabled_row_returns_false(db: Session) -> None:
    add_flag(db, {"enabled": False})

    assert lookup(make_source(db)) is False


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"enabled": "yes"},
        {"enabled": 1},
        {"enabled": None},
        {"other": True},
    ],
)
def test_malformed_row_fails_closed(db: Session, value: dict[str, object]) -> None:
    add_flag(db, value)

    assert lookup(make_source(db)) is False
