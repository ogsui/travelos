"""Seeding the generated CRM dataset into PostgreSQL is deterministic and idempotent."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.models import Consent, Contact, Customer, SuppressionEntry, Traveller
from travelos.security.encryption import default_key_provider
from travelos.synthetic import DEFAULT_SEED, generate_dataset
from travelos.synthetic.loader import seed_crm

pytestmark = pytest.mark.integration


def stable_id(seed: int, kind: str, natural_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"travelos/synthetic/{seed}/{kind}/{natural_id}")


def counts(db: Session) -> dict[str, int]:
    models = {
        "customers": Customer,
        "contacts": Contact,
        "consents": Consent,
        "travellers": Traveller,
        "suppressions": SuppressionEntry,
    }
    return {
        name: db.execute(select(func.count()).select_from(model)).scalar_one()
        for name, model in models.items()
    }


def test_seed_crm_is_deterministic_and_idempotent(db: Session) -> None:
    dataset = generate_dataset(DEFAULT_SEED)

    first = seed_crm(db, dataset)
    db.flush()
    after_first = counts(db)

    assert after_first["customers"] == len(dataset.files["customers.jsonl"])
    assert first.customers == after_first["customers"]
    assert first.contacts > 0
    assert first.consents > 0
    assert first.travellers > 0
    assert first.suppressions > 0

    second = seed_crm(db, dataset)
    db.flush()

    assert second.total == 0
    assert counts(db) == after_first


def test_seed_crm_accepts_a_second_seed_without_duplicate_suppressions(db: Session) -> None:
    first = generate_dataset(DEFAULT_SEED)
    second = generate_dataset(DEFAULT_SEED + 1)

    seed_crm(db, first)
    db.flush()
    before_second_seed = counts(db)

    second_report = seed_crm(db, second)
    db.flush()

    assert second_report.customers == len(second.files["customers.jsonl"])
    assert counts(db)["suppressions"] >= before_second_seed["suppressions"]


def test_seeded_pii_is_encrypted_at_rest_and_consent_is_consistent(db: Session) -> None:
    dataset = generate_dataset(DEFAULT_SEED)
    seed_crm(db, dataset)
    db.flush()

    raw_contact = db.execute(text("SELECT value FROM contacts LIMIT 1")).scalar_one()
    raw_passport = db.execute(
        text("SELECT passport_no FROM travellers WHERE passport_no IS NOT NULL LIMIT 1")
    ).scalar_one()
    assert raw_contact.startswith("enc:v1:")
    assert raw_passport.startswith("enc:v1:")

    customers = list(db.execute(select(Customer)).scalars())
    assert customers
    for customer in customers:
        primaries = (
            db.execute(
                select(Contact).where(Contact.customer_id == customer.id, Contact.is_primary)
            )
            .scalars()
            .all()
        )
        assert len(primaries) == 1

    repeated_channel_customer = next(
        record
        for record in dataset.files["customers.jsonl"]
        if len({contact["channel"] for contact in record["contacts"]}) < len(record["contacts"])
    )
    for fixture_consent in repeated_channel_customer["consents"]:
        persisted = db.get(Consent, stable_id(dataset.seed, "consent", fixture_consent["id"]))
        assert persisted is not None
        assert persisted.contact_id == stable_id(
            dataset.seed, "contact", fixture_consent["contact_id"]
        )

    withdrawn = list(db.execute(select(Consent).where(Consent.status == "withdrawn")).scalars())
    assert withdrawn
    assert all(entry.withdrawn_at is not None for entry in withdrawn)


def test_seed_crm_uses_the_same_keyed_fingerprints_for_contacts_and_suppressions(
    db: Session,
) -> None:
    dataset = generate_dataset(DEFAULT_SEED)
    seed_crm(db, dataset)
    fingerprinter = ContactFingerprinter(default_key_provider())

    for customer in dataset.files["customers.jsonl"]:
        for fixture_contact in customer["contacts"]:
            contact = db.get(
                Contact, stable_id(dataset.seed, "contact", str(fixture_contact["id"]))
            )
            assert contact is not None
            expected = fingerprinter.fingerprint(
                str(fixture_contact["channel"]), str(fixture_contact["value"])
            )
            assert contact.value_hash == expected
            if fixture_contact["suppressed"]:
                suppression = db.scalar(
                    select(SuppressionEntry).where(
                        SuppressionEntry.channel == str(fixture_contact["channel"]),
                        SuppressionEntry.value_hash == expected,
                    )
                )
                assert suppression is not None
