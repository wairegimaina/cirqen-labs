"""manage.py migrate_hq: judging each pending migration against HQ's schema."""
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from core.management.commands.migrate_hq import Schema, classify


class ClassifyTests(TestCase):
    def setUp(self):
        self.loader = MigrationLoader(connection)
        self.schema = Schema(connection)

    def _classify(self, app, name):
        migration = self.loader.get_migration(app, name)
        state = self.loader.project_state((app, name), at_end=False)
        return classify(migration, state, self.schema)

    def test_a_migration_already_on_the_database_is_faked(self):
        action, notes = self._classify("parts_tools", "0004_stock_movements")
        self.assertEqual(action, "fake")
        self.assertIn("table parts_tools_stockmovement: there", notes)

    def test_faking_says_what_it_skips(self):
        action, notes = self._classify("CalSoft", "0012_ansur_v2_db_defaults")
        self.assertEqual(action, "fake")
        self.assertTrue(any(n.startswith("not run when faked: AlterField, RunPython") for n in notes))

    def test_a_missing_column_is_applied(self):
        self.schema._columns["Inventory_equipment"] = self.schema.columns("Inventory_equipment") - {
            "label_printed_at", "label_snapshot"}
        self.assertEqual(self._classify("Inventory", "0006_equipment_qr_label")[0], "apply")

    def test_half_there_stops(self):
        self.schema._columns["Inventory_equipment"] = self.schema.columns("Inventory_equipment") - {"label_snapshot"}
        self.assertEqual(self._classify("Inventory", "0006_equipment_qr_label")[0], "stop")

    def test_a_removed_column_counts_as_done(self):
        self.assertEqual(self._classify("users", "0009_remove_email_preferences")[0], "fake")


class CommandTests(TestCase):
    def test_without_hq_database_url_it_says_how_to_set_it(self):
        with self.assertRaisesMessage(CommandError, "export HQ_DATABASE_URL="):
            call_command("migrate_hq")


class PasswordPromptTests(TestCase):
    def test_it_asks_for_a_password_the_address_leaves_out(self):
        from unittest import mock

        from django.db import connections

        hq = dict(connections.databases["default"], PASSWORD="", HOST="hq.invalid", USER="postgres.x")
        with mock.patch.dict(connections.databases, {"hq": hq}), \
                mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("getpass.getpass", return_value="s3cret") as ask, \
                mock.patch("django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection",
                           side_effect=RuntimeError("stop here")):
            with self.assertRaisesMessage(CommandError, "Cannot reach HQ's database at hq.invalid"):
                call_command("migrate_hq")
        self.assertIn("postgres.x on hq.invalid", ask.call_args[0][0])
