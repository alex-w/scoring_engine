"""Add composite performance indexes for hot query paths

Before this migration the schema declared no indexes at all beyond primary
keys and the one unique constraint on ``flag_solves``.  InnoDB silently adds a
single-column index for every FOREIGN KEY column, which is all the scoring
engine had to work with — every composite / covering access path degraded into
a full table scan (plus a filesort for the ORDER BY paths).

Indexes added here, and the query each one serves:

``checks`` (largest table: services x rounds)
  ix_checks_service_id_round_id   sla.get_consecutive_failures /
                                  get_max_consecutive_failures and
                                  Service.checks_reversed:
                                  WHERE service_id = ? ORDER BY round_id
  ix_checks_service_id_result     Service.score_earned / max_score:
                                  COUNT(*) WHERE service_id = ? [AND result]
  ix_checks_round_id_result       api/overview.py num_up_services /
                                  num_down_services, api/admin.py round stats:
                                  WHERE round_id = ? AND result = ?

``rounds``
  ix_rounds_number                Round.get_last_round_num():
                                  ORDER BY number DESC LIMIT 1, plus the
                                  number = / <= / >= filters everywhere else

``flag_solves``
  ix_flag_solves_host_team_id     api/agent.py do_checkin's unsolved-flag
                                  subquery and api/flags.py's solves outer
                                  join: WHERE host = ? AND team_id = ?.
                                  The existing _flag_host_team_uc unique
                                  constraint is flag_id-leading and cannot
                                  serve these.

``inject``
  ix_inject_team_id_status        Team.current_inject_score:
                                  WHERE team_id = ? AND status = 'Graded'

``notifications``
  ix_notifications_team_id_created  create_notification() de-dup lookup:
                                    WHERE team_id = ? AND created >= cutoff
  ix_notifications_team_id_is_read  /api/notifications/{read,unread} and the
                                    mark-all-read UPDATE

``kb``
  ix_kb_name_round_num            /api/admin/get_round_progress (polled every
                                  3s by the admin status page):
                                  WHERE name = 'task_ids'
                                  ORDER BY round_num DESC LIMIT 1

Deliberately NOT added:
  * ``services(team_id)`` — redundant, InnoDB already maintains it for the FK.
  * ``flag_solves(team_id, flag_id)`` — no query filters on that pair; the FK
    index on team_id and the flag_id-leading unique constraint cover the
    existing access paths.

CREATE INDEX is supported natively by both MySQL/MariaDB and SQLite, so no
``batch_alter_table`` table rebuild is required here (batch mode is only
needed for the ALTER TABLE forms SQLite lacks).  The operations are guarded by
an inspector check so the migration is idempotent against databases that were
built with ``create_all()`` and already carry the model-declared indexes.

Revision ID: 003
Revises: 002
Create Date: 2026-07-24

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


# (index_name, table_name, [columns], kwargs)
INDEXES = [
    ("ix_checks_service_id_round_id", "checks", ["service_id", "round_id"], {}),
    ("ix_checks_service_id_result", "checks", ["service_id", "result"], {}),
    ("ix_checks_round_id_result", "checks", ["round_id", "result"], {}),
    ("ix_rounds_number", "rounds", ["number"], {}),
    # ``host`` is String(260); a 191-char prefix keeps the MySQL key length
    # comfortably under InnoDB's limit while staying fully selective for
    # hostnames / IP addresses.  mysql_length is ignored by other dialects.
    (
        "ix_flag_solves_host_team_id",
        "flag_solves",
        ["host", "team_id"],
        {"mysql_length": {"host": 191}},
    ),
    ("ix_inject_team_id_status", "inject", ["team_id", "status"], {}),
    ("ix_notifications_team_id_created", "notifications", ["team_id", "created"], {}),
    ("ix_notifications_team_id_is_read", "notifications", ["team_id", "is_read"], {}),
    ("ix_kb_name_round_num", "kb", ["name", "round_num"], {}),
]


def _existing(inspector, table_name):
    """Return the set of index names currently defined on ``table_name``."""
    if table_name not in inspector.get_table_names():
        return None
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for name, table, columns, kwargs in INDEXES:
        existing = _existing(inspector, table)
        if existing is None or name in existing:
            continue
        op.create_index(name, table, columns, unique=False, **kwargs)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    for name, table, _columns, _kwargs in reversed(INDEXES):
        existing = _existing(inspector, table)
        if existing is None or name not in existing:
            continue
        op.drop_index(name, table_name=table)
