"""PostgreSQL fixtures for schema tests.

Uses ``pgserver`` (bundled PostgreSQL 16, Apache-2.0) so the suite needs no
Docker and no pre-installed database. Each migration test gets a throwaway
database; ORM tests share one migrated database inside a rolled-back
transaction.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session

from travelos.core.db import build_engine

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
