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

`bulid_V1.py` reads the secrets from the build machine's environment or its
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

The Supabase password was also committed (in `config.py`, `sync/config.py` and
`helper_scripts/patch_config.py`) and is not listed below: add it to your local
replacement file rather than writing it into this document again.

Rotation is the real fix. If you also want the old values gone from history:

```bash
# with git-filter-repo installed
git filter-repo --replace-text <(cat <<'EOF'
cTAU3kJL3NNlUYA9rR07kh87FKHA6c24==>REDACTED
Btwelvetech@2024==>REDACTED
58f8605e1966ce148990c477dcb99d02==>REDACTED
G6PScpbnjBWe4PMhi9c_31FzFzzxnHkyfnyzqsdE-JgIYwe4WBRBkBgLyuje43F5==>REDACTED
RyJEzkPmYWrdC2472TzWnFUMaOIueaye==>REDACTED
EOF
)
# then force-push and have every collaborator re-clone
```

## Next hardening step (Phase 1, item 2)

Replace the single shared `SYNC_AUTH_TOKEN` with **one token per client/workshop**,
validated and tenant-scoped on the HQ server, so a leaked token can only affect
one site rather than the whole fleet.
