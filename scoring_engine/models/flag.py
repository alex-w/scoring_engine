import enum
import html
import uuid
from datetime import datetime, timezone

import pytz
from sqlalchemy import JSON, Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint

# from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from scoring_engine.config import config
from scoring_engine.datetime_utils import ensure_utc_aware
from scoring_engine.models.base import Base
from scoring_engine.models.team import Team


class FlagTypeEnum(enum.Enum):
    file = "file"
    pipe = "pipe"
    net = "net"
    reg = "reg"


class Platform(enum.Enum):
    windows = "win"
    nix = "nix"


class Perm(enum.Enum):
    user = "user"
    root = "root"


class Flag(Base):
    __tablename__ = "flags"
    # id = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(Enum(FlagTypeEnum), nullable=False)
    platform = Column(Enum(Platform), nullable=False)
    # JSON rather than PickleType: pickle in the DB is a deserialization risk and
    # cannot be queried or migrated. SQLAlchemy's generic JSON type renders as a
    # native JSON column on MySQL/MariaDB and as TEXT-with-JSON-serialization on SQLite.
    data = Column(JSON, nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    perm = Column(Enum(Perm), nullable=False)
    dummy = Column(Boolean, nullable=False, default=False)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "data": self.data,
            "platform": self.platform.value,
            "start_time": int(ensure_utc_aware(self.start_time).timestamp()),
            "end_time": int(ensure_utc_aware(self.end_time).timestamp()),
            "perm": self.perm.value,
            "dummy": self.dummy,
        }

    @property
    def localize_start_time(self):
        return (
            ensure_utc_aware(self.start_time)
            .astimezone(pytz.timezone(config.timezone))
            .strftime("%Y-%m-%d %H:%M:%S %Z")
        )

    @property
    def localize_end_time(self):
        return (
            ensure_utc_aware(self.end_time).astimezone(pytz.timezone(config.timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")
        )


class Solve(Base):
    __tablename__ = "flag_solves"
    __table_args__ = (
        UniqueConstraint("flag_id", "host", "team_id", name="_flag_host_team_uc"),
        # The unique constraint above is flag_id-leading, so it cannot serve
        # lookups keyed on (host, team_id).  Those happen on every agent
        # check-in (api/agent.py do_checkin's NOT IN subquery) and on the
        # api/flags.py solves outer join.  ``host`` is String(260); a 191-char
        # MySQL prefix keeps the key well under InnoDB's limit while staying
        # fully selective for hostnames/IPs.  mysql_length is ignored by SQLite.
        Index(
            "ix_flag_solves_host_team_id",
            "host",
            "team_id",
            mysql_length={"host": 191},
        ),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    host = Column(String(260), nullable=False)
    flag_id = Column(String(36), ForeignKey("flags.id"))
    team_id = Column(Integer, ForeignKey("teams.id"))
    # When the capture was recorded. Basis for the wall-clock scoreboard freeze:
    # a frozen view counts only solves created at/before the freeze time.
    created_at = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    flag = relationship("Flag", backref="solves", lazy="joined")
    team = relationship("Team", backref="flag_solves", lazy="joined")
