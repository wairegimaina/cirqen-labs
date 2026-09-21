from django.db import migrations


def create_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    from core.db_indexes import ensure_indexes

    with schema_editor.connection.cursor() as cursor:
        ensure_indexes(cursor)


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    # Concrete names, not "__latest__": a later migration in one of these apps
    # must not become a dependency of this already-applied one.
    dependencies = [
        ("jobcard", "0001_initial"),
        ("CalSoft", "0005_alter_calibrationauditlog_schedule"),
        ("calSchedules", "0003_alter_calibrationschedule_status"),
        ("Inventory", "0002_initial"),
        ("ppms", "0002_ppmschedule_expected_maintenance_date_and_more"),
        ("parts_tools", "0001_initial"),
        ("users", "0005_copy_signature_files_into_database"),
        ("machineReports", "0001_initial"),
        ("workshop", "0001_initial"),
        ("accounts", "0001_initial"),
    ]

    operations = [migrations.RunPython(create_indexes, migrations.RunPython.noop)]
