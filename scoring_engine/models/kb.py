from sqlalchemy import Column, Index, Integer, String, Text

from scoring_engine.models.base import Base


class KB(Base):
    __tablename__ = "kb"
    # /api/admin/get_round_progress is polled every 3s by the admin status page
    # and runs WHERE name = 'task_ids' ORDER BY round_num DESC LIMIT 1.
    # kb grows by one row per round, so this was a scan + filesort.
    __table_args__ = (Index("ix_kb_name_round_num", "name", "round_num"),)
    id = Column(Integer, primary_key=True)
    round_num = Column(Integer)
    name = Column(String(100), nullable=False)
    value = Column(Text, nullable=False)
