"""Authenticated inquiry API: parse, persist, duplicate flags and gated drafts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from travelos.channels.models import Conversation, Message
from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.models import Contact, Customer
from travelos.inquiry.models import Inquiry, MessageDraft
from travelos.inquiry.parser import InquiryParser
from travelos.llm.gateway import LLMGateway
from travelos.llm.prompts import PromptRegistry
from travelos.llm.providers.fake import FakeProvider

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
MESSAGE = "We want a holiday package to Goa on 2026-11-02 for 2 adults."
MESSAGE_WITH_ROOMS = "We want a holiday package to Goa on 2026-11-02 for 2 adults in 2 rooms."


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login_as_agent(auth_client: TestClient, make_user, login) -> str:
    user = make_user(email="agent@example.test", role="agent")
    response = login(auth_client, user)
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def seed_thread(
    db_factory: sessionmaker[Session],
    *,
    body: str = MESSAGE,
    with_customer: bool = True,
) -> tuple[UUID, UUID]:
    """Create a customer/contact/conversation/message thread and return its ids."""

    with db_factory() as db:
        customer = Customer(customer_type="family", display_name="Synthetic Family")
        db.add(customer)
        db.flush()
        contact = Contact(
            customer_id=customer.id,
            channel="whatsapp",
            value="+919000000123",
            value_hash=ContactFingerprinter().fingerprint("whatsapp", "+919000000123"),
            is_primary=True,
            language="en",
        )
        db.add(contact)
        db.flush()
        conversation = Conversation(
            customer_id=customer.id if with_customer else None,
            contact_id=contact.id,
            channel="whatsapp",
            external_thread_id=f"thread-{uuid4()}",
        )
        db.add(conversation)
        db.flush()
        db.add(
            Message(
                conversation_id=conversation.id,
                direction="inbound",
                channel="whatsapp",
                body=body,
                external_id=f"msg-{uuid4()}",
                occurred_at=datetime(2026, 10, 26, 12, 0, tzinfo=UTC),
            )
        )
        db.commit()
        return conversation.id, customer.id


def add_inbound_message(
    db_factory: sessionmaker[Session], conversation_id: UUID, body: str
) -> None:
    with db_factory() as db:
        db.add(
            Message(
                conversation_id=conversation_id,
                direction="inbound",
                channel="whatsapp",
                body=body,
                external_id=f"msg-{uuid4()}",
                occurred_at=datetime(2026, 10, 26, 12, 5, tzinfo=UTC),
            )
        )
        db.commit()


def package_payload(*, rooms: int | None = None, omit_adults: bool = False) -> dict[str, Any]:
    requirement: dict[str, Any] = {
        "trip_type": "package",
        "origin_city": None,
        "destinations": ["Goa"],
        "start_date": "2026-11-02",
        "end_date": None,
        "date_flexibility_days": None,
        "adults": 2,
        "children_ages": [],
        "infants": 0,
        "rooms": rooms,
        "budget_paise": None,
        "budget_currency": "INR",
        "preferences": [],
        "nationality": None,
        "special_needs": [],
        "urgency": "normal",
    }
    sources: dict[str, list[str]] = {
        "trip_type": ["holiday package"],
        "destinations": ["Goa"],
        "start_date": ["2026-11-02"],
        "adults": ["2 adults"],
    }
    if rooms is not None:
        sources["rooms"] = ["2 rooms"]
    if omit_adults:
        sources.pop("adults")
    confidence = {field: 0.95 for field in sources}
    return {
        "requirement": requirement,
        "confidence": confidence,
        "sources": sources,
        "ambiguous_fields": [],
    }


def scripted_parser(responses: list[str]) -> tuple[InquiryParser, FakeProvider]:
    provider = FakeProvider(responses=responses)
    gateway = LLMGateway(
        providers=[provider],
        prompts=PromptRegistry(REPO_ROOT / "prompts"),
        max_attempts=1,
        backoff_seconds=0.01,
        sleep=lambda _seconds: None,
    )
    return InquiryParser(gateway), provider


def use_parser(app: FastAPI, responses: list[str]) -> FakeProvider:
    parser, provider = scripted_parser(responses)
    app.state.inquiry_parser = parser
    return provider


def test_inquiry_lifecycle_parse_missing_info_and_draft(
    auth_app: FastAPI,
    auth_client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    conversation_id, customer_id = seed_thread(db_factory)
    use_parser(auth_app, [json.dumps(package_payload()), json.dumps(package_payload(rooms=2))])
    token = login_as_agent(auth_client, make_user, login)

    created = auth_client.post(
        "/inquiries",
        json={"conversation_id": str(conversation_id)},
        headers=bearer(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    inquiry_id = body["id"]
    assert body["state"] == "RECEIVED"
    assert body["customer_id"] == str(customer_id)
    assert body["latest_requirement"] is None

    listed = auth_client.get("/inquiries", params={"state": "RECEIVED"}, headers=bearer(token))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [inquiry_id]

    parsed = auth_client.post(f"/inquiries/{inquiry_id}/parse", json={}, headers=bearer(token))
    assert parsed.status_code == 200, parsed.text
    parsed_body = parsed.json()
    assert parsed_body["state"] == "NEEDS_INFO"
    assert parsed_body["trip_type"] == "package"
    assert parsed_body["requires_info"] is True
    assert parsed_body["version"] == 1
    assert parsed_body["prompt_version"] == 1
    assert "rooms" in {field["field"] for field in parsed_body["missing_fields"]}
    assert parsed_body["duplicates"] == []

    detail = auth_client.get(f"/inquiries/{inquiry_id}", headers=bearer(token))
    assert detail.status_code == 200
    latest = detail.json()["latest_requirement"]
    assert latest["version"] == 1
    assert latest["data"]["trip_type"] == "package"
    assert latest["source_spans"]["destinations"] == [
        {"start": MESSAGE.index("Goa"), "end": MESSAGE.index("Goa") + 3}
    ]

    clarified = auth_client.post(f"/inquiries/{inquiry_id}/clarify", json={}, headers=bearer(token))
    assert clarified.status_code == 200, clarified.text
    clarify_body = clarified.json()
    assert clarify_body["sent"] is False
    assert clarify_body["approval_id"] is not None
    assert clarify_body["channel"] == "whatsapp"
    assert "rooms" in clarify_body["question_codes"]
    assert "How many rooms do you need?" in clarify_body["body"]

    approval = auth_client.get(f"/approvals/{clarify_body['approval_id']}", headers=bearer(token))
    assert approval.status_code == 200
    assert approval.json()["action_type"] == "message.clarifying_questions"
    assert approval.json()["level"] == "A1"
    assert approval.json()["status"] == "pending"
    assert approval.json()["subject_id"] == inquiry_id

    add_inbound_message(db_factory, conversation_id, MESSAGE_WITH_ROOMS)
    reparsed = auth_client.post(f"/inquiries/{inquiry_id}/parse", json={}, headers=bearer(token))
    assert reparsed.status_code == 200, reparsed.text
    assert reparsed.json()["version"] == 2
    assert reparsed.json()["state"] == "PARSED"
    assert reparsed.json()["requires_info"] is False

    versions = auth_client.get(f"/inquiries/{inquiry_id}", headers=bearer(token)).json()
    assert versions["latest_requirement"]["version"] == 2
    assert versions["latest_requirement"]["data"]["rooms"] == 2

    with db_factory() as db:
        draft = db.execute(select(MessageDraft)).scalar_one()
        assert draft.status == "draft"
        assert draft.sent_at is None
        assert draft.approval_id == UUID(clarify_body["approval_id"])
        raw = db.execute(
            text("SELECT body FROM message_drafts WHERE id = :id"), {"id": draft.id}
        ).scalar_one()
        assert raw.startswith("enc:v1:")
        assert "How many rooms" not in raw
        outbound = db.execute(
            select(func.count()).select_from(Message).where(Message.direction == "outbound")
        ).scalar_one()
        assert outbound == 0


def test_parse_returns_404_for_an_unknown_inquiry(
    auth_client: TestClient, make_user, login
) -> None:
    token = login_as_agent(auth_client, make_user, login)

    response = auth_client.post(f"/inquiries/{uuid4()}/parse", json={}, headers=bearer(token))

    assert response.status_code == 404
    assert response.json()["error"] == "inquiry_not_found"


def test_create_returns_404_for_an_unknown_conversation(
    auth_client: TestClient, make_user, login
) -> None:
    token = login_as_agent(auth_client, make_user, login)

    response = auth_client.post(
        "/inquiries", json={"conversation_id": str(uuid4())}, headers=bearer(token)
    )

    assert response.status_code == 404


def test_clarify_before_parse_returns_409(
    auth_app: FastAPI,
    auth_client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    conversation_id, _ = seed_thread(db_factory)
    token = login_as_agent(auth_client, make_user, login)
    inquiry_id = auth_client.post(
        "/inquiries",
        json={"conversation_id": str(conversation_id)},
        headers=bearer(token),
    ).json()["id"]

    response = auth_client.post(f"/inquiries/{inquiry_id}/clarify", json={}, headers=bearer(token))

    assert response.status_code == 409
    assert response.json()["error"] == "no_questions_to_draft"


def test_parse_with_an_invented_field_returns_422(
    auth_app: FastAPI,
    auth_client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    conversation_id, _ = seed_thread(db_factory)
    use_parser(auth_app, [json.dumps(package_payload(omit_adults=True))])
    token = login_as_agent(auth_client, make_user, login)
    inquiry_id = auth_client.post(
        "/inquiries",
        json={"conversation_id": str(conversation_id)},
        headers=bearer(token),
    ).json()["id"]

    response = auth_client.post(f"/inquiries/{inquiry_id}/parse", json={}, headers=bearer(token))

    assert response.status_code == 422
    assert response.json()["error"] == "parse_failed"
    with db_factory() as db:
        assert db.execute(select(func.count()).select_from(Inquiry)).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM requirements")).scalar_one() == 0


def test_parse_refuses_a_closed_inquiry(
    auth_app: FastAPI,
    auth_client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    conversation_id, _ = seed_thread(db_factory)
    use_parser(auth_app, [json.dumps(package_payload())])
    token = login_as_agent(auth_client, make_user, login)
    inquiry_id = auth_client.post(
        "/inquiries",
        json={"conversation_id": str(conversation_id)},
        headers=bearer(token),
    ).json()["id"]
    with db_factory() as db:
        inquiry = db.get(Inquiry, UUID(inquiry_id))
        assert inquiry is not None
        inquiry.state = "APPROVED"
        db.commit()

    response = auth_client.post(f"/inquiries/{inquiry_id}/parse", json={}, headers=bearer(token))

    assert response.status_code == 409
    assert response.json()["error"] == "inquiry_state_conflict"


def test_clarify_refuses_a_closed_inquiry(
    auth_app: FastAPI,
    auth_client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    conversation_id, _ = seed_thread(db_factory)
    use_parser(auth_app, [json.dumps(package_payload())])
    token = login_as_agent(auth_client, make_user, login)
    inquiry_id = auth_client.post(
        "/inquiries",
        json={"conversation_id": str(conversation_id)},
        headers=bearer(token),
    ).json()["id"]
    parsed = auth_client.post(f"/inquiries/{inquiry_id}/parse", json={}, headers=bearer(token))
    assert parsed.status_code == 200
    with db_factory() as db:
        inquiry = db.get(Inquiry, UUID(inquiry_id))
        assert inquiry is not None
        inquiry.state = "CLOSED"
        db.commit()

    response = auth_client.post(f"/inquiries/{inquiry_id}/clarify", json={}, headers=bearer(token))

    assert response.status_code == 409
    assert response.json()["error"] == "inquiry_state_conflict"


def test_get_does_not_leak_another_tenant(
    auth_client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    other_tenant = uuid4()
    with db_factory() as db:
        inquiry = Inquiry(tenant_id=other_tenant, state="RECEIVED")
        db.add(inquiry)
        db.commit()
        inquiry_id = inquiry.id
    token = login_as_agent(auth_client, make_user, login)

    response = auth_client.get(f"/inquiries/{inquiry_id}", headers=bearer(token))

    assert response.status_code == 404


def test_list_filters_by_customer(
    auth_client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    conversation_id, customer_id = seed_thread(db_factory)
    token = login_as_agent(auth_client, make_user, login)
    created = auth_client.post(
        "/inquiries",
        json={"conversation_id": str(conversation_id)},
        headers=bearer(token),
    )
    assert created.status_code == 201

    matching = auth_client.get(
        "/inquiries", params={"customer_id": str(customer_id)}, headers=bearer(token)
    )
    missing = auth_client.get(
        "/inquiries", params={"customer_id": str(uuid4())}, headers=bearer(token)
    )

    assert [row["id"] for row in matching.json()] == [created.json()["id"]]
    assert missing.json() == []
