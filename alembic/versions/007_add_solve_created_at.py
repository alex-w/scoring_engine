"""Add created_at to flag_solves (wave 2, phase 6)

The wall-clock scoreboard freeze counts a captured flag only if its solve was
recorded at/before the freeze time. Solves had no timestamp, so add one.

Existing rows (captured before this feature) are backfilled to the migration
time -- a freeze set during a competition is always after the upgrade, so those
solves correctly count as pre-freeze.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


TABLE = "flag_solves"
COLUMN = "created_at"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    if COLUMN in {c["name"] for c in inspector.get_columns(TABLE)}:
        return  # fresh install already has it via create_all()

    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.add_column(sa.Column(COLUMN, sa.DateTime(), nullable=True))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    op.execute(
        sa.text("UPDATE flag_solves SET created_at = :now WHERE created_at IS NULL").bindparams(now=now)
    )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    if COLUMN not in {c["name"] for c in inspector.get_columns(TABLE)}:
        return
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.drop_column(COLUMN)
