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

Example
=======

Retrieve scoreboard data as JSON:

.. code-block:: bash

   curl http://localhost/api/scoreboard/get_bar_data
