import html
from datetime import datetime, timezone

import pytz
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Text, UnicodeText
from sqlalchemy.orm import relationship

from scoring_engine.config import config
from scoring_engine.datetime_utils import ensure_utc_aware
from scoring_engine.models.base import Base


class Check(Base):
    __tablename__ = "checks"
    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey("rounds.id"))
    round = relationship("Round", back_populates="checks")
    service_id = Column(Integer, ForeignKey("services.id"))
    service = relationship("Service")
    result = Column(Boolean)
    output = Column(UnicodeText, default="")
    reason = Column(Text, default="")
    command = Column(Text, default="")
    completed_timestamp = Column(DateTime)
    completed = Column(Boolean, default=False)

    def finished(self, result, reason, output, command):
        self.result = result
        self.reason = reason
        self.output = html.escape(output)
        self.completed = True
        self.completed_timestamp = datetime.now(timezone.utc)
        self.command = command

    @property
    def local_completed_timestamp(self):
        return (
            ensure_utc_aware(self.completed_timestamp)
            .astimezone(pytz.timezone(config.timezone))
            .strftime("%Y-%m-%d %H:%M:%S %Z")
        )
