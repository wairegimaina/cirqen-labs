"""Copy signature files into the database.

Signatures used to be written to MEDIA_ROOT as files. A rebuild or a new data
directory leaves those files behind while the row still names them, so the
signature disappears. UserSignature.signature_data is now the only place a
signature is stored; this copies every file that is still reachable into that
column. Rows whose file is already gone are left unchanged.
"""
import base64
import io

from django.db import migrations
from django.db.models import Q


def copy_signature_files_into_database(apps, schema_editor):
    from PIL import Image

    alias = schema_editor.connection.alias
    UserSignature = apps.get_model('users', 'UserSignature')
    rows = (
        UserSignature.objects.using(alias)
        .filter(Q(signature_data__isnull=True) | Q(signature_data=''))
        .exclude(Q(signature_image__isnull=True) | Q(signature_image=''))
    )
    for row in rows.iterator():
        try:
            with row.signature_image.storage.open(row.signature_image.name, 'rb') as f:
                with Image.open(io.BytesIO(f.read())) as img:
                    img.load()
                    if img.mode not in ('1', 'L', 'LA', 'P', 'RGB', 'RGBA'):
                        img = img.convert('RGBA')
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
        except Exception:
            continue
        data_uri = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
        UserSignature.objects.using(alias).filter(pk=row.pk).update(signature_data=data_uri)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_savedfilter'),
    ]

    operations = [
        migrations.RunPython(copy_signature_files_into_database, migrations.RunPython.noop),
    ]
