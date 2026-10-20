# RepoPilot — Understand. Diagnose. Improve.

RepoPilot is a local-only Flask prototype for repository intelligence and
engineering risk analysis. It pairs an explainable Python analysis engine with
a polished dark developer dashboard: Risk Radar, dependency pulse, prioritized
hotspots, file evidence, source previews, and before/after comparison.

The built-in `repo-pilot-demo` makes the interface immediately explorable. No
API key, external database, frontend build tool, or external service is
required; snapshots are stored in a local SQLite file.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.lock
python app.py
```

The app is then available at <http://127.0.0.1:5000>. Open it in a browser to
use the server-rendered dashboard. The same routes return JSON when requested
with `Accept: application/json`, so the prototype remains easy to integrate:

| Endpoint | Purpose |
| --- | --- |
| `GET /` or `GET /dashboard` | Dashboard view model and summary hotspots |
| `GET /scan` | Repository scan form metadata |
| `POST /scan` | Scan `{ "repo_path": "..." }`; accepts local paths or GitHub HTTPS URLs |
| `GET /file/<path>` | File detail, source preview, and risk signals |
| `GET /compare?base=...&target=...` | Compare two analyzed files |
| `GET /api/report` | Full report JSON (`?include_content=true` adds source) |
| `GET /health` | Liveness check |

## Backend intelligence endpoints

The SQLite-backed snapshot store starts with the demo baseline/current reports
only when its database is empty, then adds a snapshot after each scan. Scans and
the current snapshot survive app restarts. These additive JSON endpoints work
with the current report:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/history` (also `/api/snapshots`, `/api/timeline`) | Snapshot history and timeline |
| `GET /api/snapshots/<id>` | Full report for one snapshot |
| `POST /api/snapshots/<id>/select` | Persist the active snapshot selection |
| `GET /api/repository/compare?before=...&after=...` | Repository-level before/after comparison |
| `GET /api/findings` | Structured findings with `new`, `open`, `improved`, `regressed`, or `resolved` lifecycle status |
| `GET /api/findings/compare` | Finding lifecycle changes between two snapshots |
| `GET /api/git/activity` | File-level Git churn, line additions/deletions, authors, and recency |
| `GET /api/git/compare?base=...&target=...` | Read-only commit/branch diff with statuses, numstat, and commit evidence |
| `GET /api/dependency-graph` | Resolved edges plus unresolved imports and circular dependency components |
| `GET /api/change-impact?file=...` | Change-impact preview for a selected file |
| `GET/POST /api/scoring/weights` | Read or configure normalized `complexity`, `change_frequency`, and `dependency_reach` weights; form and query values are accepted |
| `GET /api/report/markdown` | Download the current report as Markdown |
| `GET /api/refactor?file=...` | Deterministic, preview-only refactor simulation data |
| `GET /api/experiments` | Generated evidence for controlled experiments A-D |
| `GET /reports/experiments/experiments.json` | Download machine-readable experiment evidence |
| `GET /reports/experiments/experiments.md` | Download reviewable experiment evidence |

The same backend data is available through the non-`/api` aliases documented by
the route responses. Invalid files and invalid scoring weights return a JSON
error with HTTP 404 or 400 respectively.

Repository comparison and change-impact requests also accept local Git refs
without checking out either side. Use `base_commit`/`target_commit` or
`base_branch`/`target_branch` (the shorter `base_ref`/`target_ref` aliases are
also accepted). Git-backed responses include resolved commit evidence, branch
names, commit subjects, changed-file status, and per-file line additions and
deletions. Existing snapshot selectors (`before`/`after`, `base`/`target`)
remain supported.

## Product flow

1. Open **New scan** and enter a local repository path, a GitHub HTTPS URL, or choose the demo.
2. Start at **Overview** to see the transparent three-signal Risk Radar and
   the prioritized file table.
3. Select a hotspot to inspect the evidence behind its score, dependency reach,
   and source preview.
4. Use **Compare files** to put two files side by side and inspect risk, size,
   and dependency deltas.

The visual layer is built with Jinja templates, CSS, inline SVG, and small
vanilla JavaScript interactions. It includes responsive layouts, reduced-motion
support, mobile navigation, filtering, a local scan progress state, keyboard
shortcuts, command palette, copy-to-clipboard for source previews, restrained
dependency mapping, the Cozy Midnight visual system, and a refactor preview.

The browser prototype also includes `/history`, `/findings`, `/dependencies`,
`/impact`, `/compare/repository`, `/settings/scoring`, `/report`, `/refactor`,
and `/experiments` Evidence Center. The initial demo session includes a simulated
baseline snapshot so the before/after experience is immediately visible without
needing two real scans.

The Overview also includes a truthful Risk vs Coverage map when coverage data is
available. Files in the high-risk/low-coverage quadrant are prioritization
candidates; repositories without coverage reports show an explicit empty state
instead of fabricated values. File detail pages expose the same coverage source,
covered lines, missing lines, and percentage.
The initial demo session includes a simulated baseline snapshot so the
before/after experience is immediately visible without needing two real scans.

Form-encoded `repo_path` is also accepted by `POST /scan`. GitHub HTTPS sources
are validated, cloned with `--depth 1` into a local cache, and then scanned by
the same local analysis engine. Repeated scans reuse the cached checkout. Public
repositories work without credentials; private repositories use Git's existing
credential helper configuration, and RepoPilot never stores tokens. Invalid
remote sources return an actionable 400 error instead of silently switching to
demo mode. Unsupported or empty local paths retain the existing demo fallback.
Risk signals are explainable and combine complexity, recent change frequency,
and dependency reach (direct dependencies plus downstream dependents).

Remote clone safety defaults to a 30-second Git timeout and a 500 MB checkout
limit. Set `REPOPILOT_REMOTE_CACHE` to choose the local cache directory or
`REPOPILOT_REMOTE_MAX_MB` to change the size limit. Failed or oversized clones
are removed automatically.

## Coverage and dependency boundaries

When a scanned repository contains one of these local reports, RepoPilot attaches
per-file line coverage and emits a `low_coverage` signal below 80%:

- `coverage.xml` (Cobertura-style)
- `lcov.info`
- `.coverage` SQLite files from coverage.py, including modern `line_bits`
  bitset tables and legacy `line` tables. The bitsets are decoded with the
  Python standard library, so installing coverage.py is optional. Unsupported
  or unreadable database schemas stay unknown (no fabricated coverage values).

Static dependency analysis is conservative. It resolves known local relative
imports, lists unresolved imports separately, and reports circular dependency
components without inventing edges for third-party or runtime-only imports.

## Local persistence

By default the app creates `instance/repopilot.sqlite3`; the instance directory
and SQLite files are ignored by git. Set `REPOPILOT_DATABASE_PATH` (or pass
`DATABASE_PATH` to `create_app`) to use another database, including an isolated
temporary database in tests:

```powershell
$env:REPOPILOT_DATABASE_PATH = "C:\data\repopilot.sqlite3"
python app.py
```

## Verify

```powershell
make test
python -m compileall app app.py
```

## Command-line tooling

RepoPilot also provides a dependency-free CLI for local scans and CI quality
gates. It uses the same scanner and SQLite-backed `SnapshotStore` as the web
application. By default, each CLI invocation uses a temporary isolated
database, so it does not mutate the web application's instance database or its
`REPOPILOT_DATABASE_PATH` setting. Pass `--database PATH` explicitly (before
the subcommand) when you want snapshot history to persist between invocations:

```powershell
python repopilot_cli.py scan PATH
python repopilot_cli.py hotspots PATH
python repopilot_cli.py findings PATH
python repopilot_cli.py report PATH --format markdown
python repopilot_cli.py report PATH --format json
python repopilot_cli.py check PATH --max-risk 80 --max-new-findings 0

# Persistent CLI history, reused by later commands:
python repopilot_cli.py --database .local/repopilot-cli.sqlite3 scan PATH
python repopilot_cli.py --database .local/repopilot-cli.sqlite3 findings PATH
```

`scan`, `hotspots`, `findings`, and JSON reports write JSON to stdout. Markdown
reports write a readable report to stdout. The `check` command exits `0` when
both thresholds pass and `1` when either threshold is exceeded, making it
suitable for CI. Invalid command-line arguments or runtime errors exit `2`.
Use `demo` as `PATH` for the built-in deterministic example repository.

## Performance benchmark

Run the repeatable fixture benchmark with:

```powershell
python benchmarks/scan_benchmark.py
```

RepoPilot keeps a bounded in-process cache keyed by repository file metadata and
coverage-report metadata. The cache invalidates on normal source/report edits and
is limited to eight repository states so repeated scans stay fast without
unbounded memory growth.

## Controlled experiments

The deterministic fixture at `fixtures/experiment_repo` exercises hotspot
ranking, temporary refactor comparison, dependency boundaries, circular and
unresolved imports, low coverage, and ranking-weight sensitivity. Run all four
experiments with the exact command below:

```powershell
python experiments/run_experiments.py
python experiments/run_experiments.py --check  # exit 1 on invariant regressions
```

The runner never mutates the fixture. It writes machine-readable results to
`reports/experiments/experiments.json` and reviewable evidence to
`reports/experiments/experiments.md`.

## CI

GitHub Actions runs the locked dependency install, offline test suite, and the
CLI quality gate from `.github/workflows/ci.yml` on pushes and pull requests.

## Docker

```bash
docker build -t repopilot .
docker run --rm -p 5000:5000 repopilot
```
