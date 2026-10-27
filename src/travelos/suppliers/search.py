"""Parallel supplier search with per-supplier timeouts and a circuit breaker.

Each supplier adapter is called on its own worker thread. The caller's database
session is used only on the calling thread: workers return immutable models and
the store/audit writes happen after each future resolves. A slow or broken
supplier therefore cannot block the others or corrupt the session.

The orchestration timeout is a backstop, not a cancellation API: Python threads
cannot be killed. A portal adapter must also enforce its own page/socket
deadline. A timed-out future is abandoned (``shutdown(wait=False)``) and its
supplier is marked failed.

Search results are persisted as immutable ``price_snapshots``. An unexpired
snapshot for the same normalized query is reused when ``use_cache`` is true, so
a repeated search does not hammer a portal. The query hash and the snapshot TTL
are the only cache keys; no customer data is part of them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.circuit import CircuitBreaker
from travelos.core.clock import Clock, SystemClock
from travelos.core.config import Settings
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.suppliers.adapter import SupplierAdapter
from travelos.suppliers.errors import (
    SupplierError,
    SupplierTimeoutError,
    SupplierUnavailableError,
    SupplierValidationError,
)
from travelos.suppliers.models import (
    FailureCode,
    SearchFailure,
    SearchResult,
    SupplierResult,
    SupplierSearchOutcome,
    SupplierSearchQuery,
)
from travelos.suppliers.records import PriceSnapshot, Supplier, SupplierSearch
from travelos.suppliers.store import SnapshotStore, SupplierCatalog

logger = logging.getLogger("travelos.suppliers.search")

_CIRCUIT_OPEN_MESSAGE = "{supplier} circuit is open"


def request_hash(query: SupplierSearchQuery) -> str:
    """Stable sha256 of the normalized query, independent of dict order."""

    canonical = json.dumps(
        query.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SupplierSearchService:
    """Runs one bounded search across a fixed set of supplier adapters."""

    def __init__(
        self,
        adapters: tuple[SupplierAdapter, ...] | list[SupplierAdapter],
        *,
        clock: Clock | None = None,
        timeout: timedelta = timedelta(seconds=20),
        circuit_failure_threshold: int = 3,
        circuit_reset_after: timedelta = timedelta(seconds=60),
        snapshot_ttl: timedelta = timedelta(minutes=10),
        max_workers: int = 8,
        audit: AuditWriter | None = None,
    ) -> None:
        if not adapters:
            raise SupplierValidationError("at least one supplier adapter is required")
        if not isinstance(timeout, timedelta) or timeout <= timedelta(0):
            raise SupplierValidationError("timeout must be a positive timedelta")
        if not isinstance(circuit_reset_after, timedelta) or circuit_reset_after <= timedelta(0):
            raise SupplierValidationError("circuit reset must be a positive timedelta")
        if not isinstance(snapshot_ttl, timedelta) or snapshot_ttl <= timedelta(0):
            raise SupplierValidationError("snapshot ttl must be a positive timedelta")
        if circuit_failure_threshold < 1:
            raise SupplierValidationError("circuit failure threshold must be >= 1")
        if max_workers < 1:
            raise SupplierValidationError("max_workers must be >= 1")

        names = [adapter.name for adapter in adapters]
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise SupplierValidationError("every supplier adapter needs a non-empty name")
        if len(set(names)) != len(names):
            raise SupplierValidationError("supplier names must be unique within a search")

        self._adapters: tuple[SupplierAdapter, ...] = tuple(
            sorted(adapters, key=lambda adapter: adapter.name)
        )
        self._clock = clock or SystemClock()
        self._timeout = timeout
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_reset_after = circuit_reset_after
        self._snapshot_ttl = snapshot_ttl
        self._max_workers = max_workers
        self._audit = audit or AuditWriter()
        self._breakers: dict[str, CircuitBreaker] = {}

    @classmethod
    def from_settings(
        cls,
        adapters: tuple[SupplierAdapter, ...] | list[SupplierAdapter],
        *,
        settings: Settings,
        clock: Clock | None = None,
        audit: AuditWriter | None = None,
    ) -> SupplierSearchService:
        return cls(
            adapters,
            clock=clock,
            timeout=timedelta(seconds=settings.supplier_search_timeout_seconds),
            circuit_failure_threshold=settings.supplier_circuit_failure_threshold,
            circuit_reset_after=timedelta(seconds=settings.supplier_circuit_reset_seconds),
            snapshot_ttl=timedelta(seconds=settings.supplier_snapshot_ttl_seconds),
            max_workers=settings.supplier_search_max_workers,
            audit=audit,
        )

    def circuit_state(self, supplier: str) -> str:
        """Dashboard/diagnostic view; reading it never changes the breaker."""

        return self._breaker(supplier).state

    def search(
        self,
        db: Session,
        query: SupplierSearchQuery,
        *,
        inquiry_id: UUID | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        use_cache: bool = True,
    ) -> SupplierSearchOutcome:
        started_at = self._clock.now()
        digest = request_hash(query)
        search_row = SupplierSearch(
            tenant_id=tenant_id,
            inquiry_id=inquiry_id,
            request_hash=digest,
            params=query.model_dump(mode="json"),
            status="running",
            started_at=started_at,
            offer_count=0,
            failures=[],
        )
        db.add(search_row)
        db.flush()

        catalog = SupplierCatalog(db, tenant_id=tenant_id, audit=self._audit)
        snapshots = SnapshotStore(
            db,
            clock=self._clock,
            ttl=self._snapshot_ttl,
            tenant_id=tenant_id,
        )
        suppliers = {adapter.name: catalog.ensure(adapter) for adapter in self._adapters}
        self._audit.record(
            db,
            action="supplier.search.started",
            actor=Actor.system(),
            subject_type="supplier_search",
            subject_id=search_row.id,
            after={
                "request_hash": digest,
                "suppliers": sorted(suppliers),
            },
        )

        results: dict[str, SupplierResult] = {}
        to_call: dict[str, SupplierAdapter] = {}
        for adapter in self._adapters:
            name = adapter.name
            supplier = suppliers[name]
            if supplier.status != "active":
                results[name] = self._failure_result(
                    query,
                    name,
                    FailureCode.UNAVAILABLE,
                    f"{name} is {supplier.status}",
                    retryable=False,
                )
                continue

            if use_cache:
                cached = snapshots.fresh(supplier=supplier, request_hash=digest)
                if cached is not None:
                    results[name] = SupplierResult(
                        supplier=name,
                        offers=cached,
                        cached=True,
                    )
                    continue

            if not self._breaker(name).allows():
                results[name] = self._failure_result(
                    query,
                    name,
                    FailureCode.UNAVAILABLE,
                    _CIRCUIT_OPEN_MESSAGE.format(supplier=name),
                    retryable=True,
                )
                continue
            to_call[name] = adapter

        recorded: dict[str, tuple[PriceSnapshot, ...]] = {}
        if to_call:
            recorded = self._call_adapters(
                db,
                query,
                digest,
                search_row.id,
                suppliers,
                snapshots,
                to_call,
                results,
            )

        finished_at = self._clock.now()
        ordered = tuple(results[name] for name in sorted(results))
        outcome = SupplierSearchOutcome(
            search_id=search_row.id,
            request_hash=digest,
            results=ordered,
            started_at=started_at,
            finished_at=finished_at,
        )

        failures = [failure.model_dump(mode="json") for failure in outcome.failures]
        search_row.status = self._status(outcome)
        search_row.finished_at = finished_at
        search_row.offer_count = len(outcome.offers)
        search_row.failures = failures
        db.flush()

        self._audit.record(
            db,
            action="supplier.search.completed",
            actor=Actor.system(),
            subject_type="supplier_search",
            subject_id=search_row.id,
            after={
                "request_hash": digest,
                "status": search_row.status,
                "suppliers": [result.supplier for result in outcome.results],
                "offer_count": search_row.offer_count,
                "failure_count": len(failures),
                "cached": [result.supplier for result in outcome.results if result.cached],
            },
        )
        for name, rows in recorded.items():
            if not rows:
                continue
            self._audit.record(
                db,
                action="price_snapshot.recorded",
                actor=Actor.system(),
                subject_type="supplier",
                subject_id=suppliers[name].id,
                after={
                    "search_id": str(search_row.id),
                    "request_hash": digest,
                    "offer_count": len(rows),
                },
            )
        return outcome

    # -- internals ----------------------------------------------------------

    def _breaker(self, supplier: str) -> CircuitBreaker:
        breaker = self._breakers.get(supplier)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=self._circuit_failure_threshold,
                reset_after=self._circuit_reset_after,
                clock=self._clock,
            )
            self._breakers[supplier] = breaker
        return breaker

    def _call_adapters(
        self,
        db: Session,
        query: SupplierSearchQuery,
        digest: str,
        search_id: UUID,
        suppliers: dict[str, Supplier],
        snapshots: SnapshotStore,
        to_call: dict[str, SupplierAdapter],
        results: dict[str, SupplierResult],
    ) -> dict[str, tuple[PriceSnapshot, ...]]:
        recorded: dict[str, tuple[PriceSnapshot, ...]] = {}
        executor = ThreadPoolExecutor(
            max_workers=min(self._max_workers, len(to_call)),
            thread_name_prefix="supplier",
        )
        submitted: dict[str, tuple[Future[SearchResult], float]] = {}
        try:
            for name, adapter in to_call.items():
                submitted[name] = (
                    executor.submit(adapter.search, query),
                    time.monotonic() + self._timeout.total_seconds(),
                )

            for name in to_call:
                future, deadline = submitted[name]
                try:
                    result = future.result(timeout=max(0.0, deadline - time.monotonic()))
                except FutureTimeoutError:
                    self._breaker(name).record_failure()
                    results[name] = self._failure_result(
                        query,
                        name,
                        FailureCode.TIMEOUT,
                        f"{name} exceeded its {self._timeout.total_seconds():g}s deadline",
                        retryable=True,
                    )
                    continue
                except SupplierTimeoutError as exc:
                    self._breaker(name).record_failure()
                    results[name] = self._failure_result(
                        query,
                        name,
                        FailureCode.TIMEOUT,
                        str(exc),
                        retryable=exc.retryable,
                    )
                    continue
                except SupplierUnavailableError as exc:
                    self._breaker(name).record_failure()
                    results[name] = self._failure_result(
                        query,
                        name,
                        FailureCode.UNAVAILABLE,
                        str(exc),
                        retryable=False,
                    )
                    continue
                except SupplierError as exc:
                    self._breaker(name).record_failure()
                    results[name] = self._failure_result(
                        query,
                        name,
                        FailureCode.UNAVAILABLE,
                        str(exc),
                        retryable=False,
                    )
                    continue
                except Exception as exc:  # contain a broken adapter
                    self._breaker(name).record_failure()
                    # Never log the exception text: a provider payload may carry
                    # a cookie, token or raw customer detail.
                    logger.error("supplier adapter %s raised %s", name, type(exc).__name__)
                    results[name] = self._failure_result(
                        query,
                        name,
                        FailureCode.UNAVAILABLE,
                        f"{name} failed with {type(exc).__name__}",
                        retryable=False,
                    )
                    continue

                if result.supplier != name:
                    self._breaker(name).record_failure()
                    results[name] = self._failure_result(
                        query,
                        name,
                        FailureCode.UNAVAILABLE,
                        f"{name} returned results for another supplier",
                        retryable=False,
                    )
                    continue

                self._breaker(name).record_success()
                recorded[name] = snapshots.record(
                    supplier=suppliers[name],
                    search_id=search_id,
                    request_hash=digest,
                    offers=result.offers,
                )
                results[name] = SupplierResult(
                    supplier=name,
                    offers=result.offers,
                    failures=result.failures,
                )
        finally:
            # Do not wait for a hung adapter: its result is already unusable.
            executor.shutdown(wait=False, cancel_futures=True)
        return recorded

    def _failure_result(
        self,
        query: SupplierSearchQuery,
        supplier: str,
        code: FailureCode,
        message: str,
        *,
        retryable: bool,
    ) -> SupplierResult:
        product_types = tuple(dict.fromkeys(leg.product_type for leg in query.legs))
        return SupplierResult(
            supplier=supplier,
            failures=tuple(
                SearchFailure(
                    product_type=product_type,
                    code=code,
                    message=message,
                    retryable=retryable,
                )
                for product_type in product_types
            ),
        )

    @staticmethod
    def _status(outcome: SupplierSearchOutcome) -> str:
        if not outcome.failures:
            return "ok"
        return "partial" if outcome.offers else "failed"
