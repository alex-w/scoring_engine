"""Tests for alembic migration 005 (round_score table + backfill).

The migration must, against a database that predates it:
  1. create the round_score table and its indexes, and
  2. backfill it exactly from existing checks -- the reconstructed per-team,
     per-round service_points must equal what scoring_engine.scores would compute
     live, so a mid-competition upgrade does not change any team's score.
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
    "005_add_round_score.py",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_005", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


class TestMigration005:
    @pytest.fixture()
    def legacy_engine(self, tmp_path):
        """Full current schema, then drop round_score to simulate a pre-005 DB."""
        engine = sa.create_engine(f"sqlite:///{tmp_path}/legacy.db")
        db.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE round_score"))
        yield engine
        engine.dispose()

    @staticmethod
    def _seed_checks(conn):
        """Insert two blue teams, services with distinct points, rounds, checks.

        Team 1: svc worth 100 (up in rounds 1,2), svc worth 25 (up in round 1)
                -> round 1 = 125, round 2 = 100, total 225
        Team 2: svc worth 40 (down every round) -> no rows, total 0
        """
        conn.execute(sa.text("INSERT INTO teams (id, name, color) VALUES (1, 'B1', 'Blue'), (2, 'B2', 'Blue')"))
        conn.execute(
            sa.text(
                "INSERT INTO services (id, name, check_name, team_id, points, host, port) VALUES "
                "(1, 'A', 'ICMPCheck', 1, 100, 'h', 1), "
                "(2, 'B', 'ICMPCheck', 1, 25, 'h', 1), "
                "(3, 'C', 'ICMPCheck', 2, 40, 'h', 1)"
            )
        )
        conn.execute(sa.text("INSERT INTO rounds (id, number) VALUES (1, 1), (2, 2)"))
        conn.execute(
            sa.text(
                "INSERT INTO checks (id, round_id, service_id, result, completed) VALUES "
                "(1, 1, 1, 1, 1), "  # team1 svc A up, round 1
                "(2, 1, 2, 1, 1), "  # team1 svc B up, round 1
                "(3, 2, 1, 1, 1), "  # team1 svc A up, round 2
                "(4, 2, 2, 0, 1), "  # team1 svc B down, round 2
                "(5, 1, 3, 0, 1), "  # team2 svc C down, round 1
                "(6, 2, 3, 0, 1)"  # team2 svc C down, round 2
            )
        )

    def test_upgrade_creates_table_and_backfills_exactly(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            self._seed_checks(conn)
            assert "round_score" not in sa.inspect(conn).get_table_names()

            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()

            assert "round_score" in sa.inspect(conn).get_table_names()
            rows = conn.execute(
                sa.text(
                    "SELECT round_number, team_id, service_points, flag_points "
                    "FROM round_score ORDER BY round_number, team_id"
                )
            ).fetchall()

        # Only non-zero (team, round) rows; team 2 never passes so it is absent.
        assert [tuple(r) for r in rows] == [
            (1, 1, 125, 0),
            (2, 1, 100, 0),
        ]

    def test_indexes_created(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            names = {i["name"] for i in sa.inspect(conn).get_indexes("round_score")}
        assert "ix_round_score_team_round" in names
        assert "ix_round_score_round_number" in names

    def test_downgrade_drops_table(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                assert "round_score" in sa.inspect(conn).get_table_names()
                migration.downgrade()
            assert "round_score" not in sa.inspect(conn).get_table_names()

    def test_upgrade_idempotent_when_table_exists(self, migration, tmp_path):
        """A fresh install already has round_score (via create_all); upgrade is a no-op."""
        engine = sa.create_engine(f"sqlite:///{tmp_path}/fresh.db")
        db.metadata.create_all(engine)
        try:
            with engine.begin() as conn:
                with Operations.context(MigrationContext.configure(conn)):
                    migration.upgrade()  # must not raise "table already exists"
                assert "round_score" in sa.inspect(conn).get_table_names()
        finally:
            engine.dispose()
