"""Add the score_adjustment table (wave 2, phase 5)

Manual white-team score adjustments -- an append-only audit log of bonuses and
penalties applied to teams with a required reason. See
scoring_engine/models/score_adjustment.py.

Nothing to backfill: adjustments only exist once an operator creates one.
downgrade() drops the table (its rows are the only copy of the data, so a
downgrade with rows present would lose them -- the operator is expected to know
that, same as any table drop).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


TABLE = "score_adjustment"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        # Idempotent: a fresh install created the table via create_all() already.
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_score_adjustment_team_id", TABLE, ["team_id"], unique=False)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    op.drop_index("ix_score_adjustment_team_id", table_name=TABLE)
    op.drop_table(TABLE)
