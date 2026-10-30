"""Persisted competitor prices and their append-only history (task 014a2b1).

A snapshot row is one observation a site showed at one time, tagged with the
canonical hash of the product it answers. Nothing is updated or deleted: a
correction is a newer row with a newer fetch time. A blocked or unavailable run
is stored too, so "this site had no price" is visible later instead of looking
like a site that was never asked.

The store never converts currency, never scores comparability by itself and never
lets a row from another site or tenant leak into a query.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from travelos.core.errors import AppendOnlyError
from travelos.core.models import AuditLog
from travelos.market.adapters import CompetitorFetch
from travelos.market.comparability import assess_comparability
from travelos.market.errors import MarketValidationError
from travelos.market.models import (
    ComparabilityBasis,
    CompetitorPrice,
    FetchStatus,
    ObservationStatus,
)
from travelos.market.records import CompetitorSnapshot
from travelos.market.store import CompetitorSnapshotStore

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
START = date(2027, 2, 10)
END = date(2027, 2, 13)


def basis(**overrides: Any) -> ComparabilityBasis:
    values: dict[str, Any] = {
        "product_type": "flight",
        "route": "Delhi-Goa",
        "start_date": START,
        "end_date": END,
        "adults": 2,
        "children": 0,
        "infants": 0,
        "fare_class": "economy",
        "checked_bags": 1,
        "refundable": True,
        "currency": "INR",
    }
    values.update(overrides)
    return ComparabilityBasis.model_validate(values)


def price(**overrides: Any) -> CompetitorPrice:
    values: dict[str, Any] = {
        "site": "SiteA",
        "product_type": "flight",
        "route": "Delhi-Goa",
        "start_date": START,
        "end_date": END,
        "adults": 2,
        "fare_class": "economy",
        "checked_bags": 1,
        "refundable": True,
        "price_paise": 4_500_000,
        "currency": "INR",
        "fetched_at": NOW - timedelta(minutes=5),
        "status": ObservationStatus.OK,
        "source_ref": "manual/entry-1",
    }
    values.update(overrides)
    return CompetitorPrice.model_validate(values)


def fetch(
    *,
    site: str = "SiteA",
    status: FetchStatus = FetchStatus.OK,
    observations: tuple[CompetitorPrice, ...] | None = None,
    detail: str = "",
    at: datetime = NOW,
) -> CompetitorFetch:
    return CompetitorFetch(
        site=site,
        status=status,
        fetched_at=at,
        observations=observations if observations is not None else (price(),),
        detail=detail,
    )


def test_records_one_row_per_observation_and_reads_them_back(db) -> None:
    store = CompetitorSnapshotStore(db)

    rows = store.record(
        basis(),
        fetch(
            observations=(
                price(site="SiteA", price_paise=4_500_000),
                price(site="SiteA", price_paise=4_600_000, source_ref="manual/entry-2"),
            )
        ),
    )

    assert len(rows) == 2
    assert {row.price_paise for row in rows} == {4_500_000, 4_600_000}
    assert all(row.status == "ok" for row in rows)

    latest = store.latest(basis(), now=NOW, max_age=timedelta(hours=24))
    assert latest is not None
    assert [item.price_paise for item in latest] == [4_500_000, 4_600_000]
    assert [item.source_ref for item in latest] == ["manual/entry-1", "manual/entry-2"]


def test_a_snapshot_keeps_its_basis_and_payload(db) -> None:
    store = CompetitorSnapshotStore(db)
    observed = price(fetched_at=NOW - timedelta(minutes=5))

    row = store.record(basis(), fetch(observations=(observed,)))[0]

    assert row.basis == basis().model_dump(mode="json")
    assert row.site == "SiteA"
    assert row.currency == "INR"
    assert CompetitorPrice.model_validate(row.payload) == observed


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (FetchStatus.BLOCKED, "the portal returned a block"),
        (FetchStatus.UNAVAILABLE, "adapter failed (RuntimeError)"),
    ],
)
def test_a_failed_run_is_stored_without_a_price(db, status, detail) -> None:
    store = CompetitorSnapshotStore(db)

    rows = store.record(
        basis(),
        fetch(status=status, observations=(), detail=detail),
    )

    assert len(rows) == 1
    assert rows[0].status == status.value
    assert rows[0].price_paise is None
    assert rows[0].currency is None
    assert rows[0].payload == {"site": "SiteA", "status": status.value, "detail": detail}
    assert store.latest(basis(), now=NOW, max_age=timedelta(hours=24)) is None


def test_comparability_scores_are_stored_per_observation(db) -> None:
    store = CompetitorSnapshotStore(db)
    exact = price()
    unknown_fare = price(fare_class=None, source_ref="manual/entry-2")
    assessments = (
        assess_comparability(basis(), exact, now=NOW),
        assess_comparability(basis(), unknown_fare, now=NOW),
    )

    rows = store.record(
        basis(),
        fetch(observations=(exact, unknown_fare)),
        assessments=assessments,
    )

    assert [row.comparability_score for row in rows] == [100, 90]
    assert all(row.comparability_score is not None for row in rows)


def test_assessment_count_must_match_the_observations(db) -> None:
    store = CompetitorSnapshotStore(db)
    exact = price()
    assessment = assess_comparability(basis(), exact, now=NOW)

    with pytest.raises(MarketValidationError, match="assessment"):
        store.record(
            basis(),
            fetch(observations=(exact, price(source_ref="manual/2"))),
            assessments=(assessment,),
        )


def test_an_observation_for_another_site_is_refused(db) -> None:
    with pytest.raises(MarketValidationError, match="site"):
        CompetitorSnapshotStore(db).record(
            basis(),
            fetch(site="SiteA", observations=(price(site="SiteB"),)),
        )


def test_an_ok_fetch_cannot_carry_a_blocked_observation(db) -> None:
    with pytest.raises(MarketValidationError, match="status"):
        CompetitorSnapshotStore(db).record(
            basis(),
            fetch(
                status=FetchStatus.OK,
                observations=(price(status=ObservationStatus.BLOCKED),),
            ),
        )


def test_latest_ignores_observations_older_than_the_maximum_age(db) -> None:
    store = CompetitorSnapshotStore(db)
    old = NOW - timedelta(hours=2)
    store.record(basis(), fetch(at=old, observations=(price(fetched_at=old),)))

    assert store.latest(basis(), now=NOW, max_age=timedelta(hours=1)) is None
    fresh = store.latest(basis(), now=NOW, max_age=timedelta(hours=3))
    assert fresh is not None
    assert len(fresh) == 1


def test_latest_is_scoped_by_tenant(db) -> None:
    store = CompetitorSnapshotStore(db)
    store.record(basis(), fetch())

    other = CompetitorSnapshotStore(db, tenant_id=uuid4())
    assert other.latest(basis(), now=NOW, max_age=timedelta(hours=24)) is None


def test_latest_is_scoped_by_the_canonical_query(db) -> None:
    store = CompetitorSnapshotStore(db)
    store.record(basis(), fetch())

    assert store.latest(basis(route="Mumbai-Goa"), now=NOW, max_age=timedelta(hours=24)) is None


def test_latest_orders_observations_by_site_then_price(db) -> None:
    store = CompetitorSnapshotStore(db)
    store.record(
        basis(),
        fetch(
            site="SiteB",
            observations=(price(site="SiteB", price_paise=200_000),),
        ),
    )
    store.record(
        basis(),
        fetch(
            site="SiteA",
            observations=(price(site="SiteA", price_paise=300_000),),
        ),
    )

    latest = store.latest(basis(), now=NOW, max_age=timedelta(hours=24))

    assert latest is not None
    assert [(item.site, item.price_paise) for item in latest] == [
        ("SiteA", 300_000),
        ("SiteB", 200_000),
    ]


def test_latest_needs_a_positive_age_and_an_aware_clock(db) -> None:
    store = CompetitorSnapshotStore(db)

    with pytest.raises(MarketValidationError, match="max_age"):
        store.latest(basis(), now=NOW, max_age=timedelta(0))

    with pytest.raises(ValueError, match="timezone-aware"):
        store.latest(basis(), now=datetime(2026, 11, 1, 10, 0), max_age=timedelta(hours=1))


def test_recording_writes_one_audit_entry_per_run(db) -> None:
    store = CompetitorSnapshotStore(db)

    store.record(
        basis(),
        fetch(
            observations=(
                price(price_paise=4_500_000),
                price(price_paise=4_600_000, source_ref="manual/2"),
            )
        ),
    )
    db.flush()

    entries = list(
        db.execute(select(AuditLog).where(AuditLog.action == "market.snapshot.recorded")).scalars()
    )
    assert len(entries) == 1
    assert entries[0].subject_type == "competitor_snapshot"
    assert entries[0].after == {
        "site": "SiteA",
        "query_hash": entries[0].after["query_hash"],
        "status": "ok",
        "observation_count": 2,
    }


def test_snapshots_cannot_be_updated_through_the_orm(db) -> None:
    row = CompetitorSnapshotStore(db).record(basis(), fetch())[0]

    row.price_paise = 1
    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()


def test_snapshots_cannot_be_deleted_through_the_orm(db) -> None:
    row = CompetitorSnapshotStore(db).record(basis(), fetch())[0]
    stored = db.execute(
        select(CompetitorSnapshot).where(CompetitorSnapshot.id == row.id)
    ).scalar_one()

    db.delete(stored)
    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()


def test_snapshots_cannot_be_updated_in_raw_sql(db) -> None:
    store = CompetitorSnapshotStore(db)
    store.record(basis(), fetch())

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE competitor_snapshots SET site = 'Hacked'"))
