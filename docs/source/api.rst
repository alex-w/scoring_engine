*************
API
*************

The Scoring Engine exposes a JSON-based API used by the web interface and
automation. These endpoints allow programmatic access to scoring data and
engine management.

Scoreboard
==========

* ``/api/scoreboard/get_bar_data`` – aggregate team scores.
* ``/api/scoreboard/get_line_data`` – per-round scoring trends.

Teams and Services
==================

* ``/api/team/<team_id>/stats`` – statistics for a team's services.
* ``/api/service/<service_id>/checks`` – check history for a service.

Administration
==============

Administrative endpoints support managing competition settings, such as
updating service properties and toggling the engine. These APIs are
secured and intended for white team use.

Flags and Injects
=================

Endpoints under ``/api/flags`` and ``/api/admin/injects`` support
capture-the-flag style challenges and graded injects.

Health Checks
=============

Two unauthenticated endpoints are provided for orchestrators, load balancers
and container healthchecks. Neither is cached, so both always reflect the
current state.

* ``/health`` – liveness. Returns ``200`` with ``{"status": "healthy"}`` as
  long as the process can serve requests. It performs no dependency checks, so
  a failing database never causes the container to be restarted.
* ``/health/ready`` – readiness. Verifies database and cache (Redis)
  connectivity. Returns ``200`` when every dependency is reachable, otherwise
  ``503`` with the per-dependency verdict:

  .. code-block:: json

     {
       "status": "unhealthy",
       "checks": {"database": "unhealthy", "cache": "healthy"}
     }

  Dependencies are reported only as ``healthy`` or ``unhealthy``. Because the
  endpoint is unauthenticated, hostnames, credentials and exception details are
  written to the server log instead of the response.

Exposure
--------

The two endpoints are exposed differently on purpose.

``/health`` is reachable through nginx on the public vhost. It reveals nothing
an anonymous visitor cannot already learn by loading any other page, and an
external load balancer needs it to take a wedged instance out of rotation.

``/health/ready`` is **not** served on the public vhost — nginx answers it with
a ``404`` before the request reaches the application. Its response says whether
the scoring database and Redis are up, which during a competition is
reconnaissance: it tells a red team exactly when the scoring infrastructure is
degraded, i.e. when their activity is least likely to be recorded.

Nothing outside the container needs the public route. The bundled
``docker-compose`` healthcheck curls ``http://127.0.0.1:5001/health/ready`` on
uWSGI's loopback-only HTTP socket, inside the ``web`` container, and never goes
through nginx.

If an external orchestrator has to poll readiness, edit the
``location = /health/ready`` block in ``docker/nginx/files/web.conf`` and
replace the ``return 404`` with an allow list naming the orchestrator's
addresses. Do not simply delete the block: the request would then fall through
to the catch-all ``location /`` and readiness would be public again. Allow-list
specific addresses rather than all of RFC1918 — with Docker's userland proxy
every request can appear to come from the bridge gateway.

Example
=======

Retrieve scoreboard data as JSON:

.. code-block:: bash

   curl http://localhost/api/scoreboard/get_bar_data
