"""Accessibility landmarks every page inherits from base.html.

A skip-to-content link and a labelled ``<main>`` landmark are keyboard and
screen-reader essentials. They live in base.html, so a single guard here covers
every page and stops a future base.html rewrite from silently dropping them.
"""

import pytest


class TestAccessibilityLandmarks:
    @pytest.fixture(autouse=True)
    def setup(self, test_client, db_session):
        self.client = test_client

    def _body(self, path="/scoreboard"):
        resp = self.client.get(path)
        assert resp.status_code == 200
        return resp.data.decode()

    def test_skip_link_targets_main_content(self):
        body = self._body()
        # A keyboard user's first Tab should reveal a jump to the content.
        assert 'href="#main-content"' in body
        assert "visually-hidden-focusable" in body

    def test_main_landmark_has_the_matching_id(self):
        body = self._body()
        assert 'id="main-content"' in body

    def test_primary_nav_is_labelled(self):
        # Distinguishes the navigation landmark for assistive tech.
        body = self._body()
        assert 'aria-label="Main navigation"' in body
