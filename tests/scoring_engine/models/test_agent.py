import json
from datetime import datetime

import pytz

from scoring_engine.db import db
from scoring_engine.models.agent import Agent
from scoring_engine.models.flag import FlagTypeEnum, Platform


class TestAgent:
    def test_as_dict(self):
        start = datetime(2024, 1, 1, tzinfo=pytz.UTC)
        end = datetime(2024, 1, 2, tzinfo=pytz.UTC)
        agent = Agent(
            id=1,
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"cmd": "ls"},
            start_time=start,
            end_time=end,
        )
        assert agent.as_dict() == {
            "id": 1,
            "type": "file",
            "data": {"cmd": "ls"},
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
        }

    def test_data_round_trips_through_the_database(self):
        """data is a JSON column, not pickle: it must survive a real round trip."""
        payload = {"cmd": "dir", "args": ["/a", "/b"], "retries": 3, "env": {"X": None}}
        agent = Agent(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data=payload,
            start_time=datetime(2024, 1, 1, tzinfo=pytz.UTC),
            end_time=datetime(2024, 1, 2, tzinfo=pytz.UTC),
        )
        db.session.add(agent)
        db.session.commit()
        agent_id = agent.id
        db.session.expunge_all()

        reloaded = db.session.get(Agent, agent_id)
        assert reloaded.data == payload
        assert reloaded.as_dict()["data"] == payload

    def test_data_is_stored_as_json_text_not_pickle(self):
        """Guards against a regression back to PickleType."""
        agent = Agent(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"cmd": "ls"},
            start_time=datetime(2024, 1, 1, tzinfo=pytz.UTC),
            end_time=datetime(2024, 1, 2, tzinfo=pytz.UTC),
        )
        db.session.add(agent)
        db.session.commit()

        raw = db.session.execute(db.text("SELECT data FROM agents WHERE id = :id"), {"id": agent.id}).scalar()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        assert json.loads(raw) == {"cmd": "ls"}
