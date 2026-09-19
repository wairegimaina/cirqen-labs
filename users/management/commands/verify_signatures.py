"""
Management command to verify signature integrity and accessibility.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = "Verify signature integrity for all users"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            help="Verify signature for specific user only",
        )

    def handle(self, *args, **options):
        username = options.get("username")

        self.stdout.write("=" * 70)
        self.stdout.write("SIGNATURE VERIFICATION REPORT")
        self.stdout.write("=" * 70)

        # Get users to check
        if username:
            try:
                users = [User.objects.get(username=username)]
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"User '{username}' not found"))
                return
        else:
            users = User.objects.all()

        total = 0
        has_base64 = 0
        has_imagefield = 0
        has_both = 0
        has_none = 0

        for user in users:
            if not hasattr(user, "signature"):
                continue

            total += 1
            sig = user.signature

            has_b64 = bool(sig.signature_data)
            has_img = bool(sig.signature_image)

            if has_b64 and has_img:
                has_both += 1
                status = "✓ Both (Base64 + ImageField)"
                style = self.style.SUCCESS
            elif has_b64:
                has_base64 += 1
                status = "✓ Base64 only"
                style = self.style.SUCCESS
            elif has_img:
                has_imagefield += 1
                status = "⚠ ImageField only (convert to base64 recommended)"
                style = self.style.WARNING
            else:
                has_none += 1
                status = "✗ No signature"
                style = self.style.ERROR

            if username:  # Show details for single user
                self.stdout.write(f"\nUser: {user.username}")
                self.stdout.write(f"Status: {style(status)}")
                if has_b64:
                    self.stdout.write(f"Base64 length: {len(sig.signature_data)} chars")
                if has_img:
                    self.stdout.write(f"ImageField: {sig.signature_image.name}")

        # Summary
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Total users with signature object: {total}")
        self.stdout.write(
            self.style.SUCCESS(f"  ✓ Has base64 (PyInstaller ready): {has_base64 + has_both}")
        )
        self.stdout.write(self.style.WARNING(f"  ⚠ Has ImageField only: {has_imagefield}"))
        self.stdout.write(self.style.ERROR(f"  ✗ No signature data: {has_none}"))
        self.stdout.write("=" * 70)

        if has_imagefield > 0:
            self.stdout.write("\n" + self.style.WARNING("RECOMMENDATION:"))
            self.stdout.write("Run: python manage.py convert_signatures_to_base64")

        if has_none > 0:
            self.stdout.write("\n" + self.style.WARNING("RECOMMENDATION:"))
            self.stdout.write("Run: python manage.py create_base64_signatures")
