"""CRM tables: customers, contacts, consent, suppression and travellers."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable
from travelos.security.encryption import EncryptedDate, EncryptedString

_CUSTOMER_TYPES = "'individual', 'family', 'corporate'"
_CHANNELS = (
    "'whatsapp', 'email', 'sms', 'phone', 'instagram', 'facebook', 'google_business', 'web_form'"
)
_PURPOSES = "'marketing', 'transactional', 'reminders', 'reviews'"


class Customer(BaseTable):
    __tablename__ = "customers"

    customer_type: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    segment: Mapped[str | None] = mapped_column(String(40))
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    lifetime_value_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    lifetime_value_currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, default="INR", server_default="INR"
    )
    source_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint(f"customer_type IN ({_CUSTOMER_TYPES})", name="customer_type"),
        CheckConstraint("lifetime_value_paise >= 0", name="lifetime_value_non_negative"),
        Index("ix_customers_segment", "segment"),
    )


class Contact(BaseTable):
    __tablename__ = "contacts"

    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    # HMAC/hash for lookup and dedupe; the plaintext is encrypted and unsearchable.
    value_hash: Mapped[str | None] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    language: Mapped[str | None] = mapped_column(String(10))

    __table_args__ = (
        CheckConstraint(f"channel IN ({_CHANNELS})", name="channel"),
        Index("ix_contacts_customer_channel", "customer_id", "channel"),
        Index("ix_contacts_channel_value_hash", "channel", "value_hash"),
        Index(
            "uq_contacts_primary_per_customer",
            "customer_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )


class Consent(BaseTable):
    __tablename__ = "consents"

    contact_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    proof_ref: Mapped[str | None] = mapped_column(String(255))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"channel IN ({_CHANNELS})", name="channel"),
        CheckConstraint(f"purpose IN ({_PURPOSES})", name="purpose"),
        CheckConstraint("status IN ('granted', 'withdrawn')", name="status"),
        CheckConstraint(
            "withdrawn_at IS NULL OR withdrawn_at >= granted_at", name="withdrawal_after_grant"
        ),
        Index("ix_consents_contact_purpose", "contact_id", "purpose", "status"),
    )


class SuppressionEntry(BaseTable):
    """Global STOP/unsubscribe/DND list. Matched by hash, never plaintext."""

    __tablename__ = "suppression_list"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(f"channel IN ({_CHANNELS})", name="channel"),
        UniqueConstraint("channel", "value_hash", name="uq_suppression_channel_hash"),
        Index("ix_suppression_list_value_hash", "value_hash"),
    )


class Traveller(BaseTable):
    __tablename__ = "travellers"

    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    full_name_as_passport: Mapped[str | None] = mapped_column(EncryptedString())
    dob: Mapped[date | None] = mapped_column(EncryptedDate())
    gender: Mapped[str | None] = mapped_column(String(20))
    passport_no: Mapped[str | None] = mapped_column(EncryptedString())
    passport_expiry: Mapped[date | None] = mapped_column(EncryptedDate())
    nationality: Mapped[str | None] = mapped_column(String(2))
    is_minor: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    meal_pref: Mapped[str | None] = mapped_column(String(80))
    seat_pref: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        CheckConstraint("nationality IS NULL OR length(nationality) = 2", name="nationality"),
        Index("ix_travellers_customer", "customer_id"),
    )
