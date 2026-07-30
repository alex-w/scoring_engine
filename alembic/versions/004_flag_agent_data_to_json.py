"""Convert flags.data and agents.data from PickleType to JSON

Pickle in the database is a deserialization risk (any read of the column
executes whatever the pickle stream says) and it also makes the column
impossible to query or migrate. This migration converts both the column type
*and* the existing rows.

The upgrade unpickles each existing value exactly once, inside the migration,
and rewrites it as JSON. That single unpickle is unavoidable -- it is the only
way to read data that is already in the database -- but after this migration no
application code ever unpickles anything again.

Only the BLOB column is ever renamed, in either direction. MariaDB attaches an
implicit ``CHECK (json_valid(col))`` to JSON columns, and renaming a column that
a CHECK constraint references is fragile there; a BLOB column carries no such
constraint.

Revision ID: 004
Revises: 003
Create Date: 2026-07-24

"""

import json
import pickle

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


# (table name, primary key column) pairs holding the pickled payloads.
#
# ``agents`` is listed but may legitimately not exist: scoring_engine.models.agent
# is not imported by scoring_engine.models.__init__ (or by any application code),
# so ``create_all()`` never emits the table on a deployment that has not otherwise
# imported it. Both directions therefore skip tables that are absent rather than
# failing the whole migration.
TABLES = (("flags", "id"), ("agents", "id"))

# Types that survive a JSON round-trip unchanged. Anything else (tuple, set,
# datetime, arbitrary class instances, non-string dict keys) would be silently
# rewritten by json.dumps, so we refuse to convert it rather than corrupt it.
_JSON_SCALARS = (str, int, float, bool, type(None))


def _existing_tables(conn):
    """Return the subset of TABLES actually present in this database."""
    present = set(sa.inspect(conn).get_table_names())
    return [(table, pk) for table, pk in TABLES if table in present]


def _columns(conn, table):
    return {col["name"] for col in sa.inspect(conn).get_columns(table)}


def _assert_json_native(value, table, row_id, path="data"):
    """Raise unless ``value`` round-trips through JSON without changing shape."""
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ValueError(
            f"{table}.{row_id}: {path} is {value!r}, which is not representable in JSON. "
            "Fix or delete this row before running migration 004."
        )
    if isinstance(value, _JSON_SCALARS):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"{table}.{row_id}: {path} has a non-string dict key {key!r} "
                    f"({type(key).__name__}); JSON would coerce it to a string. "
                    "Fix or delete this row before running migration 004."
                )
            _assert_json_native(item, table, row_id, f"{path}[{key!r}]")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_native(item, table, row_id, f"{path}[{index}]")
        return
    raise ValueError(
        f"{table}.{row_id}: {path} contains a {type(value).__name__}, which has no "
        "lossless JSON representation. Fix or delete this row before running migration 004."
    )


def _to_bytes(raw):
    """Normalize whatever the DBAPI handed back for a BLOB column into bytes."""
    if isinstance(raw, memoryview):
        return raw.tobytes()
    if isinstance(raw, bytearray):
        return bytes(raw)
    return raw


def _unpickle(raw, table, row_id):
    """Decode one stored value, tolerating rows that are already JSON."""
    raw = _to_bytes(raw)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            # The one and only unpickle: existing rows cannot be read any other
            # way. See the module docstring.
            return pickle.loads(raw)  # noqa: S301
        except Exception:
            # Not a pickle stream. It may already be JSON (partially applied
            # migration, or a row written by a newer engine against an old DB).
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                raise ValueError(
                    f"{table}.{row_id}: data is neither a pickle stream nor valid JSON; "
                    "cannot convert. Fix or delete this row before running migration 004."
                )
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            raise ValueError(
                f"{table}.{row_id}: data is a string but not valid JSON; cannot convert. "
                "Fix or delete this row before running migration 004."
            )
    # Already a decoded structure (some drivers do this for JSON columns).
    return raw


def _decode_json(raw, table, row_id):
    """Read a value back out of the JSON column as a Python object."""
    raw = _to_bytes(raw)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _require_value(value, table, row_id, verb):
    if value is None:
        raise ValueError(
            f"{table}.{row_id}: data is NULL but the column is declared NOT NULL. "
            f"Fix or delete this row before {verb} migration 004."
        )
    return value


def _rollback_partial_upgrade(conn, table):
    """Undo a half-finished upgrade so this run can start from a clean slate.

    MySQL/MariaDB have no transactional DDL, so an interrupted run can leave the
    table with both ``data_old`` (the original BLOB) and a partially populated
    ``data`` JSON column.
    """
    cols = _columns(conn, table)
    if "data_old" not in cols:
        return
    if "data" in cols:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("data")
    with op.batch_alter_table(table) as batch_op:
        batch_op.alter_column(
            "data_old",
            new_column_name="data",
            existing_type=sa.LargeBinary(),
            existing_nullable=True,
            nullable=False,
        )


def _finish_partial_downgrade(conn, table):
    """Undo/complete a half-finished downgrade. Returns True if work remains."""
    cols = _columns(conn, table)
    if "data_pickle" not in cols:
        return "data" in cols
    if "data" not in cols:
        # Crashed between DROP data and the rename: just finish the rename.
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "data_pickle",
                new_column_name="data",
                existing_type=sa.LargeBinary(),
                existing_nullable=True,
                nullable=False,
            )
        return False
    # Both columns present: discard the scratch column and redo the conversion.
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_column("data_pickle")
    return True


def upgrade():
    conn = op.get_bind()

    for table, pk in _existing_tables(conn):
        _rollback_partial_upgrade(conn, table)
        if "data" not in _columns(conn, table):
            continue

        # 1. Move the pickled column aside. Renaming a BLOB is safe everywhere.
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "data",
                new_column_name="data_old",
                existing_type=sa.LargeBinary(),
                existing_nullable=False,
            )

        # 2. Create the real JSON column, nullable for now so existing rows pass.
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("data", sa.JSON(), nullable=True))

        # 3. Convert every existing row: pickled bytes -> Python object -> JSON.
        rows = conn.execute(sa.text(f"SELECT {pk}, data_old FROM {table}")).fetchall()
        update = sa.text(f"UPDATE {table} SET data = :val WHERE {pk} = :pk").bindparams(
            sa.bindparam("val", type_=sa.JSON())
        )
        for row_id, raw in rows:
            value = _require_value(_unpickle(raw, table, row_id), table, row_id, "running")
            _assert_json_native(value, table, row_id)
            conn.execute(update, {"val": value, "pk": row_id})

        # 4. Restore NOT NULL and drop the old pickled column.
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("data", existing_type=sa.JSON(), existing_nullable=True, nullable=False)
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("data_old")


def downgrade():
    """Convert JSON back to pickled bytes.

    This is lossless for anything ``upgrade()`` accepted: upgrade() refuses to
    convert values that are not JSON-native, so every value in the JSON column
    is a dict/list/str/number/bool nest that pickles back identically.
    """
    conn = op.get_bind()

    for table, pk in _existing_tables(conn):
        if not _finish_partial_downgrade(conn, table):
            continue

        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("data_pickle", sa.LargeBinary(), nullable=True))

        rows = conn.execute(sa.text(f"SELECT {pk}, data FROM {table}")).fetchall()
        update = sa.text(f"UPDATE {table} SET data_pickle = :val WHERE {pk} = :pk").bindparams(
            sa.bindparam("val", type_=sa.LargeBinary())
        )
        for row_id, raw in rows:
            value = _require_value(_decode_json(raw, table, row_id), table, row_id, "downgrading past")
            # PickleType defaults to pickle.HIGHEST_PROTOCOL, so match it.
            conn.execute(update, {"val": pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), "pk": row_id})

        # Drop the JSON column (this also removes MariaDB's implicit json_valid
        # CHECK), then rename the BLOB scratch column into its place.
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("data")
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "data_pickle",
                new_column_name="data",
                existing_type=sa.LargeBinary(),
                existing_nullable=True,
                nullable=False,
            )
