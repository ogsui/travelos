"""Record and read competitor price snapshots (task 014a2b1).

The store writes the adapters' ``CompetitorFetch`` results as one immutable row
per observation (or one row for a blocked/unavailable run) and reads back the
fresh observations for the same canonical query. It never scores comparability on
its own: when the caller has assessments they are stored with the rows, so the
score that justified a ceiling can be reviewed later.

There is no fetch, cache, throttle or portal behaviour here. Collection policy is
task 014a2b2; portal automation is 014b and remains blocked on the owner's
per-site authorization.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.market.adapters import CompetitorFetch
from travelos.market.errors import MarketValidationError
from travelos.market.models import (
    ComparabilityAssessment,
    ComparabilityBasis,
    CompetitorPrice,
    FetchStatus,
    ObservationStatus,
)
from travelos.market.query import query_hash
from travelos.market.records import CompetitorSnapshot

DEFAULT_MAX_AGE = timedelta(hours=24)


class CompetitorSnapshotStore:
    """Writes and reads competitor snapshots for one tenant."""

    def __init__(
        self,
        db: Session,
        *,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        audit: AuditWriter | None = None,
    ) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._audit = audit or AuditWriter()

    def record(
        self,
        basis: ComparabilityBasis,
        fetch: CompetitorFetch,
        *,
        assessments: Sequence[ComparabilityAssessment] | None = None,
        actor: Actor | None = None,
    ) -> tuple[CompetitorSnapshot, ...]:
        """Store one adapter run; the caller owns the transaction.

        ``assessments`` is optional and, when present, must match the fetch's
        observations one-for-one in the same order.
        """

        digest = query_hash(basis)
        basis_payload = basis.model_dump(mode="json")
        scores = self._scores(fetch, assessments)

        rows: list[CompetitorSnapshot] = []
        if fetch.status is FetchStatus.OK:
            for index, observation in enumerate(fetch.observations):
                self._check_observation(fetch, observation)
                rows.append(
                    CompetitorSnapshot(
                        tenant_id=self._tenant_id,
                        site=observation.site,
                        query_hash=digest,
                        status=observation.status.value,
                        price_paise=observation.price_paise,
                        currency=observation.currency,
                        comparability_score=scores[index] if scores is not None else None,
                        fetched_at=observation.fetched_at,
                        payload=observation.model_dump(mode="json"),
                        basis=basis_payload,
                    )
                )
        else:
            rows.append(
                CompetitorSnapshot(
                    tenant_id=self._tenant_id,
                    site=fetch.site,
                    query_hash=digest,
                    status=fetch.status.value,
                    fetched_at=fetch.fetched_at,
                    detail=fetch.detail or None,
                    payload={
                        "site": fetch.site,
                        "status": fetch.status.value,
                        "detail": fetch.detail,
                    },
                    basis=basis_payload,
                )
            )

        self._db.add_all(rows)
        self._db.flush()
        self._audit.record(
            self._db,
            action="market.snapshot.recorded",
            actor=actor or Actor.system(),
            subject_type="competitor_snapshot",
            after={
                "site": fetch.site,
                "query_hash": digest,
                "status": fetch.status.value,
                "observation_count": len(fetch.observations),
            },
        )
        return tuple(rows)

    def latest(
        self,
        basis: ComparabilityBasis,
        *,
        now: datetime,
        max_age: timedelta = DEFAULT_MAX_AGE,
    ) -> tuple[CompetitorPrice, ...] | None:
        """Fresh observations for this query, or ``None`` when there are none.

        A row is fresh when its own fetch time is within ``max_age`` of ``now``.
        Freshness is per observation, not per run: a manual entry typed at 09:00
        and another at 09:10 are both usable at 10:00, and the comparability step
        still decides whether each one may set the ceiling.
        """

        moment = _aware_utc(now)
        if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
            raise MarketValidationError("max_age must be a positive timedelta")

        rows = (
            self._db.execute(
                select(CompetitorSnapshot)
                .where(
                    CompetitorSnapshot.tenant_id == self._tenant_id,
                    CompetitorSnapshot.query_hash == query_hash(basis),
                    CompetitorSnapshot.status == FetchStatus.OK.value,
                    CompetitorSnapshot.fetched_at >= moment - max_age,
                )
                .order_by(
                    CompetitorSnapshot.site,
                    CompetitorSnapshot.price_paise,
                    CompetitorSnapshot.fetched_at,
                    CompetitorSnapshot.id,
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None
        return tuple(CompetitorPrice.model_validate(row.payload) for row in rows)

    @staticmethod
    def _scores(
        fetch: CompetitorFetch,
        assessments: Sequence[ComparabilityAssessment] | None,
    ) -> tuple[int, ...] | None:
        if assessments is None:
            return None
        if fetch.status is not FetchStatus.OK:
            raise MarketValidationError("a failed fetch cannot carry assessments")
        if len(assessments) != len(fetch.observations):
            raise MarketValidationError(
                "assessments must match observations one-for-one "
                f"(got {len(assessments)} for {len(fetch.observations)})"
            )
        return tuple(assessment.score for assessment in assessments)

    @staticmethod
    def _check_observation(fetch: CompetitorFetch, observation: CompetitorPrice) -> None:
        if observation.site != fetch.site:
            raise MarketValidationError(
                f"observation site {observation.site!r} does not match fetch site {fetch.site!r}"
            )
        if observation.status is not ObservationStatus.OK:
            raise MarketValidationError(
                "an ok fetch can only carry ok observations; "
                f"got status {observation.status.value!r}"
            )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)
