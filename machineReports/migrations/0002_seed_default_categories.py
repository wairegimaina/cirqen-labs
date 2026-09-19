from django.db import migrations

# The dashboard offers no way to create a category, so a fresh install had an
# empty "Assign New Category" dropdown. These two are the whole vocabulary the
# reports use, so they ship with the schema.
DEFAULTS = [
    ("Critical", True, "Equipment whose failure stops patient care or a service."),
    ("General", False, "Equipment that can be out of service without stopping care."),
]


def seed_categories(apps, schema_editor):
    EquipmentCategory = apps.get_model("machineReports", "EquipmentCategory")

    for name, is_critical, description in DEFAULTS:
        category, created = EquipmentCategory.objects.get_or_create(
            name=name,
            defaults={
                "is_critical": is_critical,
                "description": description,
                "needs_sync": True,
            },
        )
        # Hand-made rows exist with is_critical left at its default False, which
        # silently zeroed every critical-equipment metric (they all filter on
        # category__is_critical). Put the flag back.
        if not created and category.is_critical != is_critical:
            category.is_critical = is_critical
            category.needs_sync = True
            category.save(update_fields=["is_critical", "needs_sync", "updated_at"])


def unseed_categories(apps, schema_editor):
    # Categories that equipment now points at must not be removed on a reverse.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("machineReports", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_categories, unseed_categories),
    ]
