# RepoPilot — Understand. Diagnose. Improve.

RepoPilot is a local-only Flask prototype for repository intelligence and
engineering risk analysis. It pairs an explainable Python analysis engine with
a polished dark developer dashboard: Risk Radar, dependency pulse, prioritized
hotspots, file evidence, source previews, and before/after comparison.

The built-in `repo-pilot-demo` makes the interface immediately explorable. No
API key, database, frontend build tool, or external service is required.

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
| `POST /scan` | Scan `{ "repo_path": "..." }`; omit it for demo mode |
| `GET /file/<path>` | File detail, source preview, and risk signals |
| `GET /compare?base=...&target=...` | Compare two analyzed files |
| `GET /api/report` | Full report JSON (`?include_content=true` adds source) |
| `GET /health` | Liveness check |

## Backend intelligence endpoints

The process-local snapshot store starts with the demo report and adds a snapshot
after each scan. These additive JSON endpoints work with the current report:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/history` (also `/api/snapshots`, `/api/timeline`) | Snapshot history and timeline |
| `GET /api/snapshots/<id>` | Full report for one snapshot |
| `GET /api/repository/compare?before=...&after=...` | Repository-level before/after comparison |
| `GET /api/findings` | Deterministic findings derived from signal evidence; filter with `severity` or `file` |
| `GET /api/change-impact?file=...` | Change-impact preview for a selected file |
| `GET /api/dependency-graph` | Dependency graph nodes and edges |
| `GET/POST /api/scoring/weights` | Read or configure normalized `complexity`, `change_frequency`, and `dependency_reach` weights; form and query values are accepted |
| `GET /api/report/markdown` | Download the current report as Markdown |
| `GET /api/refactor?file=...` | Deterministic, preview-only refactor simulation data |

The same backend data is available through the non-`/api` aliases documented by
the route responses. Invalid files and invalid scoring weights return a JSON
error with HTTP 404 or 400 respectively.

## Product flow

1. Open **New scan** and enter a local repository path, or choose the demo.
2. Start at **Overview** to see the transparent three-signal Risk Radar and
   the prioritized file table.
3. Select a hotspot to inspect the evidence behind its score, dependency reach,
   and source preview.
4. Use **Compare files** to put two files side by side and inspect risk, size,
   and dependency deltas.

The visual layer is built with Jinja templates, CSS, inline SVG, and small
vanilla JavaScript interactions. It includes responsive layouts, reduced-motion
support, mobile navigation, filtering, a local scan progress state, keyboard
shortcuts, and copy-to-clipboard for source previews.

Form-encoded `repo_path` is also accepted by `POST /scan`. Unsupported or
empty paths safely fall back to the demo repository. Risk signals are
explainable and combine complexity, recent change frequency, and dependency
reach (direct dependencies plus downstream dependents).

## Verify

```powershell
make test
python -m compileall app app.py
```

## Docker

```bash
docker build -t repopilot .
docker run --rm -p 5000:5000 repopilot
```
