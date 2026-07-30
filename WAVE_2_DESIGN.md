# Wave 2 Design — `round_score` materialization and what it unblocks

Status: design, not yet implemented. Author pass: 2026-07-24.

Wave 1 hardened the existing engine. Wave 2 changes the **scoring model** itself.
Four features the maintainer wants — red-team scoring, competition freeze, manual
score adjustments, and the performance fix for large competitions — all depend on
one thing that does not exist yet: a durable, per-round record of what each team
scored. This document designs that record (`round_score`), then shows how each
feature sits on top of it.

Read this before writing any wave-2 code. The schema decisions here are load-bearing.

---

## 1. The problem

Every score in the app is recomputed live from the full `checks` history on every
read. There is no stored "team X earned Y in round Z" anywhere.

Concretely, today:

| Read path | How it computes | Cost |
|---|---|---|
| `Team.current_score` | `SUM(Service.points)` ⋈ `Check` WHERE `result=True` | full history |
| `Team.current_inject_score` | `SUM(InjectRubricScore.score)` WHERE `status='Graded'` | full inject history |
| `Team.place` / `Service.rank` | re-queries **all** teams/services per access | full history × N teams |
| `sla.calculate_team_total_penalties` | loops every service, **2 full-history queries each** | services × 2 × history |
| `sla.calculate_team_base_score_with_dynamic` | `GROUP BY round`, applies multipliers live | full history |
| flag `Solve` rows | **not scored anywhere** | — |

This has three consequences, and wave 2 is really about the second and third:

1. **Performance.** `calculate_team_total_penalties` at 100 teams × 20 services is
   ~4,000 full-history queries for one uncached scoreboard render, and
   `update_all_cache` calls `cache.clear()` every round, so the first request after
   each round pays it on every endpoint. Wave 1's indexes soften this; they do not
   remove it.
2. **You cannot freeze, adjust, or attribute a score you recompute from scratch
   every time.** There is no row to freeze, no row to add an adjustment beside, no
   place to record "red team scored 50 here."
3. **Editing `Service.points` retroactively rewrites all history**, because history
   is just `COUNT(passing checks) × current points`. There is no record of what a
   check was worth *when it was scored*.

`round_score` fixes all three by writing the facts down once, at round close.

---

## 2. Constraints discovered while reading the code

These are non-negotiable; the design is shaped around them.

- **Rollback contract** (`feature/score-rollback`, already in master).
  `admin_rollback` bulk-deletes `Round`, `Check`, and `KB` rows where
  `round >= N` in batches, then calls `update_all_cache`. **Any table keyed by
  round must be deleted in the same operation, or a rolled-back competition keeps
  ghost scores.** The engine's own failed-round cleanup
  (`_cleanup_failed_round`) has the identical requirement.
- **Weighted scoring already anticipates flags** (`feature/weighted-scoring`,
  stale/unmerged). It ships `weighted_scoring_enabled`, `service_weight`,
  `inject_weight`, `flag_weight` config. `flag_weight` scores nothing today — it is
  a placeholder for exactly the red-team scoring wave 2 adds. That branch's
  `scoreboard.py` predates the anonymize refactor and **cannot be merged as-is**;
  harvest its config surface, not its code.
- **Airgapped + SQLite tests.** No new runtime deps. Every migration must run under
  `batch_alter_table` for SQLite, and the test suite (SQLite) must exercise the new
  tables.
- **Client-side rendering rule** (CLAUDE.md). New score data reaches the browser
  through cached `/api/...` endpoints keyed per visibility, never server-rendered.
- **Settings cache** has a 60s TTL and must be cleared after mutation
  (`Setting.clear_cache`).

---

## 3. Proposed schema

Two new tables. One is engine-written fact; the other is operator-written audit.
Keeping them separate is deliberate — they have different authors, different
lifecycles, and different rollback behaviour.

### 3.1 `round_score` — the materialized fact table

Written **once per team per round**, at round close, by the engine. Never updated
after write (a corrected round is rolled back and re-run, never patched in place).

```
round_score
  id              INTEGER PK
  round_id        FK -> rounds.id      (ON DELETE cascade-in-app, see §7)
  round_number    INTEGER  (denormalized: lets reads filter by number without a join)
  team_id         FK -> teams.id
  service_points  INTEGER  NOT NULL DEFAULT 0   -- raw uptime points earned this round
  flag_points     INTEGER  NOT NULL DEFAULT 0   -- red-team points from solves this round (§6)
  UNIQUE (round_id, team_id)
  INDEX (team_id, round_number)
  INDEX (round_number)
```

Design choices, with reasons:

- **Store RAW earned points, not weighted/multiplied totals.** Weights
  (`service_weight`, `flag_weight`) and the dynamic per-round multiplier are
  *policy* an admin may tune mid-competition; the current app applies them
  retroactively and we preserve that. Applying them at read time over ~`N_rounds`
  rows per team is cheap (scalar math, no history scan). Baking them in would lock
  past rounds and make a weight change silently not apply — the opposite of what
  `feature/weighted-scoring` intends.
- **Per (team, round), not per (team, round, service).** The hot reads are
  team-level totals and ranks. Per-service granularity already lives in `checks`
  (now indexed); duplicating it here just re-creates the N+1. Service-level views
  (`Service.rank`, per-service history) keep reading `checks`.
- **`round_number` denormalized.** `Round.get_last_round_num`, freeze filters, and
  dynamic-multiplier lookups all key on the number, not the id. Carrying it avoids
  a join on every read. It is immutable once written.
- **Injects are deliberately NOT a column here.** Injects are graded asynchronously,
  not per round — folding them into a per-round row would mean rewriting old rows on
  every grade. Inject totals stay a separate small aggregate (see §5). `round_score`
  is strictly the *engine-per-round* fact.

### 3.2 `score_adjustment` — manual white-team adjustments (audit log)

```
score_adjustment
  id           INTEGER PK
  team_id      FK -> teams.id
  points       INTEGER  NOT NULL        -- signed: +bonus or -penalty
  reason       TEXT     NOT NULL        -- required; shown in audit view
  author_id    FK -> users.id           -- who applied it
  created_at   DATETIME NOT NULL
  effective_round INTEGER NULL          -- for freeze: counts only if <= frozen round
  INDEX (team_id)
```

Separate table because: it is operator-authored (not engine-derived), it is an
**append-only audit trail** (you never silently edit a competition score; you add a
compensating row), and it must **survive `admin_rollback`** — rolling back rounds
should not erase a manual ruling unless the operator says so. (`effective_round`
lets a specific adjustment ride along with a rollback if desired; default is to keep
adjustments.)

---

## 4. Write path — where `round_score` rows are created

**One hook, in the engine, at round close.** In `engine.py`, the round loop
processes all check results and commits at what is currently ~line 678
(`round_obj.round_end = ...; self.db.session.commit()`). Immediately after that
commit, while we already hold every processed check in memory, aggregate per team
and insert `round_score` rows in the same transaction boundary:

```
# after checks for this round are committed
per_team = defaultdict(lambda: {"service": 0, "flag": 0})
for check that passed:
    per_team[team_id]["service"] += check.service.points
for solve created in this round window (§6):
    per_team[team_id]["flag"] += flag_value(solve)
bulk-insert one round_score row per team for round_obj
commit
```

Critical properties:

- **Idempotent per round.** The `UNIQUE(round_id, team_id)` guard means a retried
  round-close cannot double-write. On the failed-round path
  (`_cleanup_failed_round`), delete this round's `round_score` rows alongside the
  `Round`/`Check` deletion so a discarded round leaves nothing behind.
- **Same transaction as the round.** A `round_score` row exists **iff** its round
  exists. No window where the scoreboard sees a round with no scores or scores with
  no round.
- **Written before `update_all_cache`.** The cache flush at round close then rebuilds
  from the materialized rows, not the live history.

Backfill for existing competitions is a migration concern (§8), not a runtime one.

---

## 5. Read path — what each current computation becomes

New helpers in a `scoring_engine/scores.py` module (keeps `sla.py` focused on
penalties). Every current read-path is redirected:

| Today | Becomes |
|---|---|
| `Team.current_score` | `SUM(round_score.service_points × service_weight × dyn_mult(round))` for team |
| red-team / flag total (new) | `SUM(round_score.flag_points × flag_weight × dyn_mult(round))` |
| `Team.place`, `Service.rank` | one `GROUP BY team_id` over `round_score` (was per-team full history) |
| `calculate_team_base_score_with_dynamic` | reads `round_score`, applies multiplier per stored `round_number` |
| inject total | unchanged — `SUM(InjectRubricScore.score)` WHERE `Graded` (small table) |
| manual total (new) | `SUM(score_adjustment.points)` for team |
| `get_consecutive_failures` (SLA) | **still reads `checks`** — needs per-service granularity round_score doesn't hold; wave-1 index makes it cheap |

Team grand total (the number on the scoreboard) becomes:

```
weighted_service + weighted_flag + weighted_inject + manual_adjustments − sla_penalty
```

where SLA penalty is still computed from `checks` (consecutive failures) but its
*base* (the score it takes a percentage of) now comes from `round_score` instead of
another full-history scan — this is the single biggest perf win, killing the
services × 2 × history loop in `calculate_team_total_penalties`.

The `dyn_mult(round)` and `service_weight`/`flag_weight` factors are applied in
Python over the per-round rows, exactly as `apply_dynamic_scoring_to_round` does
today — so dynamic scoring and weighted scoring keep working, retroactively, with no
history scan.

---

## 6. Red-team / flag scoring (the feature `flag_weight` was waiting for)

Semantics, confirmed from the code: a `Solve(host, team, flag)` row is created when a
blue team's BTA agent reports a red-team flag present on one of its hosts
(`api/agent.py` `agent_checkin_post`). A present flag is an indicator of compromise:
**a point for red, a compromise signal against blue.** Flags carry a `perm` level
(`user` / `root`) — root compromise is worth more.

Design:

- Each flag has a point value (start simple: a config pair
  `flag_points_user` / `flag_points_root`, mirroring the existing weight config;
  or a per-flag column later).
- At round close, `round_score.flag_points` for a team = sum over that team's solves
  **whose flag became active in this round window** of the flag's value. Scoring at
  round close (not at check-in) keeps flags on the same materialized cadence as
  services and makes them roll back for free.
- **Red team total** = `SUM` of all blue teams' `flag_points` (red scores by
  compromising anyone), surfaced on a red-team leaderboard.
- **Blue exposure** = a team's own `flag_points` shown as a compromise count /
  optional penalty (policy toggle — some competitions score red purely offensively,
  others also penalize blue).

Open question for the maintainer (see §11): does a captured flag *subtract* from the
blue team's score, or only *add* to red's? Both are legitimate competition rules; the
schema supports either (it stores the raw flag_points; the read path decides sign).

### Implementation note (phase 4, as built)

Implementing this revealed that the "materialize `flag_points` into `round_score` at
round close" plan does not fit the data: a `Solve` row has **no round and no
timestamp** (just `flag_id`, `host`, `team_id`), and it is created asynchronously on
agent check-in, while the engine creates the `Round` row only at round *close*. There
is no reliable way to attribute a solve to a round after the fact. The `checks`-volume
performance argument also does not apply — the `flag_solves` table is small.

So flags are scored **live from the `solves` table**, the way injects are scored
(an event-driven component), not materialized per round:

- `scoring_engine.scores.flag_points_by_team()` → `{blue_team_id: captured value}`,
  each non-dummy solve worth `flag_points_user`/`flag_points_root` by its flag's perm.
- `red_team_flag_total()` → sum across all blue teams (add-to-red-only, resolved §11).
- Surfaced by `/api/flags/score` (red/white only) and a card on the flags page.

`round_score.flag_points` therefore stays `0` (reserved). It is left in place rather
than dropped with another migration; if per-solve round attribution is ever wanted, a
`Solve.round_number` (or `created_at`) column would enable materialization here.
Wall-clock **freeze** (phase 6) will filter solves by a `Solve.created_at` added then;
until that column exists, flag freeze is best-effort. Rollback does not currently
delete solves (they are red captures, independent of blue's service rounds).

---

## 7. Rollback, freeze, and cache — the three cross-cutting contracts

**Rollback.** Extend `admin_rollback` and the engine's `_cleanup_failed_round` to
delete `round_score` WHERE `round_number >= N` in the same batched transaction as
`checks`. `score_adjustment` rows are kept by default (audit), with an optional
"also remove adjustments effective at/after round N" checkbox driven by
`effective_round`. Add `round_score` counts to the rollback preview/summary so an
operator sees what will be discarded.

**Freeze.** A new `Setting`, `scoreboard_frozen_round` (nullable int). When set, every
read helper adds `WHERE round_number <= frozen_round`, injects filter on
`graded_at <= freeze_time`, and adjustments on `effective_round <= frozen_round`. The
engine keeps scoring rounds past the freeze (so the competition continues and can be
un-frozen), but the public scoreboard reflects the frozen instant. White team sees a
"FROZEN at round N" banner and the live numbers. Because the read path is already
centralized in `scores.py`, freeze is one `WHERE` clause in one place — this is a
direct payoff of materializing.

**Cache.** All new mutations (round close, manual adjustment, freeze toggle,
rollback) call the existing `cache_helper` invalidators. Add
`update_scores_data()` / `update_flags_score_data()` helpers mirroring the existing
per-visibility flush pattern. Adjustment and freeze are white-team actions → flush
overview, scoreboard, team, stats caches.

---

## 8. Migration and backfill (`alembic` revision 005)

- Create both tables via `batch_alter_table` (SQLite-safe), following the
  wave-1 `003`/`004` style. `005` chains off `004`.
- **Backfill `round_score` from existing `checks`** so a mid-competition upgrade
  keeps its scoreboard: for every existing round, `INSERT ... SELECT` the
  per-team `SUM(service.points)` of passing checks into `service_points`, and the
  per-team solve values into `flag_points`. This is a one-time historical
  reconstruction; it is exact for services, and for flags uses the same
  round-window rule as the live path.
- `downgrade()` drops both tables (lossless — they are derived data;
  `score_adjustment` is the only non-derived data, so its downgrade must warn/refuse
  if rows exist, like `004`'s pattern).
- Verify against a scratch SQLite DB populated with real rounds, both upgrade and
  downgrade, before trusting.

---

## 9. Interaction with wave-1 branches

- Sits cleanly on top of `feature/wave-1-hardening`. The wave-1 indexes on
  `checks(round_id, result)` and `(service_id, round_id)` are exactly what the
  backfill query and the still-live SLA consecutive-failure scan need.
- **Supersedes `feature/weighted-scoring`.** Do not merge that branch. Re-apply its
  config surface (`weighted_scoring_enabled` + the three weights) on top of the
  current, anonymize-aware `scoreboard.py`, and wire the weights into the new
  `scores.py` read path. Its 256-line test file is a useful oracle for the weighting
  math.
- **Composes with `feature/bta-uptime-page`.** That page reads `AgentCheck` results;
  red scoring reads `Solve` rows. Independent, mergeable together. (Note: that branch
  has a latent `Check.round_id == last_round_number` bug — it compares a round *id*
  to a round *number*; flag it if that branch is revived.)

---

## 10. Phased implementation plan

Ordered by dependency. Each phase is independently testable and landable; later
phases assume earlier ones. Validate UI phases with Claude-in-Chrome against a live
`docker compose` stack.

1. **Schema + write path + backfill.** `round_score` table, `alembic 005` with
   backfill, engine round-close hook, `_cleanup_failed_round` + `admin_rollback`
   deletion. No read path changes yet — assert the materialized totals equal the
   live-computed totals (a golden test: for every team, `SUM(round_score)` ==
   `Team.current_score`). This phase is invisible to users and de-risks everything
   after it.
2. **Read-path cutover.** New `scoring_engine/scores.py`; redirect
   `Team.current_score`, `place`, `rank`, and the scoreboard/overview/stats APIs to
   read `round_score`. Delete the full-history scans. Perf test: scoreboard render at
   100 teams. Behaviour must be identical (same golden test, now through the new
   path).
3. **Weighted scoring re-applied** on the new read path (harvest
   `feature/weighted-scoring` config + tests).
4. **Red-team / flag scoring.** `flag_points` population at round close, flag value
   config, red-team leaderboard API + page, blue exposure display. Decide the
   subtract-from-blue question (§11) first.
5. **Manual score adjustments.** `score_adjustment` table, white-team API + audit
   UI, inclusion in totals, rollback interaction.
6. **Competition freeze.** `scoreboard_frozen_round` setting, read-path filter,
   white-team banner + control.

Phases 4–6 are independent of each other once 1–2 land, so they can fan out in
parallel the way wave 1 did.

## 11. Decisions — resolved 2026-07-24

1. **Flag scoring sign** (§6): **Add to red only.** A captured flag adds to the red
   team's total; the compromised blue team's score is untouched. `round_score`
   still stores each blue team's raw `flag_points`; the red total is
   `SUM(flag_points)` across all blue teams, read in phase 4. Blue's own total is
   service + inject + adjustments − SLA penalty, with no flag term.
2. **Flag point values**: **Flat per perm level.** Two config values,
   `flag_points_user` and `flag_points_root` (root worth more), mirroring the
   existing weight config surface. Per-flag YAML values can layer on later.
3. **Freeze basis**: **Wall-clock time.** Organizers announce "board freezes at
   3pm." A new `scoreboard_freeze_time` setting (nullable datetime). The read path
   resolves it to a boundary per component: `round_score` counts rounds whose
   `round.round_end <= freeze_time`, injects filter on `graded <= freeze_time`,
   adjustments on `created_at <= freeze_time`. See §7 revision below.
4. **Adjustments vs rollback**: keep manual adjustments across a rollback by default
   (audit trail), optional discard.
5. **Dynamic multiplier**: keep applying dynamic/weight policy retroactively
   (matches today's behaviour), reading raw points from `round_score`.

### §7 revision — freeze is time-based

Because freeze is wall-clock, the read filter keys on timestamps, not round number:

- `round_score`: join `rounds` and filter `round.round_end <= scoreboard_freeze_time`
  (a round counts once it *closed* before the freeze). `round_number` is still
  carried for dynamic-multiplier lookup; the freeze filter uses `round_end`.
- injects: `graded <= scoreboard_freeze_time`.
- adjustments: `created_at <= scoreboard_freeze_time`.

The engine keeps scoring past the freeze so the competition continues and can be
un-frozen; only the public read path applies the filter. White team sees live
numbers plus a "FROZEN at HH:MM" banner. `score_adjustment.effective_round` is
dropped in favour of `created_at` for the freeze comparison (simpler, one basis).

---

## 12. What this explicitly does not change

- SLA consecutive-failure detection still reads `checks` (needs per-service grain).
- Inject grading flow is untouched; only its inclusion in the total is centralized.
- The engine's round cadence, dispatch, and check execution are untouched.
- No new runtime dependencies; airgap story unchanged.
