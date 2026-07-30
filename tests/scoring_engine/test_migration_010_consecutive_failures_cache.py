"""Tests for alembic migration 010 (services.consecutive_failures_cache).

The migration adds the column and backfills it from check history so an upgraded
database is correct without waiting for the engine to run. The backfill must
match scores._service_consecutive_failures: completed failing checks in rounds
after the service's last passing round (all of them if it never passed).
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
    "010_add_service_consecutive_failures_cache.py",
)

TABLE = "services"
COLUMN = "consecutive_failures_cache"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_010", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


def _seed(conn):
    """svc1 never passes (3 fails -> 3); svc2 fails,passes,fails (-> 1); svc3 all pass (-> 0)."""
    conn.execute(sa.text("INSERT INTO teams (id, name, color) VALUES (1, 'B', 'Blue')"))
    conn.execute(
        sa.text(
            "INSERT INTO services (id, name, check_name, team_id, points, host, port) VALUES "
            "(1,'a','ICMPCheck',1,100,'h',1),(2,'b','ICMPCheck',1,100,'h',1),(3,'c','ICMPCheck',1,100,'h',1)"
        )
    )
    conn.execute(sa.text("INSERT INTO rounds (id, number) VALUES (1,1),(2,2),(3,3)"))
    conn.execute(
        sa.text(
            "INSERT INTO checks (id, round_id, service_id, result, completed) VALUES "
            "(1,1,1,0,1),(2,2,1,0,1),(3,3,1,0,1),"  # svc1: fail,fail,fail
            "(4,1,2,0,1),(5,2,2,1,1),(6,3,2,0,1),"  # svc2: fail,pass,fail
            "(7,1,3,1,1),(8,2,3,1,1),(9,3,3,1,1)"  # svc3: pass,pass,pass
        )
    )


class TestMigration010:
    @pytest.fixture()
    def legacy_engine(self, tmp_path):
        """Full schema, then drop the column to simulate a pre-010 DB."""
        engine = sa.create_engine(f"sqlite:///{tmp_path}/legacy.db")
        db.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(sa.text(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"))
        yield engine
        engine.dispose()

    def test_upgrade_adds_column_and_backfills(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            _seed(conn)
            assert COLUMN not in {c["name"] for c in sa.inspect(conn).get_columns(TABLE)}
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            assert COLUMN in {c["name"] for c in sa.inspect(conn).get_columns(TABLE)}
            rows = conn.execute(sa.text(f"SELECT id, {COLUMN} FROM {TABLE} ORDER BY id")).fetchall()
        assert [tuple(r) for r in rows] == [(1, 3), (2, 1), (3, 0)]

    def test_backfill_runs_when_column_already_present(self, tmp_path, migration):
        """Fresh-install shape: column exists (create_all), upgrade just backfills."""
        engine = sa.create_engine(f"sqlite:///{tmp_path}/fresh.db")
        db.metadata.create_all(engine)
        with engine.begin() as conn:
            _seed(conn)
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()  # column present -> add skipped, backfill runs
            rows = conn.execute(sa.text(f"SELECT id, {COLUMN} FROM {TABLE} ORDER BY id")).fetchall()
        engine.dispose()
        assert [tuple(r) for r in rows] == [(1, 3), (2, 1), (3, 0)]

    def test_downgrade_drops_column(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            assert COLUMN in {c["name"] for c in sa.inspect(conn).get_columns(TABLE)}
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert COLUMN not in {c["name"] for c in sa.inspect(conn).get_columns(TABLE)}
