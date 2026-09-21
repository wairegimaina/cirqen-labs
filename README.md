# Equiper (Cirqen Labs)

Equiper is a desktop CMMS for hospital biomedical engineering departments:
equipment inventory, job cards with technician and in-charge signatures,
preventive maintenance (PPM) scheduling, calibration sessions and ISO/IEC
17025 certificates, parts and tools, and reports.

Each site runs a self-contained desktop build: a PySide6 shell around a
Django app, an embedded PostgreSQL (the local database) and Redis, and a sync
agent that exchanges changes with the HQ server (separate `hq_server` repo)
so every site works offline and converges when connected.

| Piece | Where |
|---|---|
| Django project and settings | `Equiper/` (settings read `config.py` via `sync/config.py`) |
| Apps | `Inventory`, `jobcard`, `ppms`, `CalSoft` (calibration), `calSchedules`, `parts_tools`, `reporthub`, `machineReports`, `users`, `workshop`, `updates`, `audit_log`, `core` |
| Sync agent | `sync/` — see `sync/ARCHITECTURE.md` |
| Desktop shell and runtime | `main.py`, `bulider_tools/` |
| Build | `build.py` |
| Security notes, credential handling | `SECURITY.md` |
| Field runbook | `docs/RUNBOOK.md` |

## Configuration

Settings come from `config.json` in the data directory
(`~/.cirqen/data/` on Linux, `%LOCALAPPDATA%\Cirqen\data\` on Windows,
or `$CIRQEN_DATA_DIR`), created on first run. Environment variables override
it; a git-ignored `.env` does the same for development (`cp .env.example .env`).
Secrets are never defaults in code — see `SECURITY.md` for how they are
supplied and how installers are provisioned.

Useful switches:

| Variable | Effect |
|---|---|
| `DJANGO_DEBUG=1` | Debug mode (packaged builds ignore `app.debug` in config.json) |
| `CIRQEN_HTTPS=1` | Secure cookies, HTTPS redirect and HSTS when served over TLS |
| `SENTRY_DSN` | Send errors from Django and the sync agent to Sentry |
| `CIRQEN_QUERY_BUDGET` | Log requests that run more queries than this (default 100 in debug) |

## Where HQ is hosted

The HQ addresses and the HQ database host are written in **one place**:
`HQ_ENDPOINT_DEFAULTS` at the top of `config.py`. Nothing else in the code names
a host, and `core/tests/test_hq_endpoints.py` fails if one appears elsewhere.
Sync and updates are two different services, so they are two settings.

| Setting | Environment variable | Meaning |
|---|---|---|
| `sync.api_url` | `SYNC_API_URL` | Sync and certificate API (ends in `/api/sync`) |
| `update.server_url` | `HQ_SERVER_URL` | Update server (a bare address, no `/api/...`) |
| `hq_db.host` `.port` `.database` `.user` `.sslmode` | `POSTGRES_HQ_HOST` `_PORT` `_DB` `_USER`, `POSTGRES_SSLMODE` | HQ database |

Each is resolved in this order: environment variable, then `config.json`, then
`provisioning.json`, then the default. This works in the packaged app as well as
in development. A shipped default is **not** written to `config.json`, so:

- **Move every machine to a new HQ:** change `HQ_ENDPOINT_DEFAULTS` and ship a
  build. Machines follow on their next start. A `config.json` written by an
  older build is migrated once (a copy is kept as `config.json.pre-endpoints`).
- **Point one machine, or a group, elsewhere:** set the environment variable, or
  put the address in that machine's `provisioning.json`, or edit `config.json`.
  A value set on purpose stays until it is removed.
- **See what a machine is using and why:** `python -c "from config import
  resolve_endpoints as r; print(r('<data dir>'))"` prints each value and the
  layer it came from (`env`, `config.json`, `provisioning`, `default`).

`validate_config()` rejects a bad address by name (plain `http` outside
localhost, a sync address without `/api/sync`, an update address with an API
path, a database host with a scheme).

## Development

```bash
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # fill in local values
python manage.py migrate
python manage.py runserver        # or: python main.py for the desktop shell
```

## Tests

```bash
# Django apps — in-memory SQLite, no Redis or HQ needed
python manage.py test --settings=Equiper.test_settings

# Sync engine and update/backup snapshots — needs PostgreSQL binaries
# (runtime/postgresql, $CIRQEN_PG_BIN, or a system install)
python -m pytest

# Lint baseline
ruff check .
```

Worth knowing about when a test fails:

- `core/tests/test_route_smoke.py` requests every route as each role and fails
  on any 500, on anonymous access to a private page, and on URL names or
  templates that do not exist.
- `core/tests/test_cross_workshop_access.py` points one workshop's users at
  another workshop's records by ID. Record scoping lives in `core/scoping.py`.
- `core/tests/test_query_budget.py` fails when a page's query count grows with
  the amount of data (an N+1 query).
- `jobcard/tests.py`, `CalSoft/test_calibration_flow.py`,
  `ppms/test_ppm_flow.py`, `updates/test_updater_flow.py` cover the business
  flows end to end.

CI (`.github/workflows/ci.yml`) runs lint, the Django tests and the pytest
suites against PostgreSQL on every push.

## Releases

1. Bump `version.txt` and `APP_VERSION` in `Equiper/settings.py`.
2. On the build machine, make sure the secrets / enrollment code are in the
   environment or `.env` (the build refuses to run without them, or with
   DEBUG on).
3. `python build.py` — produces `dist/Cirqen/` and an archive, including the
   git-ignored `provisioning.json`. Treat the archive as containing credentials.
4. For in-app updates, publish the package through HQ (`hq_server/build_package.py`);
   clients check, snapshot their database, apply, health-check and roll back
   automatically on failure.
