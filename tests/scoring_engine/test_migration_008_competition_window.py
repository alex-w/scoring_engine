"""Tests for alembic migration 008 (competition_window table).

Against a database that predates it, the migration must create the
competition_window table and its index; the downgrade must drop them. On a fresh
install (table already present via create_all) upgrade is a no-op.
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
    "008_add_competition_window.py",
)

TABLE = "competition_window"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_008", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


class TestMigration008:
    @pytest.fixture()
    def legacy_engine(self, tmp_path):
        """Full current schema, then drop competition_window to simulate a pre-008 DB."""
        engine = sa.create_engine(f"sqlite:///{tmp_path}/legacy.db")
        db.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE {TABLE}"))
        yield engine
        engine.dispose()

    def test_upgrade_creates_table_and_index(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            assert TABLE not in sa.inspect(conn).get_table_names()
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            assert TABLE in sa.inspect(conn).get_table_names()
            cols = {c["name"] for c in sa.inspect(conn).get_columns(TABLE)}
            assert {"id", "name", "start_time", "end_time", "enabled"} <= cols
            names = {i["name"] for i in sa.inspect(conn).get_indexes(TABLE)}
            assert "ix_competition_window_start" in names

    def test_upgrade_is_noop_when_table_exists(self, migration):
        # Fresh install: table already present via create_all() on the test DB.
        engine = db.session.get_bind()
        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()  # must not raise
            assert TABLE in sa.inspect(conn).get_table_names()

    def test_downgrade_drops_table(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            assert TABLE in sa.inspect(conn).get_table_names()
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert TABLE not in sa.inspect(conn).get_table_names()
