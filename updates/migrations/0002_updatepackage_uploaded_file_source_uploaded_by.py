# Generated migration — adds local upload support to UpdatePackage

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('updates', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='updatepackage',
            name='source',
            field=models.CharField(
                choices=[('hq_server', 'HQ Server'), ('local_upload', 'Local Upload')],
                default='hq_server',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='updatepackage',
            name='uploaded_file',
            field=models.FileField(
                blank=True,
                help_text='Upload a .zip update package built on the dev laptop',
                null=True,
                upload_to='updates/packages/',
            ),
        ),
        migrations.AddField(
            model_name='updatepackage',
            name='uploaded_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='uploaded_packages',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='updatepackage',
            name='package_path',
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
