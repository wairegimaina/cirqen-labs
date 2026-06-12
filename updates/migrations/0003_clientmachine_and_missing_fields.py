# Migration 0003 — adds ClientMachine model and missing fields that were
# referenced in views/tasks but never defined in models.py.
#
# Adds:
#   - updates.ClientMachine  (new model)
#   - UpdatePackage.fetched_at
#   - UpdatePackage.file_count
#   - UpdateSettings.last_successful_update

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('updates', '0002_updatepackage_uploaded_file_source_uploaded_by'),
    ]

    operations = [

        # ── New model: ClientMachine ──────────────────────────────────────────
        migrations.CreateModel(
            name='ClientMachine',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name='ID',
                )),
                ('machine_id', models.CharField(max_length=100, unique=True)),
                ('hostname', models.CharField(blank=True, max_length=255)),
                ('current_version', models.CharField(default='0.0.0', max_length=50)),
                ('last_check', models.DateTimeField(blank=True, null=True)),
                ('last_update', models.DateTimeField(blank=True, null=True)),
                ('registered_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Client Machine',
                'verbose_name_plural': 'Client Machines',
                'ordering': ['-last_check'],
            },
        ),

        # ── UpdatePackage: fetched_at ─────────────────────────────────────────
        migrations.AddField(
            model_name='updatepackage',
            name='fetched_at',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # ── UpdatePackage: file_count ─────────────────────────────────────────
        migrations.AddField(
            model_name='updatepackage',
            name='file_count',
            field=models.IntegerField(default=0),
        ),

        # ── UpdateSettings: last_successful_update ────────────────────────────
        migrations.AddField(
            model_name='updatesettings',
            name='last_successful_update',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
