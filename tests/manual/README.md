# Manual test scripts

Ad-hoc, operator-run scripts. **These are not part of the automated pytest suite.**

They were written while migrating the web tier from a global SQLAlchemy
`scoped_session` to Flask-SQLAlchemy's per-request sessions, to sanity-check that
one user's data can never leak into another user's request. They are kept because
they are still useful for load/isolation spot-checks against a running instance,
but they need a live server, a seeded database and hand-made test users, so they
cannot run unattended in CI.

## Why pytest ignores this directory

The filenames start with `test_`, so `pytest` would normally try to import and
collect them. `conftest.py` in this directory sets `collect_ignore_glob = ["*.py"]`
to prevent that — without it, `make run-tests` and the CI `pytest tests/` run would
pick up three bogus "tests" that hit a real database and a real HTTP server.

The guard covers directory recursion (`pytest`, `pytest tests/`, `pytest tests/manual`).
Naming a file explicitly (`pytest tests/manual/test_session_isolation.py`) still
collects it — that is a deliberate act, not an accident.

## Contents

| File | What it is | How to run |
| --- | --- | --- |
| `TESTING_SESSION_ISOLATION.md` | The guide: what to look for, manual browser scenarios, common failure signatures | read it first |
| `test_session_isolation.py` | In-process check that each request context gets a fresh session and a cleared identity map. **Destructive: calls `delete_db()` + `init_db()`.** | `python tests/manual/test_session_isolation.py` |
| `test_concurrent_users.py` | Drives N logged-in users concurrently against a running server and asserts nobody sees another user's profile | `python tests/manual/test_concurrent_users.py` |
| `test_simple_concurrent.sh` | Dependency-free `curl` equivalent of the above | `./tests/manual/test_simple_concurrent.sh` |

## Prerequisites

- A running instance on `http://localhost:8000` (`python bin/web`) for the two
  HTTP-driven scripts.
- Test users named `team1_user` / `team2_user` / `team3_user` (password
  `password`), or edit the credentials at the bottom of each script.
- `requests` installed for `test_concurrent_users.py`. It is not a declared
  dependency in `pyproject.toml` or `tests/requirements.txt` — it only shows up
  transitively (via `coveralls`), so install it explicitly if the import fails.
- `test_session_isolation.py` wipes and recreates the database it is pointed at.
  Never run it against anything you care about.
