from datetime import datetime, timezone

import pytz
from sqlalchemy import Column, DateTime, Integer
from sqlalchemy.orm import relationship

from scoring_engine.config import config
from scoring_engine.datetime_utils import ensure_utc_aware
from scoring_engine.db import db
from scoring_engine.models.base import Base


class Round(Base):
    __tablename__ = "rounds"
    id = Column(Integer, primary_key=True)
    number = Column(Integer, nullable=False)
    checks = relationship("Check", back_populates="round")
    round_start = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    round_end = Column(DateTime)

    @staticmethod
    def get_last_round_num():
        round_obj = db.session.query(Round.number).order_by(Round.number.desc()).first()
        if round_obj is None:
            return 0
        else:
            return round_obj.number

    @property
    def local_round_start(self):
        return (
            ensure_utc_aware(self.round_start)
            .astimezone(pytz.timezone(config.timezone))
            .strftime("%Y-%m-%d %H:%M:%S %Z")
        )
