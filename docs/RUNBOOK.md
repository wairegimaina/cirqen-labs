# Field runbook

What to do when a site reports a problem. Paths below use the Linux data
directory `~/.cirqen/data/`; on Windows it is `%LOCALAPPDATA%\Cirqen\data\`.

Logs: `~/.cirqen/data/logs/` — `errors.log` (start here), `django.log`,
`sync.log`, `sync_agent.log`, `auth.log`, `redis.log`. If `SENTRY_DSN` is set, errors are
also in Sentry, tagged with the release (`equiper@<version>`).

---

## 1. Redis is down

**Symptoms:** the app works but is slower; `errors.log` has
`Cache 'default' (...) is unavailable; running without it.`

**Why it keeps working:** both Redis caches fail soft (0.3 s connect timeout,
errors ignored). Sessions fall back to the database, dashboard aggregates are
computed on every request, and login throttling uses a file cache.

**Fix:**
1. Restart the app; the desktop runtime restarts the bundled `redis-server`
   (port 7788, or the next free port).
2. If it keeps dying, read `logs/redis.log` and check free disk space and
   memory on the machine.
3. Nothing needs recovering afterwards: cached values are rebuilt on demand.

---

## 2. HQ is unreachable

**Symptoms:** sync status shows offline; calibration approvals say
"Approved offline. Certificate will be generated when connection is restored";
HQ's watchdog may send a *stale client* alert.

**What happens meanwhile:** local work continues. Changes queue in the local
outbox and upload when HQ is back. Approved calibration sessions wait in
`approved_pending_certificate` and get their certificate numbers on reconnect.

**Check, in order:**
1. Is the machine online at all (open any website)?
2. Is HQ up? `curl https://<hq-host>/api/sync/health`. Render free-tier
   instances sleep; the first request can take a minute.
3. Does the client still have a valid key? `sync_agent.log` showing HTTP 401
   means its key was revoked or never issued. Re-issue it (SECURITY.md,
   *Per-client sync keys*) and put it in `config.json` as `sync.auth_token`.
4. Clients that are online but not uploading trigger HQ's *sync backlog*
   alert. Look for repeated upload errors in `sync_agent.log`, and for
   rejected rows with `python helper_scripts/check_dead_letters.py`.

**New install that never connected:** if `config.json` has
`sync.enrollment_code` and no `sync.auth_token`, enrollment is still pending.
A 409 in `sync_agent.log` means HQ already has a key for this client ID —
ask HQ to re-issue it.

---

## 3. An update failed

**What the updater already did:** before applying, it backed up every file it
would change and took a snapshot of the local database. If anything failed —
download, checksum or signature, migration, or the post-update health check —
it restored the files and the database and asked for a restart. If the app
died mid-update, the next start rolls back automatically (`recover_if_needed`).

**Steps:**
1. Restart the app, then check the version shown on the Updates page.
2. Read the Updates page history and `errors.log` for the reason.
3. To roll back an update that applied but misbehaves: Updates page (staff
   accounts) →
   *Rollback* for that version. File backups are in
   `~/.local/share/cirqen/update_backups/v<version>_<timestamp>/`, with the
   database snapshot in its `__db__/` folder.
4. If the database restore failed (logged as
   `PostgreSQL restore failed ... snapshot kept at ...`), restore that
   snapshot by hand with step 4 below. The restore is all-or-nothing, so the
   database was left as it was.

---

## 4. Restoring the local database from a backup

A backup is taken every day at 12:30 while the app is running, and can be
taken by hand. Backups live in `~/.cirqen/data/backups/`.

```bash
# In a packaged install, run these through the app binary:
#   ./Cirqen manage.py backup_db --list
python manage.py backup_db            # take a backup now
python manage.py backup_db --list     # list backups, newest first
```

To restore:

1. Close the app on that machine (stop the sync agent so it does not upload
   half-restored data).
2. `python manage.py restore_db db-<timestamp>.pgdump`
   - A `...-pre-restore.pgdump` backup of the current state is taken first.
   - The restore runs in one transaction: it either fully succeeds or leaves
     the database unchanged.
3. Start the app. Changes made after the backup's timestamp exist only on HQ
   and on other sites; the sync agent downloads HQ's newer records.
4. Restored the wrong file? Restore the `-pre-restore` backup the same way.

Verified by `updates/tests_pg/test_local_backups.py` against a real PostgreSQL
cluster, including undoing a restore.
