# users/management/commands/convert_signatures_to_base64.py
"""
Management command to convert existing ImageField signatures to base64 format.
This prepares signatures for PyInstaller frozen applications.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
import base64
import os

User = get_user_model()


class Command(BaseCommand):
    help = "Convert existing signature ImageFields to base64 database fields"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            help="Convert signature for specific user only",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing base64 data",
        )

    def handle(self, *args, **options):
        username = options.get("username")
        force = options.get("force", False)

        self.stdout.write("=" * 70)
        self.stdout.write("CONVERTING SIGNATURES TO BASE64")
        self.stdout.write("=" * 70)

        # Get users to process
        if username:
            try:
                users = [User.objects.get(username=username)]
                self.stdout.write(f"Processing single user: {username}\n")
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"User '{username}' not found"))
                return
        else:
            users = User.objects.all()
            self.stdout.write(f"Processing all users ({users.count()} total)\n")

        converted = 0
        skipped = 0
        failed = 0

        for user in users:
            if not hasattr(user, "signature"):
                self.stdout.write(f"  ⏭  {user.username}: No signature object")
                skipped += 1
                continue

            sig = user.signature

            # Skip if already has base64 data (unless force)
            if sig.signature_data and not force:
                self.stdout.write(
                    f"  ⏭  {user.username}: Already has base64 data (use --force to overwrite)"
                )
                skipped += 1
                continue

            # Skip if no ImageField data
            if not sig.signature_image:
                self.stdout.write(f"  ⏭  {user.username}: No signature image file")
                skipped += 1
                continue

            try:
                # Read the image file
                image_data = None

                if default_storage.exists(sig.signature_image.name):
                    with default_storage.open(sig.signature_image.name, "rb") as f:
                        image_data = f.read()
                    source = "storage"
                elif hasattr(sig.signature_image, "path") and os.path.exists(
                    sig.signature_image.path
                ):
                    with open(sig.signature_image.path, "rb") as f:
                        image_data = f.read()
                    source = "filesystem"
                else:
                    self.stdout.write(
                        self.style.WARNING(f"  ⚠  {user.username}: Cannot access image file")
                    )
                    failed += 1
                    continue

                if not image_data or len(image_data) == 0:
                    self.stdout.write(
                        self.style.WARNING(f"  ⚠  {user.username}: Image file is empty")
                    )
                    failed += 1
                    continue

                # Convert to base64
                base64_encoded = base64.b64encode(image_data).decode("utf-8")

                # Add data URI prefix for web compatibility
                base64_data = f"data:image/png;base64,{base64_encoded}"

                # Save to database
                sig.signature_data = base64_data
                sig.save()

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ {user.username}: Converted from {source} ({len(image_data)} bytes → {len(base64_data)} chars)"
                    )
                )
                converted += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ {user.username}: {e}"))
                failed += 1

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS(f"✅ Converted: {converted}"))
        self.stdout.write(f"⏭  Skipped: {skipped}")
        if failed > 0:
            self.stdout.write(self.style.WARNING(f"⚠️  Failed: {failed}"))
        self.stdout.write("=" * 70)
