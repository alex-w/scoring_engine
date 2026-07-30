"""Add competition_window table (multi-day scheduling)

Stores the ``[start_time, end_time)`` intervals during which the engine should
run. No rows == no schedule == the historical always-on behaviour, so nothing is
seeded here; the table simply starts empty on upgrade.

Fresh installs get the table from ``create_all()`` and this migration is a no-op
(guarded by the table-exists check); existing databases get it created here.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


TABLE = "competition_window"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        return  # fresh install already has it via create_all()

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_competition_window_start", TABLE, ["start_time"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    op.drop_index("ix_competition_window_start", table_name=TABLE)
    op.drop_table(TABLE)
