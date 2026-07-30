"""Tests for manual score adjustments (wave 2, phase 5)."""

import pytest

from scoring_engine.db import db
from scoring_engine.models.score_adjustment import ScoreAdjustment
from scoring_engine.models.team import Team
from scoring_engine.scores import team_adjustment_total, team_adjustment_totals


class TestScoreAdjustmentModel:
    def test_create_and_read(self):
        team = Team(name="Blue 1", color="Blue")
        db.session.add(team)
        db.session.commit()
        adj = ScoreAdjustment(team_id=team.id, points=250, reason="Great IR report")
        db.session.add(adj)
        db.session.commit()
        assert adj.id is not None
        assert adj.created_at is not None
        assert adj.team.name == "Blue 1"


class TestAdjustmentTotals:
    def test_net_sum_of_signed_points(self):
        team = Team(name="Blue 1", color="Blue")
        db.session.add(team)
        db.session.commit()
        db.session.add_all(
            [
                ScoreAdjustment(team_id=team.id, points=300, reason="bonus"),
                ScoreAdjustment(team_id=team.id, points=-100, reason="penalty"),
            ]
        )
        db.session.commit()
        assert team_adjustment_total(db.session, team.id) == 200
        assert team_adjustment_totals(db.session) == {team.id: 200}

    def test_no_adjustments(self):
        team = Team(name="Blue 1", color="Blue")
        db.session.add(team)
        db.session.commit()
        assert team_adjustment_total(db.session, team.id) == 0
        assert team_adjustment_totals(db.session) == {}


class TestAdjustmentAdminAPI:
    @pytest.fixture(autouse=True)
    def setup(self, white_login):
        self.client, self.teams = white_login
        self.blue = self.teams["blue_team"]

    def test_create_requires_white_team(self):
        from tests.scoring_engine.conftest import _login

        # setup logged us in as white; the login view ignores a re-login while
        # authenticated, so log out before switching to a blue user.
        self.client.get("/logout")
        _login(self.client, "blueuser")
        resp = self.client.post(
            "/api/admin/adjustments",
            json={"team_id": self.blue.id, "points": 100, "reason": "x"},
        )
        assert resp.status_code == 403

    def test_create_and_list(self):
        resp = self.client.post(
            "/api/admin/adjustments",
            json={"team_id": self.blue.id, "points": 250, "reason": "IR report bonus"},
        )
        assert resp.status_code == 201
        assert resp.json["status"] == "success"

        listing = self.client.get("/api/admin/adjustments").json["data"]
        assert len(listing) == 1
        row = listing[0]
        assert row["team"] == "Blue Team"
        assert row["points"] == 250
        assert row["reason"] == "IR report bonus"
        assert row["author"] == "whiteuser"

    def test_negative_points_allowed(self):
        resp = self.client.post(
            "/api/admin/adjustments",
            json={"team_id": self.blue.id, "points": -75, "reason": "late submission"},
        )
        assert resp.status_code == 201
        assert team_adjustment_total(db.session, self.blue.id) == -75

    def test_reason_is_required(self):
        resp = self.client.post(
            "/api/admin/adjustments",
            json={"team_id": self.blue.id, "points": 100, "reason": "   "},
        )
        assert resp.status_code == 400

    def test_nonexistent_team_rejected(self):
        resp = self.client.post(
            "/api/admin/adjustments",
            json={"team_id": 99999, "points": 100, "reason": "x"},
        )
        assert resp.status_code == 404

    def test_non_integer_points_rejected(self):
        resp = self.client.post(
            "/api/admin/adjustments",
            json={"team_id": self.blue.id, "points": "abc", "reason": "x"},
        )
        assert resp.status_code == 400


class TestAdjustmentInScoreboardTotal:
    def test_adjustment_is_added_to_adjusted_score(self, white_login):
        client, teams = white_login
        blue = teams["blue_team"]
        # No service/inject scores; a pure +500 adjustment should show as the total.
        db.session.add(ScoreAdjustment(team_id=blue.id, points=500, reason="bonus"))
        db.session.commit()

        data = client.get("/api/scoreboard/get_bar_data").json
        idx = data["labels"].index("Blue Team")
        assert data["adjustments"][idx] == "500"
        assert data["adjusted_scores"][idx] == "500"
