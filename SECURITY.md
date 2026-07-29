# Security — Credentials

## Where credentials live now

Secrets are **no longer hardcoded in tracked source**. They are resolved at
runtime in this order:

1. **Environment variables** (`POSTGRES_*`, `SYNC_AUTH_TOKEN`, `HQ_API_KEY`, …)
2. **`~/.cirqen/data/config.json`** — the running app's config (per machine, outside the repo)
3. **`.env`** at the repo root — git-ignored; loaded by `config.py` / `sync/config.py`
   and the helper scripts via `python-dotenv`

`.env.example` documents every required variable. To set up a dev machine:

```bash
cp .env.example .env      # then fill in real values
```

Helper scripts under `helper_scripts/` load credentials via `helper_scripts/_creds.py`
(env → config.json → .env). They contain **no secrets**.

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

After rotating, update `~/.cirqen/data/config.json` on each client (and `.env`
for dev) with the new values.

### Optional but recommended: purge history

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
