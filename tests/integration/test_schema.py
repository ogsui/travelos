"""Schema, constraint, encryption-at-rest and append-only tests against PostgreSQL."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from travelos.core.db import build_engine
from travelos.core.errors import AppendOnlyError
from travelos.core.models import AuditLog, User
from travelos.crm.models import Consent, Contact, Customer, Traveller
from travelos.security.encryption import ENCRYPTED_PREFIX
from travelos.workflow.models import WorkflowTimer

pytestmark = pytest.mark.integration

BUSINESS_TABLES = {
    "approval_requests",
    "audit_log",
    "autonomy_metrics",
    "autonomy_policies",
    "business_rule_versions",
    "consents",
    "contacts",
    "customers",
    "feature_flags",
    "human_tasks",
    "idempotency_keys",
    "llm_calls",
    "outbox_events",
    "sessions",
    "suppression_list",
    "travellers",
    "users",
    "workflow_timers",
}


def make_customer(session: Session, name: str = "Synthetic Family") -> Customer:
    customer = Customer(customer_type="family", display_name=name)
    session.add(customer)
    session.flush()
    return customer


def make_contact(session: Session, customer: Customer, value: str = "+919000000001") -> Contact:
    contact = Contact(
        customer_id=customer.id,
        channel="whatsapp",
        value=value,
        is_primary=True,
    )
    session.add(contact)
    session.flush()
    return contact


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------


def test_upgrade_and_downgrade_on_empty_database(database_factory, migrate) -> None:
    url = database_factory()

    migrate.upgrade(url)
    engine = build_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) >= BUSINESS_TABLES
    finally:
        engine.dispose()


def test_customer_display_names_are_encrypted_during_migration(database_factory, migrate) -> None:
    url = database_factory()
    migrate.upgrade(url, "0006")
    engine = build_engine(url)
    customer_id = uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO customers "
                    "(id, tenant_id, created_at, updated_at, customer_type, display_name, "
                    "lifetime_value_paise, lifetime_value_currency) "
                    "VALUES (:id, :tenant, now(), now(), 'individual', :name, 0, 'INR')"
                ),
                {
                    "id": customer_id,
                    "tenant": uuid4(),
                    "name": "Migration Synthetic",
                },
            )
    finally:
        engine.dispose()

    migrate.upgrade(url)
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            encrypted = connection.execute(
                text("SELECT display_name FROM customers WHERE id = :id"),
                {"id": customer_id},
            ).scalar_one()
        assert encrypted.startswith(ENCRYPTED_PREFIX)

        from travelos.core.db import build_session_factory

        with build_session_factory(engine)() as session:
            loaded = session.get(Customer, customer_id)
            assert loaded is not None
            assert loaded.display_name == "Migration Synthetic"
    finally:
        engine.dispose()

    migrate.downgrade(url, "0006")
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            restored = connection.execute(
                text("SELECT display_name FROM customers WHERE id = :id"),
                {"id": customer_id},
            ).scalar_one()
        assert restored == "Migration Synthetic"
    finally:
        engine.dispose()

    migrate.downgrade(url)
    engine = build_engine(url)
    try:
        remaining = set(inspect(engine).get_table_names())
        assert remaining & BUSINESS_TABLES == set()
        assert remaining <= {"alembic_version"}
    finally:
        engine.dispose()


def test_downgrade_and_reupgrade_on_seeded_database(database_factory, migrate) -> None:
    url = database_factory()
    migrate.upgrade(url)

    engine = build_engine(url)
    from travelos.core.db import build_session_factory

    factory = build_session_factory(engine)
    with factory() as session:
        session.add(User(email="owner@example.test", password_hash="synthetic-hash", role="owner"))
        customer = make_customer(session)
        contact = make_contact(session, customer)
        session.add(
            Consent(
                contact_id=contact.id,
                channel="whatsapp",
                purpose="marketing",
                status="granted",
                source="web_form",
            )
        )
        session.add(
            Traveller(
                customer_id=customer.id,
                full_name_as_passport="SYNTHETIC TRAVELLER",
                dob=date(1985, 1, 1),
                passport_no="M1234567",
                passport_expiry=date(2030, 1, 1),
            )
        )
        session.add(AuditLog(actor_type="system", action="seed.test"))
        session.commit()

    try:
        migrate.downgrade(url)
        migrate.upgrade(url)
        with engine.connect() as connection:
            for table in ("users", "customers", "contacts", "consents", "travellers"):
                count = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                assert count == 0, f"{table} still has rows after downgrade"
    finally:
        engine.dispose()


# --------------------------------------------------------------------------
# Shape of the schema
# --------------------------------------------------------------------------


def test_all_business_tables_exist(schema_engine) -> None:
    assert set(inspect(schema_engine).get_table_names()) >= BUSINESS_TABLES


def test_every_business_table_has_tenant_timestamps_and_uuid_pk(schema_engine) -> None:
    inspector = inspect(schema_engine)

    with schema_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND column_name IN ('id', 'tenant_id', 'created_at', 'updated_at')
                """
            )
        ).all()
    data_types = {(row.table_name, row.column_name): row.data_type for row in rows}

    for table in sorted(BUSINESS_TABLES):
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert "id" in columns, table
        assert columns["id"]["nullable"] is False, table
        assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"], table

        for required in ("tenant_id", "created_at", "updated_at"):
            assert required in columns, f"{table}.{required} missing"
            assert columns[required]["nullable"] is False, f"{table}.{required} nullable"

        assert data_types[(table, "id")] == "uuid", table
        assert data_types[(table, "tenant_id")] == "uuid", table
        assert data_types[(table, "created_at")] == "timestamp with time zone", table
        assert data_types[(table, "updated_at")] == "timestamp with time zone", table


def test_money_columns_are_bigint_with_char_currency(schema_engine) -> None:
    inspector = inspect(schema_engine)

    customer_columns = {c["name"]: c for c in inspector.get_columns("customers")}
    assert customer_columns["lifetime_value_paise"]["type"].__class__.__name__ == "BIGINT"
    assert customer_columns["lifetime_value_currency"]["type"].__class__.__name__ == "CHAR"

    policy_columns = {c["name"]: c for c in inspector.get_columns("autonomy_policies")}
    assert policy_columns["value_threshold_paise"]["type"].__class__.__name__ == "BIGINT"
    assert policy_columns["currency"]["type"].__class__.__name__ == "CHAR"


def test_no_float_types_in_database(schema_engine) -> None:
    with schema_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND data_type IN ('numeric', 'real', 'double precision', 'money')
                """
            )
        ).all()

    assert rows == []


def test_required_indexes_and_unique_constraints_exist(schema_engine) -> None:
    inspector = inspect(schema_engine)

    expected_indexes = {
        "approval_requests": {
            "ix_approval_requests_status",
            "ix_approval_requests_action",
            "ix_approval_requests_subject",
            "ix_approval_requests_expires",
        },
        "audit_log": {"ix_audit_log_action_at", "ix_audit_log_actor", "ix_audit_log_subject"},
        "contacts": {
            "ix_contacts_customer_channel",
            "ix_contacts_channel_value_hash",
            "uq_contacts_primary_per_customer",
        },
        "consents": {"ix_consents_contact_purpose"},
        "idempotency_keys": {"ix_idempotency_keys_expires_at"},
        "llm_calls": {"ix_llm_calls_prompt", "ix_llm_calls_status", "ix_llm_calls_provider"},
        "outbox_events": {"ix_outbox_events_status_available"},
        "sessions": {"ix_sessions_user_id", "ix_sessions_expires_at"},
        "suppression_list": {"ix_suppression_list_value_hash"},
        "human_tasks": {"ix_human_tasks_status_due", "ix_human_tasks_assignee"},
        "workflow_timers": {
            "ix_workflow_timers_status_due",
            "ix_workflow_timers_subject",
        },
    }
    for table, names in expected_indexes.items():
        actual = {index["name"] for index in inspector.get_indexes(table)}
        assert names <= actual, f"{table} missing {names - actual}"

    expected_unique = {
        "users": {("tenant_id", "email")},
        "idempotency_keys": {("key", "scope")},
        "suppression_list": {("channel", "value_hash")},
        "autonomy_policies": {("tenant_id", "action_type")},
        "business_rule_versions": {("tenant_id", "version"), ("tenant_id", "yaml_hash")},
        "workflow_timers": {
            ("tenant_id", "name", "subject_type", "subject_id"),
        },
    }
    for table, expected in expected_unique.items():
        actual = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table)}
        assert expected <= actual, f"{table} missing {expected - actual}"


# --------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------


def test_partial_index_enforces_one_primary_contact_per_customer(db: Session) -> None:
    customer = make_customer(db)
    make_contact(db, customer, "+919000000001")

    db.add(
        Contact(
            customer_id=customer.id,
            channel="email",
            value="family@example.test",
            is_primary=True,
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_user_email_is_unique_per_tenant(db: Session) -> None:
    db.add(User(email="owner@example.test", password_hash="x" * 20, role="owner"))
    db.flush()
    db.add(User(email="owner@example.test", password_hash="y" * 20, role="agent"))

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_idempotency_key_is_unique_per_scope(db: Session) -> None:
    from travelos.core.models import IdempotencyKey

    db.add(IdempotencyKey(key="quote.send:1", scope="quotes"))
    db.flush()
    db.add(IdempotencyKey(key="quote.send:1", scope="quotes"))

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_consent_withdrawn_before_granted_is_rejected(db: Session) -> None:
    customer = make_customer(db)
    contact = make_contact(db, customer)
    granted_at = datetime(2026, 1, 10, 12, 0, 0)
    db.add(
        Consent(
            contact_id=contact.id,
            channel="whatsapp",
            purpose="marketing",
            status="withdrawn",
            source="web_form",
            granted_at=granted_at,
            withdrawn_at=granted_at - timedelta(days=1),
        )
    )

    with pytest.raises(IntegrityError, match="withdrawal_after_grant"):
        db.flush()
    db.rollback()


def test_consent_rejects_unknown_purpose_and_status(db: Session) -> None:
    customer = make_customer(db)
    contact = make_contact(db, customer)

    db.add(
        Consent(
            contact_id=contact.id,
            channel="whatsapp",
            purpose="telepathy",
            status="granted",
            source="web_form",
        )
    )
    with pytest.raises(IntegrityError, match="ck_consents_purpose"):
        db.flush()
    db.rollback()

    db.add(
        Consent(
            contact_id=contact.id,
            channel="whatsapp",
            purpose="marketing",
            status="maybe",
            source="web_form",
        )
    )
    with pytest.raises(IntegrityError, match="ck_consents_status"):
        db.flush()
    db.rollback()


def test_consent_requires_an_existing_contact(db: Session) -> None:
    db.add(
        Consent(
            contact_id=uuid4(),
            channel="email",
            purpose="transactional",
            status="granted",
            source="web_form",
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_negative_lifetime_value_is_rejected(db: Session) -> None:
    db.add(
        Customer(
            customer_type="individual",
            display_name="Synthetic Negative",
            lifetime_value_paise=-1,
        )
    )

    with pytest.raises(IntegrityError, match="lifetime_value_non_negative"):
        db.flush()
    db.rollback()


def test_workflow_timer_rejects_an_unknown_status(db: Session) -> None:
    db.add(
        WorkflowTimer(
            name="quote.expiry",
            subject_type="quote",
            subject_id=uuid4(),
            due_at=datetime(2026, 1, 1, tzinfo=UTC),
            status="someday",
        )
    )

    with pytest.raises(IntegrityError, match="ck_workflow_timers_status"):
        db.flush()
    db.rollback()


# --------------------------------------------------------------------------
# Field-level encryption at rest
# --------------------------------------------------------------------------


def test_contact_value_is_encrypted_at_rest_and_decrypted_on_read(db: Session) -> None:
    plaintext = "+919000000009"
    customer = make_customer(db)
    contact = make_contact(db, customer, plaintext)

    raw = db.execute(
        text("SELECT value FROM contacts WHERE id = :id"), {"id": contact.id}
    ).scalar_one()

    assert raw.startswith(ENCRYPTED_PREFIX)
    assert plaintext not in raw

    db.expire_all()
    loaded = db.get(Contact, contact.id)
    assert loaded is not None
    assert loaded.value == plaintext


def test_user_mfa_secret_is_encrypted_at_rest(db: Session) -> None:
    user = User(
        email="owner@example.test",
        password_hash="x" * 20,
        role="owner",
        mfa_secret="JBSWY3DPEHPK3PXP",
    )
    db.add(user)
    db.flush()

    raw = db.execute(
        text("SELECT mfa_secret FROM users WHERE id = :id"), {"id": user.id}
    ).scalar_one()

    assert raw.startswith(ENCRYPTED_PREFIX)
    assert "JBSWY3DPEHPK3PXP" not in raw

    db.expire_all()
    loaded = db.get(User, user.id)
    assert loaded is not None
    assert loaded.mfa_secret == "JBSWY3DPEHPK3PXP"


def test_traveller_passport_and_dob_are_encrypted_at_rest(db: Session) -> None:
    customer = make_customer(db)
    traveller = Traveller(
        customer_id=customer.id,
        full_name_as_passport="SYNTHETIC TRAVELLER",
        dob=date(1985, 1, 1),
        passport_no="M1234567",
        passport_expiry=date(2030, 12, 31),
    )
    db.add(traveller)
    db.flush()

    raw = db.execute(
        text(
            "SELECT full_name_as_passport, dob, passport_no, passport_expiry "
            "FROM travellers WHERE id = :id"
        ),
        {"id": traveller.id},
    ).one()

    for value in raw:
        assert value.startswith(ENCRYPTED_PREFIX)
    assert "M1234567" not in raw.passport_no
    assert "1985-01-01" not in raw.dob

    db.expire_all()
    loaded = db.get(Traveller, traveller.id)
    assert loaded is not None
    assert loaded.passport_no == "M1234567"
    assert loaded.dob == date(1985, 1, 1)
    assert loaded.passport_expiry == date(2030, 12, 31)
    assert loaded.full_name_as_passport == "SYNTHETIC TRAVELLER"


def test_suppression_list_stores_hash_not_plaintext(schema_engine) -> None:
    columns = {c["name"] for c in inspect(schema_engine).get_columns("suppression_list")}

    assert "value_hash" in columns
    assert "value" not in columns
    assert "phone" not in columns


# --------------------------------------------------------------------------
# Audit log append-only
# --------------------------------------------------------------------------


def test_audit_log_accepts_appends(db: Session) -> None:
    db.add(AuditLog(actor_type="system", action="test.append"))
    db.flush()

    assert db.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 1


def test_audit_log_orm_update_is_blocked(db: Session) -> None:
    entry = AuditLog(actor_type="system", action="test.append")
    db.add(entry)
    db.flush()

    entry.action = "test.tampered"

    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()
    db.rollback()


def test_audit_log_orm_delete_is_blocked(db: Session) -> None:
    entry = AuditLog(actor_type="system", action="test.append")
    db.add(entry)
    db.flush()

    db.delete(entry)

    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()
    db.rollback()


def test_audit_log_raw_update_is_blocked_by_the_database(db: Session) -> None:
    entry = AuditLog(actor_type="system", action="test.append")
    db.add(entry)
    db.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(
            text("UPDATE audit_log SET action = 'tampered' WHERE id = :id"),
            {"id": entry.id},
        )
    db.rollback()


def test_audit_log_raw_delete_is_blocked_by_the_database(db: Session) -> None:
    entry = AuditLog(actor_type="system", action="test.append")
    db.add(entry)
    db.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": entry.id})
    db.rollback()


def test_audit_log_truncate_is_blocked_by_the_database(db: Session) -> None:
    db.add(AuditLog(actor_type="system", action="test.append"))
    db.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("TRUNCATE audit_log"))
    db.rollback()
