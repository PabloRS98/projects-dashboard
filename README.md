# Projects Dashboard

[![CI](https://github.com/PabloRS98/projects-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/PabloRS98/projects-dashboard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A self-hosted personal dashboard to keep track of all your repositories in one
place. It populates itself automatically: it scans a local folder for git
repositories, infers each remote from `origin`, and pulls both local state
(branch, latest commit, uncommitted changes, TODOs) and remote metadata
(stars, issues, PRs, CI status, description, homepage) from GitHub, GitLab, or
Bitbucket. You can also attach notes, tasks, and Telegram alerts.

Single-user and built to run on your own machine or local network.

## Getting Started

### With Docker (recommended)

```bash
cp .env.example .env       # adjust as needed
mkdir -p data
LOCAL_REPOS_HOST_PATH=~/repos docker compose up -d --build
```

Available at <http://localhost:8003> (change `HOST_PORT` in `.env` to use another port).

`docker-compose.yml` mounts two volumes: `./data` for the SQLite database and
backups, and your repositories directory at `/repos` **read-only** — the app
never writes to your repositories.

### Locally without Docker

Requires Python 3.12+ and `git` on your `PATH`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Adjust DB_PATH and LOCAL_REPOS_BASE_PATH: defaults (/data, /repos) are container paths.
uvicorn app.main:app --reload
```

## How Auto-Discovery Works

No manual onboarding needed. On startup and every `DISCOVERY_MINUTES`, the app:

1. **Scans git repositories** under `LOCAL_REPOS_BASE_PATH`, up to
   `PROJECTS_SCAN_DEPTH` levels deep.
2. **Infers the remote** by reading `git remote get-url origin`, extracting the
   forge provider and `owner/repo` so newly discovered repos immediately fetch
   stars, PRs, and CI status.
3. **Imports GitHub repositories** from your account that aren't cloned locally
   yet (requires `GITHUB_TOKEN`; disable with `AUTO_IMPORT_GITHUB=false`).
4. **Flags missing local paths** instead of silently keeping stale data.

Manual edits always take precedence: if you override a project's `owner/repo`,
description, or homepage, auto-discovery will not overwrite them.

> If your repositories are nested inside subfolders (`workspace/group/my-app`),
> `PROJECTS_SCAN_DEPTH=1` will not reach them — raise it to `2` or `3`.

## Configuration

Everything is configured via environment variables; see `.env.example` for the
full list and defaults. Key settings:

| Variable | Default | Purpose |
|---|---|---|
| `LOCAL_REPOS_BASE_PATH` | `/repos` | Root folder scanned for git repositories |
| `PROJECTS_SCAN_DEPTH` | `3` | Directory depth for auto-discovery |
| `DB_PATH` | `/data/projects.db` | SQLite database file |
| `HOST_PORT` | `8003` | Port exposed by docker-compose |
| `ENABLE_AUTH` | `false` | Enables HTTP Basic authentication |
| `GITHUB_TOKEN` | empty | Increases API rate limits, unlocks private repos and GitHub auto-import |
| `AUTO_IMPORT_GITHUB` | `true` | Automatically imports repositories from your GitHub account |
| `DISCOVERY_MINUTES` | `360` | How often to scan for new projects |
| `LOCAL_SYNC_MINUTES` | `15` | How often to refresh local git status |
| `REMOTE_SYNC_MINUTES` | `60` | How often to query remote forge APIs |
| `STALE_PROJECT_DAYS` | `30` | Days without commits before flagging a project as stale |
| `STALE_PR_DAYS` | `7` | Days an open PR stays untouched before flagging it as stale |
| `TELEGRAM_BOT_TOKEN` | empty | Enables Telegram alerts (paired with `TELEGRAM_CHAT_ID`) |

## Views

- **`/`** — Main dashboard with incremental search, custom sorting, composable
  filters, and two layouts (cards or dense table). Top KPIs link directly to
  filtered views.
- **`/proyecto/{id}`** — Project detail view: collapsible README, recent commits,
  TODOs with `file:line`, weekly activity, notes, and tasks.
- **`/tv`** — Kiosk/TV mode: read-only summary highlighting items that need
  attention, auto-refreshing every minute.
- **`/estado`** — Dashboard health page: last run time and status of every
  background job, remaining GitHub API quota, Telegram status, and active config.

## Error Diagnostics

Remote API failures are mapped to actionable root causes: expired token (401),
missing repository permissions (403), rate limit exhausted with reset timestamp,
missing/private repository (404), upstream forge outage (5xx), timeouts, and
network errors.

## Security

This is a single-user app: anyone who can reach it can view and edit everything.
Before exposing it beyond `localhost`:

- **Set `ENABLE_AUTH=true` and choose a strong password.** When auth is enabled,
  the app refuses to start if `AUTH_PASSWORD` is left as `changeme` or is shorter
  than 8 characters.
- **Put it behind TLS.** HTTP Basic sends credentials in plaintext on every
  request — always terminate HTTPS via a reverse proxy or VPN.
- `docker-compose.yml` binds to `127.0.0.1` by default. If you change it to
  `0.0.0.0`, make sure auth and TLS are in place first.

Built-in protections: origin validation against CSRF on every state-changing
request, HTTP security headers (CSP, `nosniff`, `Referrer-Policy`), internal-only
redirect validation, allowlist-based README sanitization (`nh3`), and URL scheme
validation for user-supplied links.

If you access the app behind a reverse proxy under a different hostname, add it
to `TRUSTED_ORIGINS` (comma-separated) so CSRF origin checks allow form
submissions.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

The scanner and discovery tests create real git repositories in temporary
directories, so `git` must have `user.name` and `user.email` configured.

## Architecture

```text
app/
  main.py          entrypoint, middleware and scheduler startup
  config.py        environment configuration (pydantic-settings)
  database.py      SQLite engine and lightweight column migrations
  models.py        Project, TaskItem and ProjectSnapshot
  security.py      CSRF, safe redirects and URL validation
  auth.py          optional HTTP Basic auth
  routers/         HTTP views
  services/
    local_scanner.py  local git: status, commits, TODOs, remote, activity
    discovery.py      auto-discovery (disk + GitHub account)
    sync.py           local and remote sync orchestration
    forge_errors.py   HTTP failure translation into actionable diagnostics
    history.py        daily snapshots and chart series
    scheduler.py      background jobs and last-run tracking
    alerts.py         deduplicated Telegram notifications
  templates/       Jinja2 + HTMX
```

Sync runs in three decoupled cycles: disk discovery runs infrequently, local git
scanning is cheap and runs often, and remote API sync is spaced out to respect
rate limits (running with bounded concurrency and stopping early if quota runs
out). Each cycle tracks its own error state independently so failures don't mask
each other.
