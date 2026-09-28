"""Bring HQ's database up to this code's migrations, safely, from an operator PC.

    HQ_DATABASE_URL='postgresql://...' python manage.py migrate_hq          # show the plan
    HQ_DATABASE_URL='postgresql://...' python manage.py migrate_hq --apply  # do it

Installed PCs never connect to HQ's database (they use its sync API), so HQ's
schema is kept in step from here. It has also been changed by hand-written SQL
(hq_server/migrations, applied on HQ boot), so a plain ``migrate --database=hq``
would stop on a column that already exists. This command looks at each
pending migration first:

    fake    everything it adds is already there: recorded as applied, not run
    apply   none of it is there: run
    STOP    some of it is there: nothing is run; the report says what differs

Afterwards (``--apply``):
* foreign keys this run created are dropped again, because HQ is built
  without them (sync can deliver a child row before its parent);
* every table with ``updated_at`` gets ``source_updated_at``, HQ's
  last-write-wins column (as hq_server's 2026_add_source_updated_at.sql).
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations import operations as ops
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

ALIAS = "hq"

SOURCE_UPDATED_AT_SQL = """
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT c.table_name FROM information_schema.columns c
        JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = 'public' AND c.column_name = 'updated_at' AND t.table_type = 'BASE TABLE'
          AND NOT EXISTS (SELECT 1 FROM information_schema.columns x WHERE x.table_schema = 'public'
                          AND x.table_name = c.table_name AND x.column_name = 'source_updated_at')
    LOOP
        EXECUTE format('ALTER TABLE public.%I ADD COLUMN source_updated_at timestamptz', r.table_name);
        EXECUTE format('UPDATE public.%I SET source_updated_at = updated_at WHERE source_updated_at IS NULL',
                       r.table_name);
    END LOOP;
END $$;
"""


def _foreign_keys(connection):
    with connection.cursor() as cur:
        cur.execute("""SELECT conrelid::regclass::text, conname FROM pg_constraint c
                       JOIN pg_namespace n ON n.oid = c.connamespace
                       WHERE c.contype = 'f' AND n.nspname = 'public'""")
        return set(cur.fetchall())


class Schema:
    """What HQ's database has, read once."""

    def __init__(self, connection):
        self.connection = connection
        self._intro = connection.introspection
        self._columns = {}
        with connection.cursor() as cur:
            self.tables = set(self._intro.table_names(cur))
            cur.execute("SELECT conname FROM pg_constraint UNION SELECT indexname FROM pg_indexes")
            self.names = {row[0] for row in cur.fetchall()}

    def applied(self, migration, before, after):
        """Update this picture as if ``migration`` had run, so the migrations
        after it are judged against what HQ will have by then (one run can
        create a table that a later migration changes)."""
        app = migration.app_label
        for op in migration.operations:
            if isinstance(op, ops.DeleteModel):
                table = before.apps.get_model(app, op.name)._meta.db_table
                self.tables.discard(table)
                self._columns.pop(table, None)
                continue
            if isinstance(op, (ops.AddIndex, ops.AddConstraint)):
                self.names.add((op.index if isinstance(op, ops.AddIndex) else op.constraint).name)
            name = getattr(op, "model_name", None) or getattr(op, "name", None)
            if isinstance(op, ops.RenameModel):
                old = before.apps.get_model(app, op.old_name)._meta.db_table
                self.tables.discard(old)
                self._columns.pop(old, None)
                name = op.new_name
            if not name or (app, name.lower()) not in after.models:
                continue
            model = after.apps.get_model(app, name)
            table = model._meta.db_table
            self.tables.add(table)
            self._columns[table] = {f.column for f in model._meta.concrete_fields}

    def columns(self, table):
        if table not in self._columns:
            with self.connection.cursor() as cur:
                self._columns[table] = ({d.name for d in self._intro.get_table_description(cur, table)}
                                        if table in self.tables else set())
        return self._columns[table]


def _column(field, name):
    return field.db_column or (f"{name}_id" if field.is_relation and not field.many_to_many else name)


def classify(migration, state, schema):
    """("apply" | "fake" | "stop", [what was checked]) for one migration, given
    the project state just before it."""
    present, absent, notes, unchecked = 0, 0, [], []
    app = migration.app_label
    for op in migration.operations:
        if isinstance(op, ops.CreateModel):
            table = (op.options or {}).get("db_table") or f"{app}_{op.name.lower()}"
            table = next((t for t in schema.tables if t.lower() == table.lower()), table)
            there = table in schema.tables
            notes.append(f"table {table}: {'there' if there else 'missing'}")
        elif isinstance(op, (ops.AddField, ops.RemoveField)):
            model = state.apps.get_model(app, op.model_name)
            table = model._meta.db_table
            field = op.field if isinstance(op, ops.AddField) else model._meta.get_field(op.name)
            if field.many_to_many:
                continue
            column = _column(field, op.name)
            exists = column in schema.columns(table)
            there = exists if isinstance(op, ops.AddField) else not exists
            notes.append(f"{table}.{column}: {'there' if exists else 'missing'}"
                         + (" (removed)" if isinstance(op, ops.RemoveField) and not exists else ""))
        elif isinstance(op, ops.DeleteModel):
            table = state.apps.get_model(app, op.name)._meta.db_table
            there = table not in schema.tables
            notes.append(f"table {table}: {'gone' if there else 'still there'}")
        elif isinstance(op, (ops.AddIndex, ops.AddConstraint)):
            name = (op.index if isinstance(op, ops.AddIndex) else op.constraint).name
            there = name in schema.names
            notes.append(f"{name}: {'there' if there else 'missing'}")
        else:
            # AlterField, RunPython, ...: nothing to compare. They run when the
            # migration is applied, and are skipped when it is faked (said below).
            unchecked.append(type(op).__name__)
            continue
        if there:
            present += 1
        else:
            absent += 1
    if present and absent:
        return "stop", notes
    if present:
        if unchecked:
            notes.append("not run when faked: " + ", ".join(sorted(set(unchecked)))
                         + " (check HQ has these, e.g. from its SQL migrations)")
        return "fake", notes
    return "apply", notes


class Command(BaseCommand):
    help = "Show, then (with --apply) run, the migrations HQ's database is missing (needs HQ_DATABASE_URL)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Make the changes (default: only show the plan).")
        parser.add_argument("--keep-foreign-keys", action="store_true",
                            help="Keep the foreign keys new tables get (HQ is normally built without them).")

    def handle(self, *args, apply=False, keep_foreign_keys=False, **options):
        if ALIAS not in connections.databases:
            raise CommandError(
                "HQ's database is not set. In this shell only (never in config.json):\n"
                "  export HQ_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres?sslmode=require'\n"
                "then run this again.")
        connection = connections[ALIAS]
        settings = connection.settings_dict
        try:
            connection.ensure_connection()
        except Exception as exc:
            raise CommandError(f"Cannot reach HQ's database at {settings['HOST']}: {exc}")
        self.stdout.write(f"HQ database: {settings['NAME']} on {settings['HOST']}:{settings['PORT']}")

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not plan:
            self.stdout.write(self.style.SUCCESS("HQ is up to date: nothing to migrate."))
            if apply:
                self._after(connection, set(), keep_foreign_keys=True)
            return
        if any(backwards for _, backwards in plan):
            raise CommandError("HQ is ahead of this code (a migration would be reversed). Update this checkout.")

        schema = Schema(connection)
        rows, stops = [], []
        for migration, _ in plan:
            key = (migration.app_label, migration.name)
            before = executor.loader.project_state(key, at_end=False)
            action, notes = classify(migration, before, schema)
            rows.append((migration, action, notes))
            if action == "stop":
                stops.append(migration)
            elif action == "apply":
                schema.applied(migration, before, executor.loader.project_state(key, at_end=True))

        self.stdout.write(f"\n{len(rows)} migration(s) HQ has not recorded:\n")
        for migration, action, notes in rows:
            label = {"apply": self.style.WARNING("apply"), "fake": self.style.SUCCESS("fake "),
                     "stop": self.style.ERROR("STOP ")}[action]
            self.stdout.write(f"  {label}  {migration.app_label}.{migration.name}")
            if action != "apply" or len(notes) <= 3:
                for note in notes:
                    self.stdout.write(f"           {note}")

        if stops:
            raise CommandError(
                f"{len(stops)} migration(s) are partly on HQ already, so nothing was changed. "
                "Make HQ match one way or the other (add the missing pieces by hand, or remove the "
                "partial ones), then run this again.")
        if not apply:
            self.stdout.write("\nNothing was changed. Run with --apply to do this.")
            return

        before = _foreign_keys(connection)
        recorder = MigrationRecorder(connection)
        recorder.ensure_schema()
        for migration, action, _ in rows:
            name = f"{migration.app_label}.{migration.name}"
            if action == "fake":
                recorder.record_applied(migration.app_label, migration.name)
                self.stdout.write(f"  faked    {name}")
            else:
                call_command("migrate", migration.app_label, migration.name, database=ALIAS,
                             verbosity=0, interactive=False)
                self.stdout.write(f"  applied  {name}")
        self._after(connection, before, keep_foreign_keys)
        self.stdout.write(self.style.SUCCESS(
            "\nHQ is up to date. Its sync API picks up new columns within 5 minutes (its column cache)."))

    def _after(self, connection, before, keep_foreign_keys):
        if not keep_foreign_keys:
            new = sorted(_foreign_keys(connection) - before)
            with connection.cursor() as cur:
                for table, name in new:
                    cur.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"')
            if new:
                self.stdout.write(f"  dropped {len(new)} foreign key(s) new tables got (HQ is built without them)")
        with connection.cursor() as cur:
            cur.execute(SOURCE_UPDATED_AT_SQL)
        self.stdout.write("  every table with updated_at has source_updated_at")
