"""Add ix_checks_sla_scan covering index for batched SLA penalties

scores.team_penalties computes SLA penalties for every team in a couple of
grouped queries over ``checks`` (last-passing-round MAX and trailing-failure
COUNT, both grouped by service_id and filtered on completed/result with a
round_id bound). Without a composite index these do full scans of the largest
table in the schema. ``(service_id, completed, result, round_id)`` lets both run
as index-only loose scans -- measured ~6x faster cold scoreboard recompute at
100 teams x 100 services x 3000 rounds.

Fresh installs get the index from ``create_all``; this migration adds it to
existing databases (and is a no-op if it is already present).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

TABLE = "checks"
INDEX = "ix_checks_sla_scan"
COLUMNS = ["service_id", "completed", "result", "round_id"]


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    if INDEX in {i["name"] for i in inspector.get_indexes(TABLE)}:
        return  # fresh install already has it via create_all()
    op.create_index(INDEX, TABLE, COLUMNS)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    if INDEX not in {i["name"] for i in inspector.get_indexes(TABLE)}:
        return
    op.drop_index(INDEX, table_name=TABLE)
