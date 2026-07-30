"""Add services.consecutive_failures_cache and backfill it

The batched SLA penalty read (scores.team_penalties) gets each service's
consecutive-failure count from this materialized column instead of scanning the
checks table -- the engine keeps it current at round close. Add the column and
backfill it from existing check history so an upgraded database is correct
immediately (matching scores._service_consecutive_failures: completed failing
checks in rounds after the service's last passing round, or all of them if it
never passed).

Fresh installs get the column from create_all and populate it via bin/setup
(example data) or leave it at 0 (no checks yet); this migration covers upgrades.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

TABLE = "services"
COLUMN = "consecutive_failures_cache"

# 1/0 literals rather than bound booleans so the same SQL runs on MySQL and
# SQLite (both store booleans as 0/1). Correlated on services.id; the inner
# selects read only ``checks``, so this never selects from the table it updates.
_BACKFILL = (
    "UPDATE services SET consecutive_failures_cache = ("
    "  SELECT COUNT(*) FROM checks c"
    "  WHERE c.service_id = services.id AND c.completed = 1 AND c.result = 0"
    "    AND c.round_id > COALESCE("
    "      (SELECT MAX(round_id) FROM checks"
    "       WHERE service_id = services.id AND completed = 1 AND result = 1), 0)"
    ")"
)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    if COLUMN not in {c["name"] for c in inspector.get_columns(TABLE)}:
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.add_column(sa.Column(COLUMN, sa.Integer(), nullable=False, server_default="0"))
    op.execute(_BACKFILL)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    if COLUMN in {c["name"] for c in inspector.get_columns(TABLE)}:
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.drop_column(COLUMN)
