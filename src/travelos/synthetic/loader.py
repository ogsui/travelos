"""Write generated datasets to disk and load the CRM portion into PostgreSQL.

Files are serialised deterministically: JSON keys sorted, compact separators,
UTF-8 and LF line endings. The manifest records a SHA-256 per file so a stale
or hand-edited fixture is caught by the tests.

Database IDs are UUIDv5 values derived from the dataset seed and the fixture's
natural key, which makes a re-run an idempotent no-op. Only CRM tables exist at
task 007; inquiry, quote and supplier tables arrive in later tasks and keep
using the same JSONL files.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.crm.models import Consent, Contact, Customer, SuppressionEntry, Traveller
from travelos.synthetic.generator import Record, SyntheticDataset

logger = logging.getLogger("travelos.synthetic.loader")

MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class SeedCrmReport:
    """How many rows the CRM seed actually inserted."""

    customers: int = 0
    contacts: int = 0
    consents: int = 0
    travellers: int = 0
    suppressions: int = 0

    @property
    def total(self) -> int:
        return self.customers + self.contacts + self.consents + self.travellers + self.suppressions


def _encode_jsonl(records: list[Record]) -> bytes:
    lines = [
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for record in records
    ]
    text = "\n".join(lines)
    if lines:
        text += "\n"
    return text.encode("utf-8")


def _encode_json(value: object) -> bytes:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def write_dataset(
    dataset: SyntheticDataset,
    directory: str | Path,
    *,
    eval_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Write every JSONL file and the manifest. Returns the manifest."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    eval_target = Path(eval_directory) if eval_directory is not None else target
    eval_target.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for name, records in sorted(dataset.files.items()):
        payload = _encode_jsonl(records)
        (target / name).write_bytes(payload)
        entries.append(
            {
                "name": name,
                "count": len(records),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    for name, records in sorted(dataset.eval_files.items()):
        (eval_target / name).write_bytes(_encode_jsonl(records))

    manifest: dict[str, Any] = {
        "generator_version": dataset.generator_version,
        "seed": dataset.seed,
        "files": entries,
    }
    (target / MANIFEST_NAME).write_bytes(_encode_json(manifest))
    return manifest


def _stable_id(seed: int, kind: str, natural_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"travelos/synthetic/{seed}/{kind}/{natural_id}")


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value is not None else None


def _hash_value(value: str) -> str:
    """Placeholder dedupe hash.

    Task 008 owns the real consent/dedupe convention (HMAC keyed by the
    application secret). The seed only needs a deterministic 64-character value
    that never exposes the plaintext contact.
    """

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def seed_crm(db: Session, dataset: SyntheticDataset) -> SeedCrmReport:
    """Insert the CRM rows for ``dataset``; return counts of new rows only."""

    customers = dataset.files["customers.jsonl"]
    counts = {
        "customers": 0,
        "contacts": 0,
        "consents": 0,
        "travellers": 0,
        "suppressions": 0,
    }

    customer_ids: dict[str, UUID] = {}
    contact_ids_by_customer: dict[str, dict[str, UUID]] = {}

    # Pass 1: customers.
    for record in customers:
        customer_natural = str(record["id"])
        customer_id = _stable_id(dataset.seed, "customer", customer_natural)
        customer_ids[customer_natural] = customer_id
        if db.get(Customer, customer_id) is None:
            db.add(
                Customer(
                    id=customer_id,
                    customer_type=str(record["customer_type"]),
                    display_name=str(record["display_name"]),
                    segment=str(record["segment"]),
                    lifetime_value_paise=int(record["lifetime_value_paise"]),
                    lifetime_value_currency=str(record["lifetime_value_currency"]),
                    created_at=_parse_ts(str(record["created_at"])),
                )
            )
            counts["customers"] += 1

    # These mappers declare no ORM relationships, so the unit of work cannot
    # infer the insert order. Flush each level explicitly before the next one.
    db.flush()

    # Pass 2: contacts and the global suppression list.
    for record in customers:
        customer_natural = str(record["id"])
        customer_id = customer_ids[customer_natural]
        contact_ids: dict[str, UUID] = {}
        for contact in record["contacts"]:
            contact_id = _stable_id(dataset.seed, "contact", str(contact["id"]))
            contact_ids[str(contact["id"])] = contact_id
            if db.get(Contact, contact_id) is None:
                db.add(
                    Contact(
                        id=contact_id,
                        customer_id=customer_id,
                        channel=str(contact["channel"]),
                        value=str(contact["value"]),
                        value_hash=_hash_value(str(contact["value"])),
                        verified=bool(contact["verified"]),
                        is_primary=bool(contact["primary"]),
                        language=str(contact["language"]),
                    )
                )
                counts["contacts"] += 1

            if bool(contact["suppressed"]):
                value_hash = _hash_value(str(contact["value"]))
                suppression_id = _stable_id(
                    dataset.seed, "suppression", f"{contact['channel']}:{contact['value']}"
                )
                existing_suppression = db.scalar(
                    select(SuppressionEntry.id).where(
                        SuppressionEntry.channel == str(contact["channel"]),
                        SuppressionEntry.value_hash == value_hash,
                    )
                )
                if existing_suppression is None:
                    db.add(
                        SuppressionEntry(
                            id=suppression_id,
                            channel=str(contact["channel"]),
                            value_hash=value_hash,
                            reason=str(contact["suppression_reason"]),
                            note="Synthetic STOP/unsubscribe fixture; no real contact.",
                        )
                    )
                    counts["suppressions"] += 1

        contact_ids_by_customer[customer_natural] = contact_ids

    db.flush()

    # Pass 3: consents and travellers.
    for record in customers:
        customer_natural = str(record["id"])
        customer_id = customer_ids[customer_natural]
        contact_ids = contact_ids_by_customer[customer_natural]

        for consent in record["consents"]:
            consent_id = _stable_id(dataset.seed, "consent", str(consent["id"]))
            if db.get(Consent, consent_id) is not None:
                continue
            consent_contact_id = contact_ids.get(str(consent["contact_id"]))
            if consent_contact_id is None:
                raise ValueError(
                    f"consent {consent['id']} references an unknown contact {consent['contact_id']}"
                )
            db.add(
                Consent(
                    id=consent_id,
                    contact_id=consent_contact_id,
                    channel=str(consent["channel"]),
                    purpose=str(consent["purpose"]),
                    status=str(consent["status"]),
                    source=str(consent["source"]),
                    proof_ref=None if consent["proof_ref"] is None else str(consent["proof_ref"]),
                    granted_at=_parse_ts(str(consent["granted_at"])),
                    withdrawn_at=None
                    if consent["withdrawn_at"] is None
                    else _parse_ts(str(consent["withdrawn_at"])),
                )
            )
            counts["consents"] += 1

        for traveller in record["travellers"]:
            traveller_id = _stable_id(dataset.seed, "traveller", str(traveller["id"]))
            if db.get(Traveller, traveller_id) is not None:
                continue
            db.add(
                Traveller(
                    id=traveller_id,
                    customer_id=customer_id,
                    full_name_as_passport=str(traveller["full_name_as_passport"]),
                    dob=_parse_date(None if traveller["dob"] is None else str(traveller["dob"])),
                    gender=str(traveller["gender"]),
                    passport_no=None
                    if traveller["passport_no"] is None
                    else str(traveller["passport_no"]),
                    passport_expiry=_parse_date(
                        None
                        if traveller["passport_expiry"] is None
                        else str(traveller["passport_expiry"])
                    ),
                    nationality=None
                    if traveller["nationality"] is None
                    else str(traveller["nationality"]),
                    is_minor=bool(traveller["is_minor"]),
                    meal_pref=None
                    if traveller["meal_pref"] is None
                    else str(traveller["meal_pref"]),
                    seat_pref=None
                    if traveller["seat_pref"] is None
                    else str(traveller["seat_pref"]),
                )
            )
            counts["travellers"] += 1

    report = SeedCrmReport(**counts)
    logger.info(
        "seed_crm: customers=%s contacts=%s consents=%s travellers=%s suppressions=%s",
        report.customers,
        report.contacts,
        report.consents,
        report.travellers,
        report.suppressions,
    )
    return report
