# Operations: monitoring, backups and recovery

Targets (plan 2D): at most **24 hours** of data lost at a hospital, **15
minutes** at HQ, and service back within **4 hours**.

## Monitoring

| What | How | Where to set it |
|---|---|---|
| Errors in the app, sync agent and HQ | Sentry: set `SENTRY_DSN` (and `SENTRY_ENVIRONMENT`) | hospital server: `/etc/cirqen/cirqen.env`; HQ: Render environment |
| HQ is up | An uptime monitor (UptimeRobot, Better Stack or similar) on `https://<hq>/api/sync/health`, every 1–5 min, alert after 2 failures | the monitoring service |
| Hospital server is up | Same, on `https://<hospital-server>/health/` from inside the hospital network, or from HQ over a VPN | the monitoring service |
| Fleet health | `GET /api/sync/ops` with the admin token: stale sites, versions, backlog, failed uploads, certificates waiting. `"ok": false` lists the problems | an uptime monitor that checks for `"ok":true` |
| A site stops syncing | Built in: alerts after `SYNC_STALE_ALERT_HOURS` (default 2) and on upload backlog | HQ env: `ALERT_WEBHOOK_URL` and/or `ALERT_EMAIL_ENABLED=true`, `ALERT_EMAIL_TO`, `RESEND_API_KEY` |
| Backups stopped | Built in: a daily check at 13:30 that the newest backup is under 36 h old and readable; failure is logged at ERROR (reaches Sentry) | nothing to set once Sentry is on |

## Backups

**Hospital server.** Daily at 12:30 (`core.tasks.backup_local_database`),
kept for `CIRQEN_BACKUP_KEEP` days (30 on servers), copied to
`CIRQEN_BACKUP_COPY_DIR` (a share or disk off the server). Check by hand:

```bash
sudo -u cirqen bash -c 'set -a; source /etc/cirqen/cirqen.env; set +a;
  cd /opt/cirqen/app && /opt/cirqen/venv/bin/python manage.py backup_db --verify'
```

**HQ.** Supabase daily backups plus point-in-time recovery (Pro plan and
above). Point-in-time recovery is what meets the 15-minute target; confirm it
is enabled under Project settings, Database, Backups.

**Desktops.** Each desktop keeps 14 daily backups in its data folder. Its
records also reach HQ within minutes when online, so HQ is the second copy.

## Restore drill (every quarter)

Do it on a spare machine or VM, never on the live server.

1. Install Cirqen in server mode on the spare machine
   (`deploy/server/install.sh --server-name drill.local`).
2. Copy the newest backup from the backup share to
   `/var/lib/cirqen/backups/` on the spare machine.
3. Stop the services: `sudo systemctl stop cirqen-web cirqen-worker cirqen-beat cirqen-sync`.
4. Restore: `manage.py restore_db <file>` (as in step "Restoring the local
   database from a backup" in RUNBOOK.md).
5. Start the web service, sign in, and check: the equipment count, the latest
   work orders, the latest calibration certificates (open one PDF; its stored
   copy must pass the fingerprint check), and a user can sign in.
6. Record the date, the backup used, how long it took and anything that went
   wrong in the drill log. If it took over 4 hours, fix the cause before the
   next quarter.

Do **not** start `cirqen-sync` on the drill machine: it would talk to HQ as
if it were the hospital.

## If the hospital server is lost

1. Build a new server (same Ubuntu version) and run the installer.
2. Restore the newest backup from the copy location (steps 2–4 above).
3. Point the DNS name at the new server; install the TLS certificate.
4. Start `cirqen-sync` last: it pushes anything newer than HQ has and pulls
   what HQ has that the backup missed.

## Two-factor reset

Someone who lost their authenticator app signs in with one of their
recovery codes. If they have none left, after confirming who they are
(in person, or a call-back to the number on record):

```bash
sudo -u cirqen bash -c 'set -a; source /etc/cirqen/cirqen.env; set +a;
  cd /opt/cirqen/app && /opt/cirqen/venv/bin/python manage.py reset_two_factor <username>'
```

On a desktop install, run `manage.py reset_two_factor <username>` from the
Cirqen folder. The reset is recorded in the user's security log. They sign
in with their password and set two-factor up again.
