"""PostgreSQL fixtures for schema tests.

Uses ``pgserver`` (bundled PostgreSQL 16, Apache-2.0) so the suite needs no
Docker and no pre-installed database. Each migration test gets a throwaway
database; ORM tests share one migrated database inside a rolled-back
transaction.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from travelos.api.app import create_app
from travelos.core.config import KillSwitches, Settings
from travelos.core.db import DEFAULT_TENANT_ID, build_engine, build_session_factory
from travelos.core.models import AuditLog, User
from travelos.security.passwords import hash_password
from travelos.security.totp import generate_secret

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "migrations"

# pgserver logs at exit after pytest may have closed its capture stream, which
# turns a successful run into "--- Logging error ---". Its errors still raise.
logging.getLogger("pgserver").setLevel(logging.CRITICAL)


def alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    # Keep pytest's logging configuration intact.
    config.attributes["configure_logger"] = False
    return config


def upgrade_database(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade_database(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


class AlembicRunner:
    """Callable wrapper so tests can migrate a database they just created."""

    def upgrade(self, url: str, revision: str = "head") -> None:
        upgrade_database(url, revision)

    def downgrade(self, url: str, revision: str = "base") -> None:
        downgrade_database(url, revision)


def _admin_engine(base_url: str) -> Engine:
    return create_engine(base_url, isolation_level="AUTOCOMMIT")


def _database_url(base_url: str, name: str) -> str:
    url: URL = make_url(base_url).set(database=name)
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def postgres_base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start one bundled PostgreSQL 16 server for the whole test session."""

    pgserver = pytest.importorskip("pgserver")
    data_dir = tmp_path_factory.mktemp("pgdata") / "data"
    server = pgserver.get_server(str(data_dir))
    try:
        uri = server.get_uri()
        yield uri.replace("postgresql://", "postgresql+psycopg://", 1)
    finally:
        server.cleanup()


@pytest.fixture
def database_factory(postgres_base_url: str) -> Iterator[Callable[[], str]]:
    """Create throwaway databases and drop them afterwards."""

    created: list[URL] = []

    def factory() -> str:
        name = f"travelos_test_{uuid.uuid4().hex[:12]}"
        admin = _admin_engine(postgres_base_url)
        try:
            with admin.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
        finally:
            admin.dispose()
        url = make_url(postgres_base_url).set(database=name)
        created.append(url)
        return url.render_as_string(hide_password=False)

    yield factory

    admin = _admin_engine(postgres_base_url)
    try:
        with admin.connect() as connection:
            for url in created:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    finally:
        admin.dispose()


@pytest.fixture
def migrate() -> AlembicRunner:
    return AlembicRunner()


@pytest.fixture(scope="session")
def migrated_database_url(
    postgres_base_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    """One migrated database shared by read/write schema tests."""

    name = f"travelos_schema_{uuid.uuid4().hex[:12]}"
    admin = _admin_engine(postgres_base_url)
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()

    url = _database_url(postgres_base_url, name)
    upgrade_database(url)
    try:
        yield url
    finally:
        admin = _admin_engine(postgres_base_url)
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        finally:
            admin.dispose()


@pytest.fixture
def schema_engine(migrated_database_url: str) -> Iterator[Engine]:
    engine = build_engine(migrated_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db(schema_engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back at the end of the test."""

    connection = schema_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# --------------------------------------------------------------------------
# Migrated database clones and API fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def migrated_template_name(postgres_base_url: str) -> Iterator[str]:
    """One fully migrated database used as a template for cheap clones.

    Cloning with ``CREATE DATABASE ... TEMPLATE`` takes milliseconds, so each
    API test gets an empty, migrated database even though migrations are slow.
    """

    name = f"travelos_template_{uuid.uuid4().hex[:12]}"
    admin = _admin_engine(postgres_base_url)
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()

    upgrade_database(_database_url(postgres_base_url, name))
    try:
        yield name
    finally:
        admin = _admin_engine(postgres_base_url)
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        finally:
            admin.dispose()


@pytest.fixture
def database_url(postgres_base_url: str, migrated_template_name: str) -> Iterator[str]:
    """A throwaway migrated database for one API test."""

    name = f"travelos_api_{uuid.uuid4().hex[:12]}"
    admin = _admin_engine(postgres_base_url)
    try:
        with admin.connect() as connection:
            connection.execute(
                text(f'CREATE DATABASE "{name}" TEMPLATE "{migrated_template_name}"')
            )
    finally:
        admin.dispose()

    try:
        yield _database_url(postgres_base_url, name)
    finally:
        admin = _admin_engine(postgres_base_url)
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        finally:
            admin.dispose()


@pytest.fixture
def db_factory(database_url: str) -> Iterator[sessionmaker[Session]]:
    """Session factory for direct assertions against an API test database."""

    engine = build_engine(database_url)
    try:
        yield build_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture
def auth_app(database_url: str, test_settings: Settings) -> FastAPI:
    """App with every operator kill switch at its safe default (OFF)."""

    return create_app(test_settings.model_copy(update={"database_url": database_url}))


@pytest.fixture
def auth_client(auth_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(auth_app) as client:
        yield client


@pytest.fixture
def permissive_app(database_url: str, test_settings: Settings) -> FastAPI:
    """App where the operator permits every capability, so DB flags decide.

    Production deployments enable individual switches explicitly. These tests
    need the operator layer open in order to exercise the owner-level flags.
    """

    switches = KillSwitches(**dict.fromkeys(KillSwitches.switch_names(), True))
    settings = test_settings.model_copy(
        update={"database_url": database_url, "kill_switches": switches}
    )
    return create_app(settings)


@pytest.fixture
def permissive_client(permissive_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(permissive_app) as client:
        yield client


@dataclasses.dataclass(frozen=True, slots=True)
class SyntheticUser:
    """Credentials and identity of a user created by ``make_user``."""

    id: UUID
    email: str
    role: str
    password: str
    mfa_secret: str | None = None


@pytest.fixture
def make_user(db_factory: sessionmaker[Session]) -> Callable[..., SyntheticUser]:
    """Create a synthetic user; never real or realistic personal data."""

    def factory(
        email: str = "agent@example.test",
        role: str = "agent",
        password: str = "synthetic-password-42",
        *,
        mfa_enrolled: bool = False,
        is_active: bool = True,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> SyntheticUser:
        secret = generate_secret() if mfa_enrolled else None
        with db_factory() as db:
            user = User(
                tenant_id=tenant_id,
                email=email,
                password_hash=hash_password(password),
                role=role,
                is_active=is_active,
                mfa_secret=secret,
            )
            db.add(user)
            db.commit()
            return SyntheticUser(
                id=user.id,
                email=user.email,
                role=user.role,
                password=password,
                mfa_secret=secret,
            )

    return factory


@pytest.fixture
def login() -> Callable[..., Response]:
    """POST /auth/login for a user, optionally with a TOTP code."""

    def factory(
        client: TestClient,
        user: SyntheticUser,
        *,
        code: str | None = None,
        password: str | None = None,
    ) -> Response:
        payload: dict[str, str] = {
            "email": user.email,
            "password": password if password is not None else user.password,
        }
        if code is not None:
            payload["totp_code"] = code
        return client.post("/auth/login", json=payload)

    return factory


@pytest.fixture
def csrf_headers() -> Callable[[TestClient], dict[str, str]]:
    """Headers that satisfy the double-submit CSRF check for cookie auth."""

    def factory(client: TestClient) -> dict[str, str]:
        token = client.cookies.get("travelos_csrf")
        return {"X-CSRF-Token": token} if token else {}

    return factory


@pytest.fixture
def audit_rows(db_factory: sessionmaker[Session]) -> Callable[..., list[AuditLog]]:
    """Read audit rows after a request has committed them."""

    def factory(action: str | None = None) -> list[AuditLog]:
        with db_factory() as db:
            statement = select(AuditLog).order_by(AuditLog.at)
            if action is not None:
                statement = statement.where(AuditLog.action == action)
            return list(db.execute(statement).scalars())

    return factory
