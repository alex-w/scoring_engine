"""Tests for alembic migration 004 (flags.data / agents.data: PickleType -> JSON).

The migration is exercised against a throwaway on-disk SQLite database built
with the *old* (BLOB / pickled) schema, so we are testing the real data
conversion and not just the column-type DDL.
"""

import importlib.util
import json
import os
import pickle
import sqlite3

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

MIGRATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "alembic",
    "versions",
    "004_flag_agent_data_to_json.py",
)

OLD_FLAGS_DDL = """
CREATE TABLE flags (
    id VARCHAR(36) NOT NULL,
    type VARCHAR(4) NOT NULL,
    platform VARCHAR(7) NOT NULL,
    data BLOB NOT NULL,
    start_time DATETIME NOT NULL,
    end_time DATETIME NOT NULL,
    perm VARCHAR(4) NOT NULL,
    dummy BOOLEAN NOT NULL,
    PRIMARY KEY (id)
)
"""

OLD_AGENTS_DDL = """
CREATE TABLE agents (
    id INTEGER NOT NULL,
    type VARCHAR(4) NOT NULL,
    platform VARCHAR(7) NOT NULL,
    data BLOB NOT NULL,
    start_time DATETIME NOT NULL,
    end_time DATETIME NOT NULL,
    PRIMARY KEY (id)
)
"""

FLAG_PAYLOADS = {
    "flag-0": {"path": "/root/flag.txt", "content": "flag{nix_root}"},
    "flag-1": {"key": "HKEY_LOCAL_MACHINE\\SOFTWARE\\Flag", "value": "flag{win}"},
    "flag-2": {"port": 8080, "content": "flag{net}", "nested": {"a": [1, 2, 3], "b": None, "c": True}},
    "flag-3": {"path": "/tmp/unicode.txt", "content": "flag{éà中文}"},
}

AGENT_PAYLOADS = {
    1: {"cmd": "ls"},
    2: {"cmd": "dir", "args": ["/a", "/b"], "retries": 3},
}


@pytest.fixture(scope="module")
def migration():
    """Import alembic/versions/004_flag_agent_data_to_json.py as a module."""
    spec = importlib.util.spec_from_file_location("migration_004", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pickle(value):
    return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)


def _build_old_db(path, flags=None, agents=None, create_agents=True):
    """Create a SQLite DB with the pre-migration schema and pickled payloads."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(OLD_FLAGS_DDL)
    if create_agents:
        cur.execute(OLD_AGENTS_DDL)

    for flag_id, blob in (flags if flags is not None else {k: _pickle(v) for k, v in FLAG_PAYLOADS.items()}).items():
        cur.execute(
            "INSERT INTO flags (id, type, platform, data, start_time, end_time, perm, dummy) "
            "VALUES (?, 'file', 'nix', ?, '2025-01-01 12:00:00.000000', '2025-01-01 18:00:00.000000', 'root', 0)",
            (flag_id, blob),
        )
    if create_agents:
        for agent_id, blob in (
            agents if agents is not None else {k: _pickle(v) for k, v in AGENT_PAYLOADS.items()}
        ).items():
            cur.execute(
                "INSERT INTO agents (id, type, platform, data, start_time, end_time) "
                "VALUES (?, 'file', 'nix', ?, '2025-01-01 12:00:00.000000', '2025-01-01 18:00:00.000000')",
                (agent_id, blob),
            )
    conn.commit()
    conn.close()


def _run(migration, path, direction="upgrade"):
    """Run upgrade()/downgrade() against the SQLite file at ``path``."""
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(migration, direction)()
        conn.commit()
    engine.dispose()


def _column_info(path, table, column="data"):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    for row in rows:
        if row[1] == column:
            return {"type": row[2], "notnull": bool(row[3])}
    return None


def _column_names(path, table):
    conn = sqlite3.connect(path)
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _raw_values(path, table):
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute(f"SELECT id, data FROM {table}").fetchall())
    finally:
        conn.close()


class TestMigration004Upgrade:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, migration):
        self.migration = migration
        self.path = str(tmp_path / "scratch.db")
        _build_old_db(self.path)

    def test_column_becomes_json_and_stays_not_null(self):
        assert _column_info(self.path, "flags")["type"] == "BLOB"
        _run(self.migration, self.path)
        for table in ("flags", "agents"):
            info = _column_info(self.path, table)
            assert info["type"] == "JSON", table
            assert info["notnull"] is True, table

    def test_existing_flag_rows_are_converted_not_orphaned(self):
        _run(self.migration, self.path)
        values = _raw_values(self.path, "flags")
        assert set(values) == set(FLAG_PAYLOADS)
        for flag_id, expected in FLAG_PAYLOADS.items():
            # Stored as JSON text, and decodes back to the original object.
            assert isinstance(values[flag_id], str)
            assert json.loads(values[flag_id]) == expected

    def test_existing_agent_rows_are_converted(self):
        _run(self.migration, self.path)
        values = _raw_values(self.path, "agents")
        assert {k: json.loads(v) for k, v in values.items()} == AGENT_PAYLOADS

    def test_converted_column_is_queryable_as_json(self):
        _run(self.migration, self.path)
        conn = sqlite3.connect(self.path)
        try:
            rows = conn.execute("SELECT id FROM flags WHERE json_extract(data, '$.path') = '/root/flag.txt'").fetchall()
        finally:
            conn.close()
        assert rows == [("flag-0",)]

    def test_orm_reads_converted_rows_as_dicts(self):
        from scoring_engine.models.flag import Flag

        _run(self.migration, self.path)
        engine = sa.create_engine(f"sqlite:///{self.path}")
        with Session(engine) as session:
            flags = {f.id: f.data for f in session.query(Flag).all()}
        engine.dispose()
        assert flags == FLAG_PAYLOADS

    def test_rows_already_holding_json_are_tolerated(self):
        """A partially-applied migration must not wedge on re-run."""
        path = self.path + ".mixed"
        _build_old_db(
            path,
            flags={
                "pickled": _pickle({"path": "/a"}),
                "already-json": json.dumps({"path": "/b"}).encode(),
            },
            agents={},
        )
        _run(self.migration, path)
        values = {k: json.loads(v) for k, v in _raw_values(path, "flags").items()}
        assert values == {"pickled": {"path": "/a"}, "already-json": {"path": "/b"}}


class TestMigration004Downgrade:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, migration):
        self.migration = migration
        self.path = str(tmp_path / "scratch.db")
        _build_old_db(self.path)

    def test_round_trip_restores_identical_pickles(self):
        before = _raw_values(self.path, "flags")
        _run(self.migration, self.path, "upgrade")
        _run(self.migration, self.path, "downgrade")

        info = _column_info(self.path, "flags")
        assert info["type"] == "BLOB"
        assert info["notnull"] is True

        after = _raw_values(self.path, "flags")
        assert set(after) == set(before)
        for flag_id, blob in after.items():
            assert isinstance(blob, bytes)
            # Byte-identical: same payload, same HIGHEST_PROTOCOL as PickleType.
            assert blob == before[flag_id]
            assert pickle.loads(blob) == FLAG_PAYLOADS[flag_id]

    def test_agents_round_trip(self):
        _run(self.migration, self.path, "upgrade")
        _run(self.migration, self.path, "downgrade")
        assert {k: pickle.loads(v) for k, v in _raw_values(self.path, "agents").items()} == AGENT_PAYLOADS

    def test_upgrade_after_downgrade_works(self):
        _run(self.migration, self.path, "upgrade")
        _run(self.migration, self.path, "downgrade")
        _run(self.migration, self.path, "upgrade")
        assert _column_info(self.path, "flags")["type"] == "JSON"
        assert {k: json.loads(v) for k, v in _raw_values(self.path, "flags").items()} == FLAG_PAYLOADS


class TestMigration004EdgeCases:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, migration):
        self.migration = migration
        self.tmp_path = tmp_path

    def test_missing_agents_table_is_skipped(self):
        """scoring_engine.models.agent is not imported by app code, so many
        real databases have no ``agents`` table at all."""
        path = str(self.tmp_path / "no_agents.db")
        _build_old_db(path, create_agents=False)
        _run(self.migration, path)  # must not raise
        assert _column_info(path, "flags")["type"] == "JSON"
        assert _column_info(path, "agents") is None

    def test_non_json_native_value_fails_loudly(self):
        """Values JSON cannot represent losslessly abort the migration."""
        path = str(self.tmp_path / "tuple.db")
        _build_old_db(path, flags={"bad": _pickle({"ports": (80, 443)})}, agents={})
        with pytest.raises(ValueError, match="no lossless JSON representation"):
            _run(self.migration, path)

    def test_non_string_dict_key_fails_loudly(self):
        path = str(self.tmp_path / "intkey.db")
        _build_old_db(path, flags={"bad": _pickle({1: "one"})}, agents={})
        with pytest.raises(ValueError, match="non-string dict key"):
            _run(self.migration, path)

    def test_non_finite_float_fails_loudly(self):
        path = str(self.tmp_path / "nan.db")
        _build_old_db(path, flags={"bad": _pickle({"ratio": float("inf")})}, agents={})
        with pytest.raises(ValueError, match="not representable in JSON"):
            _run(self.migration, path)

    def test_undecodable_value_fails_loudly(self):
        path = str(self.tmp_path / "junk.db")
        _build_old_db(path, flags={"bad": b"\x00\x01not a pickle and not json"}, agents={})
        with pytest.raises(ValueError, match="neither a pickle stream nor valid JSON"):
            _run(self.migration, path)

    def test_resumes_when_upgrade_was_interrupted_after_the_rename(self):
        """MySQL/MariaDB have no transactional DDL, so a crash can leave the
        original BLOB parked in data_old with no JSON column yet."""
        path = str(self.tmp_path / "partial_rename.db")
        _build_old_db(path, agents={})
        conn = sqlite3.connect(path)
        conn.execute("ALTER TABLE flags RENAME COLUMN data TO data_old")
        conn.commit()
        conn.close()

        _run(self.migration, path)

        assert _column_info(path, "flags")["type"] == "JSON"
        assert "data_old" not in _column_names(path, "flags")
        assert {k: json.loads(v) for k, v in _raw_values(path, "flags").items()} == FLAG_PAYLOADS

    def test_resumes_when_upgrade_was_interrupted_mid_conversion(self):
        """Both data_old and a half-populated data column present."""
        path = str(self.tmp_path / "partial_convert.db")
        _build_old_db(path, agents={})
        conn = sqlite3.connect(path)
        conn.execute("ALTER TABLE flags RENAME COLUMN data TO data_old")
        conn.execute("ALTER TABLE flags ADD COLUMN data JSON")
        conn.execute("UPDATE flags SET data = '{\"stale\": true}' WHERE id = 'flag-0'")
        conn.commit()
        conn.close()

        _run(self.migration, path)

        info = _column_info(path, "flags")
        assert info["type"] == "JSON"
        assert info["notnull"] is True
        assert "data_old" not in _column_names(path, "flags")
        # The stale partial value was discarded and re-derived from the pickle.
        assert {k: json.loads(v) for k, v in _raw_values(path, "flags").items()} == FLAG_PAYLOADS

    def test_resumes_when_downgrade_was_interrupted_before_the_rename(self):
        path = str(self.tmp_path / "partial_downgrade.db")
        _build_old_db(path, agents={})
        _run(self.migration, path, "upgrade")
        conn = sqlite3.connect(path)
        conn.execute("ALTER TABLE flags ADD COLUMN data_pickle BLOB")
        for flag_id, payload in FLAG_PAYLOADS.items():
            conn.execute("UPDATE flags SET data_pickle = ? WHERE id = ?", (_pickle(payload), flag_id))
        conn.execute("ALTER TABLE flags DROP COLUMN data")
        conn.commit()
        conn.close()

        _run(self.migration, path, "downgrade")

        info = _column_info(path, "flags")
        assert info["type"] == "BLOB"
        assert info["notnull"] is True
        assert {k: pickle.loads(v) for k, v in _raw_values(path, "flags").items()} == FLAG_PAYLOADS

    def test_empty_tables_convert_cleanly(self):
        path = str(self.tmp_path / "empty.db")
        _build_old_db(path, flags={}, agents={})
        _run(self.migration, path)
        assert _column_info(path, "flags")["type"] == "JSON"
        assert _column_info(path, "agents")["type"] == "JSON"
