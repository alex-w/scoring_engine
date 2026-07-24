"""Keep pytest away from the manual scripts in this directory.

The files here are operator-run scripts, not automated tests. Their names start
with ``test_`` for historical reasons, so pytest would otherwise try to import
and collect them during a normal ``pytest`` / ``make run-tests`` run. They
require a live web server, a seeded database and third-party packages that are
not test dependencies, so collecting them breaks the suite.

``collect_ignore_glob`` applies to this directory only; see
tests/manual/README.md for how to run these scripts by hand.
"""

collect_ignore_glob = ["*.py"]
