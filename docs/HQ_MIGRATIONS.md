# Migrating HQ's database

Installed PCs never connect to HQ's database: they use its sync API, and hold
no HQ database password (commit 9abdf3e). HQ's schema is brought up to date
from an operator PC with one command, run by hand when a release adds
migrations.

## Run it

In a terminal on the developer PC, in `cirqen-labs` with the release checked out:

```
export HQ_DATABASE_URL='postgresql://postgres.nwlwaeeyduxroykrgksi@aws-0-eu-north-1.pooler.supabase.com:5432/postgres?sslmode=require'
venv/bin/python manage.py migrate_hq            # asks for the password, shows the plan, changes nothing
venv/bin/python manage.py migrate_hq --apply    # asks again, then does it
unset HQ_DATABASE_URL
```

Leave the password out of the address: the command asks for it (hidden) each
time and never stores it. It is HQ's database password (Render: `cirqen-hq` >
Environment > `POSTGRES_HQ_PASSWORD`), not a login password. Use port 5432
(Supabase's session pooler), not HQ's 6543.

- Take the address from the HQ database provider (Supabase: *Project settings >
  Database > Connection string*, the direct or session connection on port 5432).
- The variable lives in that terminal only. Never put it in `config.json`,
  provisioning files or a build.
- Take a backup of HQ first (the provider's backup, or `pg_dump`).

## What the plan means

HQ's schema is also changed by the SQL files in `hq_server/migrations`, which
HQ runs when it starts. So before running anything, the command compares each
migration HQ has not recorded with HQ's actual tables:

| Plan says | Meaning | With `--apply` |
|---|---|---|
| `apply` | None of it is on HQ | Run |
| `fake` | All of it is on HQ already (for example from an SQL file) | Recorded as done, not run |
| `STOP` | Part of it is on HQ | Nothing at all is changed; the lines under it say what is there and what is missing |

A `fake` line may add *not run when faked: AlterField, RunPython*: those steps
cannot be compared and are skipped. Check HQ has them (the matching SQL file
usually sets the same defaults) before applying.

After applying, the command also:

- drops the foreign keys new tables were given, because HQ is built without
  them (sync can deliver a row before the row it points to);
- adds `source_updated_at` (HQ's last-write-wins column) to any table missing it.

HQ's sync API sees new columns within 5 minutes (its column cache). New tables
must also be added to HQ's `TABLES` environment variable on Render to sync.

## Rehearse first

Point `HQ_DATABASE_URL` at a copy of HQ (restore a backup into a scratch
database) and run `--apply` there before running it on HQ.
