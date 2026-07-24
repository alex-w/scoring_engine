"""Add the round_score materialized fact table (wave 2, phase 1)

``round_score`` stores one row per team per round holding the raw points that team
earned that round. It replaces recomputing every team's total from the full
``checks`` history on every scoreboard read. The engine writes a row at round close
(see scoring_engine/scores.materialize_round); this migration creates the table and
backfills it from existing checks so a mid-competition upgrade keeps its scoreboard.

Columns:
  service_points  sum of Service.points over the team's passing checks that round
  flag_points     red-team capture points (populated in phase 4; backfilled as 0)

The backfill is exact for services: for every existing round it reconstructs the
per-team passing-check point sum -- the same value scoring_engine.scores computes
live. Only teams with a non-zero total get a row (a missing (team, round) reads as
zero), which matches the live write path.

downgrade() drops the table. This is lossless: round_score is derived data --
service_points can be recomputed from checks at any time, and flag_points is still 0
at this phase.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


TABLE = "round_score"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        # Idempotent: a fresh install created the table via create_all() already.
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("service_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flag_points", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["round_id"], ["rounds.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "team_id", name="_round_score_round_team_uc"),
    )
    op.create_index("ix_round_score_team_round", TABLE, ["team_id", "round_number"], unique=False)
    op.create_index("ix_round_score_round_number", TABLE, ["round_number"], unique=False)

    # Backfill from existing checks. One row per (round, team) with a non-zero
    # passing-check point sum. `checks.result = 1` is portable across MariaDB
    # (TINYINT(1)) and SQLite (INTEGER); both compare boolean truth to 1.
    op.execute(
        """
        INSERT INTO round_score (round_id, round_number, team_id, service_points, flag_points)
        SELECT r.id, r.number, s.team_id, SUM(s.points), 0
        FROM checks c
        JOIN services s ON s.id = c.service_id
        JOIN rounds r ON r.id = c.round_id
        WHERE c.result = 1
        GROUP BY r.id, r.number, s.team_id
        HAVING SUM(s.points) > 0
        """
    )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    op.drop_index("ix_round_score_round_number", table_name=TABLE)
    op.drop_index("ix_round_score_team_round", table_name=TABLE)
    op.drop_table(TABLE)
