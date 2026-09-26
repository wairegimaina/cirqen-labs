# Security — Credentials

## Where credentials live now

`config.py` and `sync/config.py` carry **no secrets** in `DEFAULT_CONFIG`
(checked by `core/tests/test_config_secrets.py`). The three HQ secrets —
`sync.auth_token`, `update.api_key`, `hq_db.password` — are resolved at runtime
from, in order:

1. **Environment variables**: `SYNC_AUTH_TOKEN`, `HQ_API_KEY`, `POSTGRES_HQ_PASSWORD`
   (used for that run, never written to disk by the config manager)
2. **`<data dir>/config.json`**: `~/.cirqen/data/config.json` on Linux, the
   running app's per-machine config, outside the repo
3. **`provisioning.json`**: read once when config.json lacks a secret, from
   `$CIRQEN_PROVISIONING_FILE`, the data directory, or next to the installed
   executable. Values are copied into config.json, after which the file can be
   deleted.

A missing secret is reported at startup (console banner + `cirqen.config` error
log) and by `validate_config()`.

### How installers get their secrets

`build.py` reads the secrets from the build machine's environment or its
git-ignored `.env`, **refuses to build** if any is missing or if DEBUG resolves
true, writes `provisioning.json` (git-ignored, mode 600) and copies it into
`dist/Cirqen/`. Treat a built installer as containing credentials.

`.env.example` documents every variable. To set up a dev machine:

```bash
cp .env.example .env      # then fill in real values
```

Helper scripts under `helper_scripts/` load credentials via `helper_scripts/_creds.py`
(env → config.json → .env).

## ⚠️ REQUIRED: rotate the exposed credentials

The following values were previously committed to this repository and therefore
remain readable in **git history**. Removing them from the current tree does NOT
make them safe — they must be rotated. Assume all of these are compromised:

| Credential | Where to rotate |
|---|---|
| HQ PostgreSQL password (`cirqen_hq_db1_user`) | Render → the Postgres instance → reset password |
| Old HQ PostgreSQL password (`cirqen_hq` / `dpg-d7rk…`) | Render (if instance still exists) → reset or delete |
| Local PostgreSQL password (`cirqen1` role) | `ALTER ROLE cirqen1 WITH PASSWORD '<new>';` on each client, then update `.env` / config.json |
| Sync auth token (`SYNC_AUTH_TOKEN`) | Regenerate on the HQ server; issue **per-client** tokens (see below) and redistribute |
| HQ update API key (`HQ_API_KEY`) | Render → HQ FastAPI service → Environment → regenerate |
| HQ Supabase database password (`postgres.nwlwaeeyduxroykrgksi`) | Supabase → Project settings → Database → reset password; then Render env `POSTGRES_HQ_PASSWORD` and every client's config.json |

After rotating, update `~/.cirqen/data/config.json` on each client (and `.env`
for dev) with the new values.

### Optional but recommended: purge history

The Supabase password was also committed (in `config.py`, `sync/config.py`,
`helper_scripts/patch_config.py`, and inside `hq.tar.xz` in the `hq_server`
repository). Purge both repositories. Never write the old values into this
document or any other tracked file.

Rotation is the real fix. If you also want the old values gone from history:

Write the old values, one per line as `<old value>==>REDACTED`, into a local
file **outside the repository** (never commit it), then:

```bash
# with git-filter-repo installed
git filter-repo --replace-text /path/outside/repo/old-secrets.txt
# then force-push and have every collaborator re-clone
```

## Per-client sync keys (enrollment)

Each client now authenticates with its own key, bound to its client_id on HQ
(hq_server `sync_api_keys` table, hashes only). Installers carry an
**enrollment code**, not a key:

1. On HQ (Render → Environment) set `HQ_ADMIN_TOKEN` (admin API only, never
   given to clients) and `HQ_ENROLLMENT_CODES` (one or more random codes,
   comma-separated).
2. Build installers with `SYNC_ENROLLMENT_CODE=<code>` in the build `.env`
   (and no `SYNC_AUTH_TOKEN`). The code lands in provisioning.json.
3. On first start the sync agent posts the code and its client_id to
   `/api/sync/enroll`, saves the returned key in config.json and clears the code.
   A client that already holds a key cannot be re-enrolled with a code.
4. Existing sites: issue a key per client with
   `POST /api/admin/generate_api_key {"device_id": <client_id>, "device_name": …}`
   (Bearer `HQ_ADMIN_TOKEN`), put it in that client's config.json as
   `sync.auth_token`.
5. Once every client has its own key, remove the shared master key from
   `API_KEYS_JSON` and rotate `SYNC_AUTH_TOKEN`. Rotate `HQ_ENROLLMENT_CODES`
   after each rollout.

## Moving HQ to a new host

Addresses take care of themselves: set `FLEET_SYNC_API_URL` on the update
server and every desktop follows within `FLEET_POLL_SECONDS` (see
`review/09_HQ_CONNECTION_CONFIG.pdf` §20). **Client keys do not.** They live in
HQ's `sync_api_keys` table, hashed and bound to a `client_id`, so a new host
with an empty table rejects every desktop with 401 and sync stops, even though
the address is correct.

So, in order:

1. Stand up the new host and **copy `sync_api_keys` to it** before announcing
   the move. This is the whole step — the stored values are hashes, and the
   tokens the desktops already hold keep working.
2. Set `FLEET_SYNC_API_URL` to the new host and keep the old one in
   `FLEET_SYNC_FALLBACKS` until the fleet has moved.
3. Watch `/api/updates/machines/` until desktops report in from the new host.
4. Remove the fallback and retire the old host.

If the table cannot be copied, each desktop must be re-issued a key
(`POST /api/admin/generate_api_key` with `"replace": true`) — 500 calls, so
copying the table is strongly preferred. A desktop whose key is rejected says
so explicitly in its log, naming the move if it recently followed one.

Lost or compromised client: `POST /api/admin/revoke_api_key {"device_id": …}`,
then re-issue with `generate_api_key` and `"replace": true`.
