Configuration
*************

Location to config file
-----------------------
Docker
^^^^^^
.. note:: This file needs to be edited before running the make commands.

::

  <path to source root>/docker/engine.conf.inc

Manual
^^^^^^
.. note:: Need to restart each scoring engine service once the config is modified.

::

  /home/engine/scoring_engine/src/engine.conf


Configuration Keys
------------------
.. note:: Each of these config keys can be expressed via environment variables (and take precendence over the values defined in the file). IE: To define target_round_time, I'd set SCORINGENGINE_TARGET_ROUND_TIME=3.

.. list-table::
   :widths: 25 50
   :header-rows: 1

   * - Key Name
     - Description
   * - checks_location
     - Local path to directory of checks
   * - target_round_time
     - Length of time (seconds) the engine should target per round
   * - agent_psk
     - The pre-shared key used for encryption between BTA and the Scoring Engine
   * - agent_show_flag_early_mins
     - The length of time in minutes before a flag becomes active that BTA can grab the flag details
   * - worker_refresh_time
     - Amount of time (seconds) the engine will sleep for in-between polls of worker status
   * - worker_num_concurrent_tasks
     - The number of concurrent tasks the worker will run. Set to -1 to default to number of processors.
   * - worker_queue
     - The queue name for a worker to pull tasks from. This can be used to control which workers get which service checks. Default is 'main'
   * - blue_team_update_hostname
     - A boolean indicating if blue teams should be allowed to update the hostnames associated for scored checks
   * - blue_team_update_port
     - A boolean indicating if blue teams should be allowed to update the port associated for scored checks
   * - blue_team_update_account_usernames
     - A boolean indicating if blue teams should be allowed to change usernames associated with scored checks
   * - blue_team_update_account_passwords
     - A boolean indicating if blue teams should be allowed to change passwords of scored users
   * - blue_team_view_check_output
     - A boolean indicating if blue teams should be allowed to view verbose output from checks
   * - timezone
     - Local timezone of the competition
   * - debug
     - Determines wether or not the engine should be run in debug mode (useful for development). The worker will also display output from all checks.
   * - secret_key
     - The key Flask uses to sign session cookies. Must be a long random value and must be identical across restarts and across every web process/container. See `Session Secret Key`_.
   * - db_uri
     - Database connection URI
   * - cache_type
     - The type of storage for the cache. Set to null to disable caching
   * - redis_host
     - The hostname/ip of the redis server
   * - redis_port
     - The port of the redis server
   * - redis_password
     - The password used to connect to redis (if no password, leave empty)
   * - sla_enabled
     - A boolean to enable/disable SLA penalties for consecutive service failures
   * - sla_penalty_threshold
     - Number of consecutive failures before penalties begin (default: 5)
   * - sla_penalty_percent
     - Penalty percentage per failure after threshold (default: 10)
   * - sla_penalty_max_percent
     - Maximum total penalty percentage cap (default: 50)
   * - sla_penalty_mode
     - Penalty calculation mode: additive, flat, exponential, or next_check_reduction
   * - sla_allow_negative
     - A boolean to allow scores to go negative from penalties
   * - dynamic_scoring_enabled
     - A boolean to enable/disable time-based scoring multipliers
   * - dynamic_scoring_early_rounds
     - Number of rounds in the early phase (default: 10)
   * - dynamic_scoring_early_multiplier
     - Points multiplier for early phase (default: 2.0)
   * - dynamic_scoring_late_start_round
     - Round number when late phase begins (default: 50)
   * - dynamic_scoring_late_multiplier
     - Points multiplier for late phase (default: 0.5)
   * - inject_scores_visible
     - A boolean to show inject scores on the public scoreboard (default: False, private grading)


Session Secret Key
------------------

The web application signs session cookies with ``secret_key``. It is the only
configuration key that is security critical *and* has no default, so it is worth
calling out separately.

**Why it must be set**

* If the key changes, every existing session cookie becomes invalid and all
  users -- including white team -- are logged out. A key that is generated at
  startup therefore logs everyone out on every restart, redeploy, or crash loop.
* If two web processes or containers hold different keys, a session issued by
  one is rejected by the other. That makes it impossible to run more than one
  web replica behind a load balancer, i.e. it blocks horizontal scaling
  entirely.

**Why there is no default**

No key is shipped in ``engine.conf.inc``. A fixed default that operators forget
to change would let anyone who has read the source forge a session cookie for
any team, including white team. An unset key is a warning; a shared default key
would be a vulnerability.

**Generating a key**

::

  python -c "import secrets; print(secrets.token_hex(64))"

**Setting it**

Docker (recommended -- ``docker/engine.conf.inc`` is baked into the image at
build time, so use the environment instead):

::

  # in .env, next to the other credentials
  SCORINGENGINE_SECRET_KEY=<paste the generated value>

The ``web``, ``engine``, and ``bootstrap`` services in ``docker-compose.yml``
already pass this variable through.

Manual install, in ``engine.conf``:

::

  secret_key = <paste the generated value>

**If it is not set**

The application still starts. It generates a random key for that process and
logs a warning explaining that sessions will not survive a restart and cannot
scale beyond one process. ``bin/setup`` performs the same check and prints a
ready-to-use generated value. This is fine for local development and never fine
for a competition.

.. warning:: Treat the key like a password. Do not commit it, and rotate it if it leaks -- rotating logs everyone out, which is the intended effect.
