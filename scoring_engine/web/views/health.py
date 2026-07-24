"""Liveness and readiness endpoints for orchestrators and load balancers.

Two separate endpoints, because they answer two different questions:

* ``/health`` (liveness) -- "is this process alive?".  It touches nothing
  external, so a dead database never causes the container to be killed and
  restarted, which would not fix anything anyway.
* ``/health/ready`` (readiness) -- "can this process serve real traffic?".
  It verifies every backing service the web app needs (database, cache) and
  returns 503 with per-dependency status when any of them is down, so the
  orchestrator can pull the instance out of rotation.

Design constraints:

* These are polled every few seconds by every orchestrator in the cluster, so
  the checks must stay cheap (``SELECT 1`` / Redis ``PING``) and must never be
  wrapped in ``@cache.cached`` -- a cached readiness response would keep
  reporting "healthy" long after a dependency died, and a readiness check that
  depends on the cache being alive in order to answer is useless.  Responses
  also carry ``Cache-Control: no-store`` so no proxy in front of us caches them.
* They are unauthenticated, so responses must not leak internal detail.  Each
  dependency is reported only as ``healthy`` or ``unhealthy``; hostnames,
  credentials, driver versions and exception text stay in the server log.

Exposure (see ``docker/nginx/files/web.conf``):

* ``/health`` is routed on the public vhost.  A static 200 tells a caller
  nothing they do not already learn from loading any other page.
* ``/health/ready`` is **not**: nginx answers it with a 404 before the request
  reaches this module.  "Is the scoring database up?" is reconnaissance during
  a competition -- it tells a red team when their activity is least likely to
  be recorded.  The container healthcheck reaches this view over uWSGI's
  loopback-only HTTP socket instead, bypassing nginx entirely.
"""

import logging

from flask import Blueprint, jsonify
from sqlalchemy import text

from scoring_engine.cache import cache
from scoring_engine.db import db

logger = logging.getLogger(__name__)

mod = Blueprint("health", __name__)

HEALTHY = "healthy"
UNHEALTHY = "unhealthy"

# Never let a proxy, CDN or browser serve a stale health verdict.
NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


def _response(payload, status_code):
    """Build an uncacheable JSON response."""
    response = jsonify(payload)
    response.status_code = status_code
    response.headers.extend(NO_STORE_HEADERS)
    return response


def check_database():
    """Return True if a trivial query round-trips to the database."""
    try:
        db.session.execute(text("SELECT 1")).scalar()
        return True
    except Exception:
        # Log the real reason server-side; the caller only ever sees "unhealthy".
        logger.warning("Readiness check failed: database unreachable", exc_info=True)
        try:
            # Leave the session usable for the next request.
            db.session.rollback()
        except Exception:
            logger.debug("Readiness check: session rollback failed", exc_info=True)
        return False


def check_cache():
    """Return True if the cache backend is reachable.

    Only Redis-backed caches have anything to probe.  When caching is disabled
    (``cache_type = null``) or in-process (``simple``), there is no external
    dependency, so the check trivially passes.
    """
    try:
        backend = cache.cache
        client = getattr(backend, "_write_client", None)
        if client is None:
            return True
        client.ping()
        return True
    except Exception:
        logger.warning("Readiness check failed: cache unreachable", exc_info=True)
        return False


@mod.route("/health")
def health():
    """Liveness probe: the process is up and serving. No dependency checks."""
    return _response({"status": HEALTHY}, 200)


@mod.route("/health/ready")
def ready():
    """Readiness probe: every backing dependency is reachable."""
    checks = {
        "database": check_database(),
        "cache": check_cache(),
    }
    all_healthy = all(checks.values())
    payload = {
        "status": HEALTHY if all_healthy else UNHEALTHY,
        "checks": {name: HEALTHY if ok else UNHEALTHY for name, ok in checks.items()},
    }
    return _response(payload, 200 if all_healthy else 503)
