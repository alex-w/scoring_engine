import pytest
from flask_caching.backends.simplecache import SimpleCache
from sqlalchemy.exc import OperationalError

from scoring_engine.cache import cache
from scoring_engine.db import db
from scoring_engine.web.views import health


def _patch_cache_backend(monkeypatch, redis_client):
    """Swap in a fake Redis-backed cache backend for the health blueprint.

    The test config uses ``cache_type = null``, which has no remote client to
    probe, so a stand-in is needed to exercise the Redis path.
    """

    class FakeBackend:
        _write_client = redis_client

    class FakeCache:
        cache = FakeBackend()

    monkeypatch.setattr(health, "cache", FakeCache())


@pytest.fixture()
def working_cache_backend(app, monkeypatch):
    """Give the app a cache backend that actually caches, for one test.

    The test config uses ``cache_type = null``.  With ``NullCache`` the
    ``@cache.cached`` decorator stores nothing and every call is a miss, so any
    "the response is not cached" assertion passes whether or not the view is
    decorated -- the test cannot fail.  ``SimpleCache`` is in-process (no Redis
    needed) and caches for real, which is what gives those assertions teeth.

    ``Cache.cache`` resolves the backend per request via
    ``current_app.extensions["cache"][self]``, so swapping that entry is enough
    to change the behaviour of an already-applied ``@cache.cached`` decorator.
    """
    monkeypatch.setitem(app.extensions["cache"], cache, SimpleCache())
    return app.extensions["cache"][cache]


class TestHealth:

    @pytest.fixture(autouse=True)
    def setup(self, test_client, db_session):
        self.client = test_client

    # ------------------------------------------------------------------
    # Liveness
    # ------------------------------------------------------------------

    def test_liveness_returns_200(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        assert resp.json == {"status": "healthy"}

    def test_liveness_requires_no_auth(self):
        # No login performed by this test class -- a 200 (not a 302 to /login)
        # proves the endpoint is unauthenticated.
        assert self.client.get("/health").status_code == 200

    def test_liveness_is_not_cacheable(self):
        resp = self.client.get("/health")
        assert "no-store" in resp.headers["Cache-Control"]

    def test_liveness_does_not_touch_the_database(self, monkeypatch):
        """Liveness must stay up even when every dependency is down."""

        def boom(*args, **kwargs):
            raise AssertionError("liveness must not query the database")

        monkeypatch.setattr(db.session, "execute", boom)
        assert self.client.get("/health").status_code == 200

    # ------------------------------------------------------------------
    # Readiness -- happy path
    # ------------------------------------------------------------------

    def test_readiness_returns_200_when_dependencies_are_up(self):
        resp = self.client.get("/health/ready")
        assert resp.status_code == 200
        assert resp.json == {
            "status": "healthy",
            "checks": {"database": "healthy", "cache": "healthy"},
        }

    def test_readiness_is_not_cacheable(self):
        resp = self.client.get("/health/ready")
        assert "no-store" in resp.headers["Cache-Control"]

    @pytest.mark.parametrize("path", ["/health/ready/", "/HEALTH/READY", "/health/ready/x"])
    def test_readiness_has_no_alternate_spelling(self, path):
        """nginx hides /health/ready from the public vhost with an exact-match
        location (``location = /health/ready``, see docker/nginx/files/web.conf).

        Anything nginx does not match exactly falls through to the catch-all and
        does reach the app, so the app must not answer readiness under a second
        spelling -- that would be a trivial bypass of the nginx block.
        """
        assert self.client.get(path).status_code == 404

    def test_the_caching_harness_can_actually_detect_a_cached_view(self, app, working_cache_backend):
        """Guard for the guard.

        ``test_readiness_is_not_served_from_cache`` is only meaningful if
        ``@cache.cached`` really caches under ``working_cache_backend``.  Prove
        that here on a throwaway view: if this ever stops holding, the tests
        below silently stop testing anything and this one fails loudly instead.
        """
        calls = []

        @cache.cached(timeout=60, key_prefix="health-test-canary")
        def canary():
            calls.append(1)
            return "call-{0}".format(len(calls))

        with app.test_request_context("/health/ready"):
            first = canary()
            second = canary()

        assert first == "call-1"
        assert second == "call-1", "backend under test is not caching -- the cache tests are toothless"
        assert len(calls) == 1

    def test_readiness_is_not_served_from_cache(self, monkeypatch, working_cache_backend):
        """A stale 200 would keep a broken instance in rotation.

        Runs against a real (in-process) cache backend, so wrapping ``ready()``
        in ``@cache.cached`` would make the second request replay the first
        request's 200 and fail this test.
        """
        assert self.client.get("/health/ready").status_code == 200

        monkeypatch.setattr(db.session, "execute", self._db_failure)
        resp = self.client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json["checks"]["database"] == "unhealthy"

    def test_liveness_is_not_served_from_cache(self, monkeypatch, working_cache_backend):
        """Liveness must reflect the process, not a stored copy of an old answer.

        The liveness body is a constant, so vary the constant between the two
        requests: a cached response would replay the first one.
        """
        assert self.client.get("/health").json == {"status": "healthy"}

        monkeypatch.setattr(health, "HEALTHY", "second-call")
        assert self.client.get("/health").json == {"status": "second-call"}

    @pytest.mark.parametrize("endpoint", ["health.health", "health.ready"])
    def test_health_views_are_not_wrapped_by_flask_caching(self, app, endpoint):
        """Structural check, independent of which backend is configured.

        ``flask_caching``'s ``cached``/``memoize`` decorators attach ``uncached``
        and ``make_cache_key`` to the function they wrap.  Their absence is
        proof the view runs on every request, no matter how the deployment has
        ``cache_type`` set.
        """
        view = app.view_functions[endpoint]
        assert not hasattr(view, "uncached")
        assert not hasattr(view, "make_cache_key")
        assert not hasattr(view, "cache_timeout")

    # ------------------------------------------------------------------
    # Readiness -- failure paths
    # ------------------------------------------------------------------

    @staticmethod
    def _db_failure(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("could not connect to db-host:3306"))

    def test_readiness_returns_503_when_database_is_unreachable(self, monkeypatch):
        monkeypatch.setattr(db.session, "execute", self._db_failure)

        resp = self.client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json["status"] == "unhealthy"
        assert resp.json["checks"]["database"] == "unhealthy"
        assert resp.json["checks"]["cache"] == "healthy"

    def test_readiness_does_not_leak_internals_on_failure(self, monkeypatch):
        monkeypatch.setattr(db.session, "execute", self._db_failure)

        resp = self.client.get("/health/ready")
        body = resp.get_data(as_text=True)
        assert "db-host" not in body
        assert "3306" not in body
        assert "OperationalError" not in body
        assert "Traceback" not in body

    def test_readiness_returns_503_when_cache_is_unreachable(self, monkeypatch):
        class FailingRedis:
            def ping(self):
                raise ConnectionError("Error connecting to redis-host:6379")

        _patch_cache_backend(monkeypatch, FailingRedis())

        resp = self.client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json["status"] == "unhealthy"
        assert resp.json["checks"]["cache"] == "unhealthy"
        assert resp.json["checks"]["database"] == "healthy"
        assert "redis-host" not in resp.get_data(as_text=True)

    def test_readiness_reports_every_failing_dependency(self, monkeypatch):
        class FailingRedis:
            def ping(self):
                raise ConnectionError("no redis")

        monkeypatch.setattr(db.session, "execute", self._db_failure)
        _patch_cache_backend(monkeypatch, FailingRedis())

        resp = self.client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json["checks"] == {"database": "unhealthy", "cache": "unhealthy"}

    # ------------------------------------------------------------------
    # Dependency check helpers
    # ------------------------------------------------------------------

    def test_check_cache_passes_when_backend_has_no_remote_client(self):
        """null/simple caches are in-process -- nothing to probe, nothing to fail."""
        assert health.check_cache() is True

    def test_check_cache_pings_the_redis_client(self, monkeypatch):
        pings = []

        class FakeRedis:
            def ping(self):
                pings.append(True)
                return True

        _patch_cache_backend(monkeypatch, FakeRedis())

        assert health.check_cache() is True
        assert pings == [True]

    def test_redis_backend_still_exposes_the_client_attribute_check_cache_uses(self):
        """Contract test against flask-caching.

        ``check_cache`` reaches for the private ``_write_client`` attribute. If a
        future flask-caching/cachelib release renames it, the readiness check
        would silently degrade into "always healthy" -- fail here instead.
        """
        from flask_caching.backends.rediscache import RedisCache

        # Constructing the client is lazy; no connection is opened here.
        assert getattr(RedisCache(host="127.0.0.1", port=6379), "_write_client", None) is not None

    def test_check_database_leaves_session_usable_after_failure(self, monkeypatch):
        monkeypatch.setattr(db.session, "execute", self._db_failure)
        assert health.check_database() is False

        monkeypatch.undo()
        assert health.check_database() is True
