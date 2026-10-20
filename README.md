# RepoPilot

> **Understand. Diagnose. Improve.**

RepoPilot is a local-first repository intelligence tool. It scans a codebase,
explains the signals behind engineering risk, maps local dependencies, and
helps compare changes over time without uploading source code to RepoPilot.

The project combines a dependency-light Flask application, a deterministic
Python scanner, a server-rendered dashboard, and a local SQLite snapshot store.
It is designed to run from a clean checkout with no frontend build step and no
external service dependency.

## Live demo

Try the hosted demo:

**[Open RepoPilot on Render](https://repopilot-9sn8.onrender.com)**

The live deployment currently starts with the built-in demo repository so the
dashboard can be explored immediately. Use **New scan** to analyze a public
GitHub repository.

## Highlights

- **Local repository scans** from an absolute folder path.
- **Public and private GitHub HTTPS scans** using the machine's existing Git
  credential configuration.
- **Explainable risk signals** for complexity, recent change activity, and
  dependency reach.
- **Dependency map** with resolved edges, circular components, and unresolved
  imports kept separate from the graph.
- **Coverage-aware prioritization** from Cobertura XML, LCOV, and coverage.py
  SQLite databases.
- **Snapshot history** stored locally in SQLite.
- **File evidence** with source previews, Git activity, and structural warnings.
- **File and repository comparisons** using snapshots, branches, or commits.
- **Preview-only refactor analysis** and deterministic controlled experiments.
- **CLI quality gates** for local development and CI.
- **Docker support** with Git installed for remote repository cloning.

## Screens and workflow

1. Open **New scan**.
2. Enter a local folder, a GitHub HTTPS URL, or choose the built-in demo.
3. Review the **Overview** risk radar and prioritized hotspots.
4. Open a file to inspect its score, evidence, dependencies, coverage, and
   source preview.
5. Use **Dependency map**, **Impact preview**, **Compare files**, or
   **Scan history** to explore the result.

The **Evidence Center** contains stable, controlled experiment output. It is
intentionally based on the checked-in fixture and does not change when a
different repository is scanned. The live repository graph is shown under
**Dependency map**.

## Requirements

- Python `3.12.8` (pinned in [`.python-version`](.python-version))
- Git on `PATH` for GitHub URL scans
- Windows, macOS, or Linux

The application dependencies are pinned in
[`requirements.lock`](requirements.lock). No Node.js installation is required.

## Quick start

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --requirement requirements.lock
python app.py
```

### macOS or Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements.lock
python app.py
```

Open <http://127.0.0.1:5000> after the server starts.

The development entrypoint enables Flask debug mode. For a production
deployment, run behind a production WSGI server and configure the deployment's
process manager separately.

## Render deployment

The project is also deployed as a Docker web service on Render:

```text
https://repopilot-9sn8.onrender.com
```

The hosted demo is intended for product exploration and public repository
scans. The free Render deployment uses temporary storage, so SQLite scan history
and the remote Git clone cache should be treated as disposable. A paid Render
service with a persistent disk is recommended when scan history and cached
checkouts must survive restarts.

If the live demo has been inactive, the first request may take longer while the
service wakes up. Refresh the page after it responds if the initial request
appears delayed.

## Scan a repository

### From the web interface

Open **New scan** and enter one of these values:

```text
C:\projects\my-repository
/Users/you/code/project
/home/you/project
https://github.com/owner/repository
https://github.com/owner/repository.git
```

Local scans are analyzed in place. GitHub HTTPS sources are shallow-cloned into
the local cache first, then analyzed by the same scanner. The source checkout
and scan remain local.

### From the API

```bash
curl -X POST http://127.0.0.1:5000/scan \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"repo_path":"https://github.com/psf/requests.git"}'
```

Form-encoded `repo_path` is also accepted. A successful response contains the
report plus repository metadata such as `source_type`, `source_url`, and the
local checkout `path`.

### Authentication and privacy

Public repositories need no additional credentials. For private repositories,
RepoPilot delegates authentication to Git, including configured credential
helpers and existing machine authentication. Do not put access tokens in the
repository path, logs, database, reports, or environment files.

RepoPilot supports HTTPS Git repository URLs. SSH clone URLs are not accepted
by the web scan input at this time.

## Remote clone safety

Remote scans use conservative defaults:

- `git clone --depth 1 --no-tags`
- 30-second clone timeout
- 500 MB checkout size limit
- Failed and oversized checkouts are deleted
- Successful checkouts are reused by normalized URL
- GitHub is allowed by default; additional HTTPS Git hosts require an explicit
  allowlist

Configure these values before starting the app:

| Variable | Default | Purpose |
| --- | --- | --- |
| `REPOPILOT_REMOTE_CACHE` | system temp directory | Remote checkout cache location |
| `REPOPILOT_REMOTE_TIMEOUT` | `30` | Clone timeout in seconds |
| `REPOPILOT_REMOTE_MAX_MB` | `500` | Maximum checkout size |
| `REPOPILOT_ALLOWED_GIT_HOSTS` | `github.com,www.github.com` | Comma-separated HTTPS Git hosts |

Example:

```bash
export REPOPILOT_REMOTE_CACHE="$HOME/.cache/repopilot/git"
export REPOPILOT_REMOTE_TIMEOUT=60
export REPOPILOT_REMOTE_MAX_MB=1000
export REPOPILOT_ALLOWED_GIT_HOSTS="github.com,git.example.com"
python app.py
```

## What the scanner analyzes

RepoPilot currently considers these file types:

```text
.py .js .jsx .ts .tsx .java .go .rb .rs .md
```

The scanner ignores common generated or dependency directories such as
`.git`, virtual environments, `node_modules`, bytecode caches, and test caches.
It analyzes at most 250 supported files per scan to keep the dashboard
responsive.

### Risk signals

Each analyzed file receives explainable signals for:

- **Complexity** — file size, functions, and branch points.
- **Change frequency** — recent Git activity, churn, and contributors when
  history is available.
- **Dependency reach** — direct local imports and downstream dependents.
- **Low coverage** — emitted when detected line coverage is below 80%.

The final risk score is a weighted prioritization aid, not a claim that a file
contains a defect.

### Dependency boundaries

Dependency analysis is deliberately conservative. Local imports are resolved
when they match files in the scan. External, ambiguous, or unresolved imports
are reported separately rather than turned into invented graph edges. This is
why a repository can show many nodes but zero resolved connections.

### Coverage reports

If present at the repository root, RepoPilot can read:

- `coverage.xml` in Cobertura format
- `lcov.info`
- `.coverage` SQLite databases from coverage.py, including modern `line_bits`
  and legacy `line` tables

Unknown or unreadable coverage stays unknown; the scanner does not fabricate
coverage values.

## Persistence

The default database is:

```text
instance/repopilot.sqlite3
```

The `instance` directory and SQLite files are ignored by Git. Override the
database location with `REPOPILOT_DATABASE_PATH`:

```powershell
$env:REPOPILOT_DATABASE_PATH = "C:\data\repopilot.sqlite3"
python app.py
```

Scans create snapshots that survive application restarts. The built-in demo
seeds initial snapshots only when the database is empty.

## Web routes and API

All browser routes return HTML by default. The same view-model routes return
JSON when requested with `Accept: application/json`; routes beginning with
`/api/` always return JSON.

### Primary routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/` or `/dashboard` | Overview dashboard |
| `GET` | `/scan` | Scan form metadata |
| `POST` | `/scan` | Scan a local path or GitHub HTTPS URL |
| `GET` | `/file/<path>` | File evidence and source preview |
| `GET` | `/compare` | Compare two analyzed files |
| `GET` | `/history` | Snapshot timeline |
| `GET` | `/findings` | Finding lifecycle view |
| `GET` | `/dependencies` | Current repository dependency map |
| `GET` | `/impact` | Change-impact preview |
| `GET` | `/compare/repository` | Repository snapshot/Git comparison |
| `GET` | `/settings/scoring` | Configure scoring weights |
| `GET` | `/report` | Render a Markdown-style report view |
| `GET` | `/refactor` | Preview-only refactor simulation |
| `GET` | `/experiments` | Controlled experiment evidence |

### JSON endpoints

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness response |
| `GET` | `/api/report` | Current report; use `include_content=true` for source |
| `GET` | `/api/history` | Snapshot history and timeline |
| `GET` | `/api/snapshots/<id>` | Full report for one snapshot |
| `POST` | `/api/snapshots/<id>/select` | Select the active snapshot |
| `GET` | `/api/findings` | Structured findings and lifecycle status |
| `GET` | `/api/findings/compare` | Finding changes between snapshots |
| `GET` | `/api/dependency-graph` | Resolved edges, unresolved imports, and cycles |
| `GET` | `/api/change-impact?file=...` | Impact preview for a file |
| `GET` | `/api/git/activity` | Git churn and author evidence |
| `GET` | `/api/git/compare?base=...&target=...` | Read-only Git diff evidence |
| `GET/POST` | `/api/scoring/weights` | Read or update normalized weights |
| `GET` | `/api/report/markdown` | Download a Markdown report |
| `GET` | `/api/refactor?file=...` | Deterministic refactor preview data |
| `GET` | `/api/experiments` | Controlled experiment results |

Git comparison accepts `base_commit`/`target_commit`,
`base_branch`/`target_branch`, or the shorter `base_ref`/`target_ref` names.
Snapshot comparison remains available through `before`/`after` and the saved
snapshot selectors.

## CLI

`repopilot_cli.py` uses the same scanner and snapshot model without requiring
Flask. Each command uses an isolated temporary database unless `--database` is
provided.

```bash
python repopilot_cli.py scan PATH
python repopilot_cli.py hotspots PATH
python repopilot_cli.py findings PATH
python repopilot_cli.py report PATH --format markdown
python repopilot_cli.py report PATH --format json
python repopilot_cli.py check PATH --max-risk 80 --max-new-findings 0
```

Use the built-in deterministic example:

```bash
python repopilot_cli.py scan demo
```

Persist CLI history explicitly when needed:

```bash
python repopilot_cli.py --database .local/repopilot-cli.sqlite3 scan PATH
python repopilot_cli.py --database .local/repopilot-cli.sqlite3 findings PATH
```

The `check` command exits with `0` when thresholds pass, `1` when a quality
threshold fails, and `2` for invalid arguments or runtime errors.

## Testing and verification

The project keeps its test command offline and dependency-light:

```bash
make test
```

On Windows without `make`, run the equivalent command:

```powershell
python -m unittest discover -s tests -v
```

Additional checks:

```bash
python -m compileall app app.py
python experiments/run_experiments.py --check
python benchmarks/scan_benchmark.py
```

The test suite covers the scanner, persistence, API routes, CLI, Git metrics,
coverage parsing, dependency graphs, comparisons, experiments, and repository
source handling.

## Controlled experiments

The checked-in fixture at `fixtures/experiment_repo` validates four repeatable
behaviors:

1. Hotspot ranking precision
2. Before/after refactor comparison
3. Dependency boundaries, cycles, and unresolved imports
4. Ranking sensitivity to scoring weights

Run the suite with:

```bash
python experiments/run_experiments.py
python experiments/run_experiments.py --check
```

Results are written to:

```text
reports/experiments/experiments.json
reports/experiments/experiments.md
```

The experiment runner uses temporary copies and does not mutate the fixture.

## Docker

The Docker image includes Git so remote repository scans work inside the
container:

```bash
docker build -t repopilot .
docker run --rm -p 5000:5000 repopilot
```

Open <http://127.0.0.1:5000> in your browser.

For a persistent database or remote clone cache, mount local directories and set
the corresponding environment variables. Do not bake credentials into the
image.

## Reproducible development

- Python is pinned in `.python-version`.
- Application dependencies are pinned in `requirements.lock`.
- The Dockerfile installs the system Git dependency and locked Python packages.
- `make test` is the single offline test command used by CI.
- No global package installation is required.
- Runtime databases, caches, and generated local state are excluded from Git.

## CI

GitHub Actions runs on pushes and pull requests. The workflow:

1. Checks out the repository.
2. Uses the pinned Python version.
3. Installs `requirements.lock`.
4. Runs the offline test suite.
5. Runs the demo CLI quality gate.
6. Runs the controlled experiment invariants.

See [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Project structure

```text
app.py                 Flask development entrypoint
app/                   Application package, scanner, routes, templates, assets
tests/                 Offline unit and API tests
fixtures/              Deterministic analysis fixture
experiments/            Controlled experiment runner
benchmarks/             Repeatable scanner benchmark
reports/experiments/    Generated experiment evidence
Dockerfile              Reproducible container build
Makefile                Test and run commands
repopilot_cli.py        Dependency-free CLI wrapper
```

## Limitations

- The browser scan input currently accepts local folders and HTTPS Git URLs;
  SSH clone URL input is not supported.
- Dependency analysis is conservative and may classify valid alias-based or
  generated imports as unresolved.
- Only the first 250 supported files are analyzed in one scan.
- Remote scans require network access and Git for the initial clone. Cached
  clones can be scanned without recloning.
- The free Render demo may sleep after inactivity, so its first request can be
  slower while the service wakes up.
- The free Render deployment has ephemeral storage; scan history and remote
  clone caches may be lost after a restart or redeploy.
- Hosted scans clone repository source onto the hosting service. Run RepoPilot
  locally when source code must remain on your own machine.
- The development server is not intended to be exposed directly to the public
  internet.

## License

No license file is currently included in this repository. Add a license before
distributing RepoPilot or accepting external contributions under a specific
license.
