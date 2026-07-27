"""Tests for the competition-window admin CRUD API.

The engine/schedule logic is covered in test_schedule.py; here we cover the HTTP
surface: white-team authorization, create/list/delete/toggle, input validation,
and the naive->competition-tz->UTC parsing (mirroring the freeze endpoint).
"""

import pytest

from scoring_engine.db import db
from scoring_engine.models.competition_window import CompetitionWindow


class TestWindowEndpoints:
    @pytest.fixture(autouse=True)
    def setup(self, white_login):
        self.client, self.teams = white_login

    def test_list_empty_by_default(self):
        resp = self.client.get("/api/admin/windows")
        assert resp.status_code == 200
        assert resp.json == {"windows": []}

    def test_create_and_list(self):
        resp = self.client.post(
            "/api/admin/windows",
            json={"name": "Day 1", "start_time": "2026-07-27T09:00:00", "end_time": "2026-07-27T17:00:00"},
        )
        assert resp.status_code == 200
        assert resp.json["status"] == "success"
        assert resp.json["window"]["name"] == "Day 1"

        listing = self.client.get("/api/admin/windows").json["windows"]
        assert len(listing) == 1
        assert listing[0]["enabled"] is True
        # Input is interpreted in the competition timezone (US/Eastern in tests)
        # and stored naive-UTC; the display localizes back to the entered time.
        assert "09:00:00" in listing[0]["start_display"]
        assert "17:00:00" in listing[0]["end_display"]

    def test_create_rejects_end_before_start(self):
        resp = self.client.post(
            "/api/admin/windows",
            json={"start_time": "2026-07-27T17:00:00", "end_time": "2026-07-27T09:00:00"},
        )
        assert resp.status_code == 400
        assert db.session.query(CompetitionWindow).count() == 0

    def test_create_rejects_equal_start_end(self):
        resp = self.client.post(
            "/api/admin/windows",
            json={"start_time": "2026-07-27T09:00:00", "end_time": "2026-07-27T09:00:00"},
        )
        assert resp.status_code == 400

    def test_create_rejects_missing_times(self):
        resp = self.client.post("/api/admin/windows", json={"name": "no times"})
        assert resp.status_code == 400

    def test_delete(self):
        wid = self.client.post(
            "/api/admin/windows",
            json={"start_time": "2026-07-27T09:00:00", "end_time": "2026-07-27T17:00:00"},
        ).json["window"]["id"]
        resp = self.client.delete(f"/api/admin/windows/{wid}")
        assert resp.status_code == 200
        assert self.client.get("/api/admin/windows").json["windows"] == []

    def test_delete_missing_is_404(self):
        assert self.client.delete("/api/admin/windows/99999").status_code == 404

    def test_toggle_flips_enabled(self):
        wid = self.client.post(
            "/api/admin/windows",
            json={"start_time": "2026-07-27T09:00:00", "end_time": "2026-07-27T17:00:00"},
        ).json["window"]["id"]
        # Default enabled -> PATCH with no body flips to disabled.
        resp = self.client.patch(f"/api/admin/windows/{wid}")
        assert resp.status_code == 200
        assert resp.json["window"]["enabled"] is False
        # Explicit enable.
        resp = self.client.patch(f"/api/admin/windows/{wid}", json={"enabled": True})
        assert resp.json["window"]["enabled"] is True

    def test_requires_white_team(self):
        self.client.get("/logout")
        from tests.scoring_engine.conftest import _login

        _login(self.client, "blueuser")
        assert self.client.get("/api/admin/windows").status_code == 403
        assert (
            self.client.post(
                "/api/admin/windows",
                json={"start_time": "2026-07-27T09:00:00", "end_time": "2026-07-27T17:00:00"},
            ).status_code
            == 403
        )
        assert self.client.delete("/api/admin/windows/1").status_code == 403


class TestWindowEndpointsAuth:
    def test_requires_login(self, test_client):
        assert test_client.get("/api/admin/windows").status_code == 302
