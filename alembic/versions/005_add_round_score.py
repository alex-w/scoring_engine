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

    # Backfill from existing checks: one row per (round, team) with a non-zero
    # passing-check point sum. Built with SQLAlchemy core rather than a raw boolean
    # literal so the truth test renders per dialect -- a hardcoded `result = 1`
    # breaks on PostgreSQL (boolean vs integer), and a bare `WHERE result` breaks on
    # MSSQL (BIT), both of which the README lists as supported. `result.is_(True)`
    # is exactly what the live scoring path uses.
    checks = sa.table(
        "checks",
        sa.column("result", sa.Boolean),
        sa.column("service_id", sa.Integer),
        sa.column("round_id", sa.Integer),
    )
    services = sa.table(
        "services",
        sa.column("id", sa.Integer),
        sa.column("team_id", sa.Integer),
        sa.column("points", sa.Integer),
    )
    rounds = sa.table("rounds", sa.column("id", sa.Integer), sa.column("number", sa.Integer))
    round_score = sa.table(
        TABLE,
        sa.column("round_id"),
        sa.column("round_number"),
        sa.column("team_id"),
        sa.column("service_points"),
        sa.column("flag_points"),
    )

    points_sum = sa.func.sum(services.c.points)
    backfill_select = (
        sa.select(
            rounds.c.id,
            rounds.c.number,
            services.c.team_id,
            points_sum,
            sa.literal(0),
        )
        .select_from(
            checks.join(services, services.c.id == checks.c.service_id).join(
                rounds, rounds.c.id == checks.c.round_id
            )
        )
        .where(checks.c.result.is_(True))
        .group_by(rounds.c.id, rounds.c.number, services.c.team_id)
        .having(points_sum > 0)
    )
    op.execute(
        round_score.insert().from_select(
            ["round_id", "round_number", "team_id", "service_points", "flag_points"],
            backfill_select,
        )
    )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    op.drop_index("ix_round_score_round_number", table_name=TABLE)
    op.drop_index("ix_round_score_team_round", table_name=TABLE)
    op.drop_table(TABLE)
