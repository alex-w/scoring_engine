import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, UnicodeText

from scoring_engine.models.base import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # scoring_engine/notifications.py create_notification() de-dupes with
        # WHERE team_id = ? AND ... AND created >= cutoff on every inject event.
        Index("ix_notifications_team_id_created", "team_id", "created"),
        # /api/notifications/read, /unread and the read-all UPDATE all filter
        # WHERE team_id = ? AND is_read = ?.
        Index("ix_notifications_team_id_is_read", "team_id", "is_read"),
    )
    id = Column(Integer, primary_key=True)
    message = Column(UnicodeText)
    target = Column(UnicodeText)
    created = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))
    is_read = Column(Boolean, default=False)

    # Foreign Keys
    team_id = Column(Integer, ForeignKey("teams.id"))
