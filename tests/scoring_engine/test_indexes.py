"""Tests for the performance indexes added in alembic revision 003.

Two things need to stay true and are easy to break silently:

1. The models declare the indexes, so ``create_all()`` on a fresh install
   produces the same schema an upgraded database gets from the migration.
2. The ``003_add_performance_indexes`` migration declares exactly the same set,
   so fresh installs and upgraded installs do not drift apart.
"""

import importlib.util
import os

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from scoring_engine.db import db

# index name -> (table name, ordered column names)
EXPECTED_INDEXES = {
    "ix_checks_service_id_round_id": ("checks", ["service_id", "round_id"]),
    "ix_checks_service_id_result": ("checks", ["service_id", "result"]),
    "ix_checks_round_id_result": ("checks", ["round_id", "result"]),
    "ix_rounds_number": ("rounds", ["number"]),
    "ix_flag_solves_host_team_id": ("flag_solves", ["host", "team_id"]),
    "ix_inject_team_id_status": ("inject", ["team_id", "status"]),
    "ix_notifications_team_id_created": ("notifications", ["team_id", "created"]),
    "ix_notifications_team_id_is_read": ("notifications", ["team_id", "is_read"]),
    "ix_kb_name_round_num": ("kb", ["name", "round_num"]),
}

_MIGRATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "alembic",
    "versions",
    "003_add_performance_indexes.py",
)


def _load_migration():
    """Import the migration by path (the versions dir is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_003", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


class TestModelIndexDeclarations:
    """The indexes must live on the models so create_all() emits them."""

    def _metadata_indexes(self):
        found = {}
        for table in db.metadata.tables.values():
            for index in table.indexes:
                found[index.name] = (table.name, [c.name for c in index.columns])
        return found

    @pytest.mark.parametrize("index_name", sorted(EXPECTED_INDEXES))
    def test_index_declared_on_model(self, index_name):
        declared = self._metadata_indexes()
        assert index_name in declared, f"{index_name} is not declared in any model's __table_args__"
        expected_table, expected_columns = EXPECTED_INDEXES[index_name]
        actual_table, actual_columns = declared[index_name]
        assert actual_table == expected_table
        assert actual_columns == expected_columns

    def test_no_undeclared_extra_indexes(self):
        """Guard against a stray index landing in the models without a migration."""
        declared = set(self._metadata_indexes())
        assert declared == set(EXPECTED_INDEXES)

    def test_flag_solves_unique_constraint_still_present(self):
        """The new index is additive — the existing unique constraint stays."""
        constraint_names = {c.name for c in db.metadata.tables["flag_solves"].constraints}
        assert "_flag_host_team_uc" in constraint_names


class TestIndexesExistInDatabase:
    """create_all() (used by bin/setup on fresh installs) must build them."""

    @pytest.mark.parametrize("index_name", sorted(EXPECTED_INDEXES))
    def test_index_created_by_create_all(self, index_name, db_session):
        table_name, expected_columns = EXPECTED_INDEXES[index_name]
        inspector = sa.inspect(db.engine)
        indexes = {i["name"]: i for i in inspector.get_indexes(table_name)}
        assert index_name in indexes, f"{index_name} missing from live {table_name} schema"
        assert indexes[index_name]["column_names"] == expected_columns


class TestMigrationMatchesModels:

    def test_revision_chain(self, migration):
        assert migration.revision == "003"
        assert migration.down_revision == "002"

    def test_migration_declares_same_indexes(self, migration):
        declared = {name: (table, columns) for name, table, columns, _kwargs in migration.INDEXES}
        assert declared == EXPECTED_INDEXES

    def test_host_index_uses_mysql_prefix_length(self, migration):
        """flag_solves.host is String(260); MySQL needs a bounded key prefix."""
        kwargs = {name: kw for name, _t, _c, kw in migration.INDEXES}["ix_flag_solves_host_team_id"]
        assert kwargs["mysql_length"] == {"host": 191}


class TestMigrationRunsBothWays:
    """Simulate an existing database that predates the indexes."""

    @pytest.fixture()
    def legacy_engine(self, tmp_path, migration):
        """A DB with the current tables but none of the new indexes."""
        engine = sa.create_engine(f"sqlite:///{tmp_path}/legacy.db")
        db.metadata.create_all(engine)
        with engine.begin() as conn:
            for name, _table, _columns, _kwargs in migration.INDEXES:
                conn.execute(sa.text(f"DROP INDEX {name}"))
        yield engine
        engine.dispose()

    @staticmethod
    def _index_names(conn, table_name):
        return {i["name"] for i in sa.inspect(conn).get_indexes(table_name)}

    def test_upgrade_creates_then_downgrade_drops(self, legacy_engine, migration):
        with legacy_engine.begin() as conn:
            # Precondition: the legacy schema really is missing them.
            for name, table, _columns, _kwargs in migration.INDEXES:
                assert name not in self._index_names(conn, table)

            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()

            for name, table, _columns, _kwargs in migration.INDEXES:
                assert name in self._index_names(conn, table)

            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()

            for name, table, _columns, _kwargs in migration.INDEXES:
                assert name not in self._index_names(conn, table)

    def test_upgrade_is_idempotent(self, legacy_engine, migration):
        """Re-running against a DB that already has the indexes is a no-op."""
        with legacy_engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                migration.upgrade()

            for name, table, _columns, _kwargs in migration.INDEXES:
                assert name in self._index_names(conn, table)

    def test_downgrade_is_idempotent(self, legacy_engine, migration):
        """Downgrading a DB that never got the indexes must not explode."""
        with legacy_engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()

            for name, table, _columns, _kwargs in migration.INDEXES:
                assert name not in self._index_names(conn, table)
