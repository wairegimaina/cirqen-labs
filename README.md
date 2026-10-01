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
| Hospital server install, operations | `docs/SERVER_MODE.md`, `docs/OPERATIONS.md` |
| User manuals and FAQ | `docs/manuals/` |
| ISO/IEC 17025 validation pack | `docs/validation/ISO17025_VALIDATION.md` |
| Data protection, licences, EULA and service terms (drafts) | `docs/legal/` |
| Support, releases, changelog, pilot | `SUPPORT.md`, `RELEASES.md`, `CHANGELOG.md`, `PILOT_PLAN.md` |

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

The HQ addresses are written in **one place**:
`HQ_ENDPOINT_DEFAULTS` at the top of `config.py`. Nothing else in the code names
a host, and `core/tests/test_hq_endpoints.py` fails if one appears elsewhere.
Sync and updates are two different services, so they are two settings.

| Setting | Environment variable | Meaning |
|---|---|---|
| `sync.api_url` | `SYNC_API_URL` | Sync and certificate API (ends in `/api/sync`) |
| `update.server_url` | `HQ_SERVER_URL` | Update server (a bare address, no `/api/...`) |

Clients never connect to the HQ database: the app and the sync agent reach HQ
through these two services only, so no install or `config.json` holds an HQ
database password. A `config.json` from an older build has its `hq_db` section
removed on the next start.

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
2. On the build machine, make sure the update key and local database password
   are in the environment or `.env` (the build refuses to run without them, or
   with DEBUG on). No sync key: each PC enrolls and gets its own.
3. `python build.py` — produces `dist/Cirqen/` and a neutral archive
   (`Cirqen_linux_v<version>.tar.gz`), the same for every hospital, with no
   installer file inside.
   For a hospital: admin panel → its page → Installers → download its
   `provisioning.json`, then
   `python build.py --package-only --provisioning provisioning-ch0002.json`.
   That gives `Cirqen_linux_v<version>_CH0002.tar.gz` with the file inside.
   It holds an enrollment token: hand it over privately. Files with a shared
   `sync.auth_token` are refused.
4. Push to `main`. Control builds the code update package
   (`hq_server/build_package.py`); PCs check, snapshot their database, apply,
   health-check and roll back automatically on failure. Cirqen's own code is
   kept as loose `.py` files in the build, so these updates change the code.
5. **When the runtime changes** (`runtime.json`: Python, PostgreSQL, Redis;
   `requirements.txt`; `main.py`), a code update can't carry it. Run
   `python build.py --publish` after bumping the version: it builds the full
   app, stamps its runtime id and uploads it to the GitHub release
   `desktop-v<version>`. PCs on the older runtime get the full app from
   Control instead of the code package: they download and check it, unpack
   it next to the app, and on Restart swap folders; if the new app doesn't
   start within 5 minutes the old one comes back. Until it is published,
   such PCs are told and keep their version. Needs `gh` (logged in) here and
   `GITHUB_TOKEN` on Control with read access to this repository. Linux PCs
   swap by themselves; on other systems install the full app by hand.

The full release checklist (staged rollout, validation, licences) is in
`RELEASES.md`.
