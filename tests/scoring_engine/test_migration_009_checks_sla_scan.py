"""Tests for alembic migration 009 (ix_checks_sla_scan index).

Against a database that predates it, the migration must create the composite
index; the downgrade drops it. On a fresh install (index already present via
create_all) upgrade is a no-op.
"""

import importlib.util
import os

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from scoring_engine.db import db

_MIGRATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "alembic",
    "versions",
    "009_add_checks_sla_scan_index.py",
)

TABLE = "checks"
INDEX = "ix_checks_sla_scan"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_009", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


class TestMigration009:
    @pytest.fixture()
    def legacy_engine(self, tmp_path):
        """Full current schema, then drop the index to simulate a pre-009 DB."""
        engine = sa.create_engine(f"sqlite:///{tmp_path}/legacy.db")
        db.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP INDEX {INDEX}"))
        yield engine
        engine.dispose()

    def test_upgrade_creates_index(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            assert INDEX not in {i["name"] for i in sa.inspect(conn).get_indexes(TABLE)}
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            idx = {i["name"]: i["column_names"] for i in sa.inspect(conn).get_indexes(TABLE)}
            assert INDEX in idx
            assert idx[INDEX] == ["service_id", "completed", "result", "round_id"]

    def test_upgrade_is_noop_when_present(self, migration):
        engine = db.session.get_bind()
        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()  # must not raise
            assert INDEX in {i["name"] for i in sa.inspect(conn).get_indexes(TABLE)}

    def test_downgrade_drops_index(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            assert INDEX in {i["name"] for i in sa.inspect(conn).get_indexes(TABLE)}
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert INDEX not in {i["name"] for i in sa.inspect(conn).get_indexes(TABLE)}
