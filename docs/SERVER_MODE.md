# Hospital server mode

The desktop app runs Cirqen on one PC. Server mode runs it once, on a
hospital server, and everyone (technologists, HOD, department in-charges)
uses it from a browser on the hospital network. Use it when more than a
handful of people need Cirqen at the same time: one database, no per-PC
installs, and one place to back up.

What runs on the server:

| Service          | What it does                                             |
|------------------|----------------------------------------------------------|
| `nginx`          | HTTPS on port 443, serves static files, redirects HTTP   |
| `cirqen-web`     | The Cirqen web app (uvicorn, 4 workers, 127.0.0.1:8000)  |
| `cirqen-worker`  | Background jobs: emails, certificates, reports           |
| `cirqen-beat`    | Scheduled jobs: nightly backup, PPM and calibration due  |
| `cirqen-sync`    | Keeps this site in step with Cirqen HQ when online       |
| `postgresql`     | The site database                                        |
| `redis-server`   | Cache and job queue                                      |

Offline working is the same as on the desktop: if the internet link is down,
the site keeps working and `cirqen-sync` catches up once HQ can be reached.

## Requirements

- Ubuntu Server 22.04 or 24.04, 4 CPU cores, 8 GB RAM, 100 GB disk
  (enough for a Level 6 hospital: about 15 staff users and 200 departments).
- A fixed IP address on the hospital network, and ideally a DNS name
  (for example `cirqen.hospital.local`) from the hospital IT team.
- A place for a second copy of backups: a network share, NAS or USB disk.
- An enrollment code from Cirqen Labs, to link the site to HQ.

## Install

Copy the Cirqen source to the server, then from inside it:

```bash
sudo deploy/server/install.sh --server-name cirqen.hospital.local,10.0.0.20
```

`--server-name` lists every name or address people will type in the browser.
Requests for any other name are refused.

To use a certificate from the hospital's certificate authority (recommended,
so browsers trust the site without warnings):

```bash
sudo deploy/server/install.sh --server-name cirqen.hospital.local \
     --cert /path/cirqen.crt --key /path/cirqen.key
```

Without `--cert`, the installer makes a self-signed certificate. It works,
but every browser shows a warning until the certificate is replaced.

The installer:

1. Installs PostgreSQL, Redis, nginx and Python.
2. Creates a `cirqen` system account; the app runs as that account, not root.
3. Puts the code in `/opt/cirqen/app` and its Python environment in
   `/opt/cirqen/venv`.
4. Writes `/etc/cirqen/cirqen.env` with a new secret key and database
   password (readable by root and `cirqen` only).
5. Creates the database, runs migrations, collects static files and runs
   Django's deployment checks.
6. Installs and starts the services, then checks `https://<name>/health/`.

It is safe to run again: secrets, the database and data are kept.

## After installing

### 1. Create the first HOD account

```bash
sudo -u cirqen bash -c 'set -a; source /etc/cirqen/cirqen.env; set +a;
  cd /opt/cirqen/app && /opt/cirqen/venv/bin/python manage.py create_hod \
  --username jdoe --email jdoe@hospital.example --first-name Jane --last-name Doe'
```

It prints a one-time password once. At first login the HOD must choose a new
password and draw a signature; until then no other page opens. The HOD then
creates workshops, departments and the other users from within Cirqen, and
each of them goes through the same first-login setup.

### 2. Link to Cirqen HQ

Put the enrollment code from Cirqen Labs in `/etc/cirqen/cirqen.env`:

```
SYNC_ENROLLMENT_CODE=<code>
```

then `sudo systemctl restart cirqen-sync`. The agent swaps the code for this
site's own key on first contact. Check with `journalctl -u cirqen-sync -f`.

Certificate QR codes point at HQ's public verification page, so anyone can
check a printed certificate from a phone.

### 3. Set up the second backup copy

A backup is taken every day at 12:30 into `/var/lib/cirqen/backups`, and the
newest 14 are kept. A backup on the same disk as the database is lost with
it, so also set:

```
CIRQEN_BACKUP_COPY_DIR=/mnt/cirqen-backups
```

pointing at a mounted share or disk, then
`sudo systemctl restart cirqen-worker cirqen-beat`. Each backup is copied
there too (the newest 14 are kept). If the share is not mounted, the copy
fails and is logged instead of silently filling the server's own disk.

Take a backup by hand, or list them:

```bash
sudo -u cirqen bash -c 'set -a; source /etc/cirqen/cirqen.env; set +a;
  cd /opt/cirqen/app && /opt/cirqen/venv/bin/python manage.py backup_db'
# add --list to see existing backups
```

To restore, see "Restoring the local database from a backup" in
[RUNBOOK.md](RUNBOOK.md); run the
commands as above, with `cirqen-web`, `cirqen-worker` and `cirqen-beat`
stopped first.

## Everyday operation

| Task                  | Command                                                     |
|-----------------------|-------------------------------------------------------------|
| Status                | `systemctl status 'cirqen-*'`                               |
| Logs                  | `journalctl -u cirqen-web -f` (or `-worker`, `-sync`, `-beat`) |
| App logs              | `/var/lib/cirqen/logs/`                                     |
| Restart after a change to `cirqen.env` | `sudo systemctl restart cirqen-web cirqen-worker cirqen-beat cirqen-sync` |
| Health                | `curl -k https://cirqen.hospital.local/health/`             |

### Upgrading

Get the new Cirqen source onto the server and run the installer again from
it (no options needed). It updates the code and dependencies, runs any new
migrations and restarts the services. Take a backup first.

### Adding a name or address

Run the installer again with the full new list in `--server-name`. With a
self-signed certificate, delete `/etc/cirqen/tls/cirqen.crt` first so a new
one covering the new names is made.

## Files

| Path                          | Contents                                 |
|-------------------------------|------------------------------------------|
| `/etc/cirqen/cirqen.env`      | Settings and secrets (mode 640)          |
| `/etc/cirqen/tls/`            | HTTPS certificate and key                |
| `/opt/cirqen/app`             | Cirqen code (replaced on upgrade)        |
| `/opt/cirqen/venv`            | Python environment                       |
| `/var/lib/cirqen`             | Data: config, logs, backups, uploads, static files |
| `deploy/server/`              | Installer, systemd units, nginx site, env template |

## Security notes

- Only nginx listens on the network; the app, database and Redis listen on
  127.0.0.1 only. Open port 443 (and 80, for the redirect) in the firewall.
- Session and CSRF cookies are HTTPS-only; HSTS is sent.
- Services run as the unprivileged `cirqen` account with systemd hardening
  (read-only `/usr` and `/etc`, private `/tmp`, no privilege escalation).
- Keep `/etc/cirqen/cirqen.env` out of email and chat; it holds the
  database password and the secret key that signs sessions.
