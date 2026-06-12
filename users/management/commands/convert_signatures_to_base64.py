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
    help = 'Convert existing signature ImageFields to base64 database fields'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Convert signature for specific user only',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite existing base64 data',
        )

    def handle(self, *args, **options):
        username = options.get('username')
        force = options.get('force', False)

        self.stdout.write("="*70)
        self.stdout.write("CONVERTING SIGNATURES TO BASE64")
        self.stdout.write("="*70)

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
            if not hasattr(user, 'signature'):
                self.stdout.write(f"  ⏭  {user.username}: No signature object")
                skipped += 1
                continue

            sig = user.signature

            # Skip if already has base64 data (unless force)
            if sig.signature_data and not force:
                self.stdout.write(f"  ⏭  {user.username}: Already has base64 data (use --force to overwrite)")
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
                    with default_storage.open(sig.signature_image.name, 'rb') as f:
                        image_data = f.read()
                    source = "storage"
                elif hasattr(sig.signature_image, 'path') and os.path.exists(sig.signature_image.path):
                    with open(sig.signature_image.path, 'rb') as f:
                        image_data = f.read()
                    source = "filesystem"
                else:
                    self.stdout.write(self.style.WARNING(f"  ⚠  {user.username}: Cannot access image file"))
                    failed += 1
                    continue

                if not image_data or len(image_data) == 0:
                    self.stdout.write(self.style.WARNING(f"  ⚠  {user.username}: Image file is empty"))
                    failed += 1
                    continue

                # Convert to base64
                base64_encoded = base64.b64encode(image_data).decode('utf-8')

                # Add data URI prefix for web compatibility
                base64_data = f"data:image/png;base64,{base64_encoded}"

                # Save to database
                sig.signature_data = base64_data
                sig.save()

                self.stdout.write(self.style.SUCCESS(
                    f"  ✓ {user.username}: Converted from {source} ({len(image_data)} bytes → {len(base64_data)} chars)"
                ))
                converted += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ {user.username}: {e}"))
                failed += 1

        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS(f"✅ Converted: {converted}"))
        self.stdout.write(f"⏭  Skipped: {skipped}")
        if failed > 0:
            self.stdout.write(self.style.WARNING(f"⚠️  Failed: {failed}"))
        self.stdout.write("="*70)


# =============================================================================
# users/management/commands/create_base64_signatures.py
# =============================================================================

"""
Management command to create base64 signatures for users who don't have any.
Generates text-based signature images programmatically.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from PIL import Image, ImageDraw, ImageFont
import base64
import io

User = get_user_model()


class Command(BaseCommand):
    help = 'Create base64 signatures for users who do not have any signature'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Create signature for specific user only',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing signatures',
        )
        parser.add_argument(
            '--width',
            type=int,
            default=400,
            help='Signature image width (default: 400)',
        )
        parser.add_argument(
            '--height',
            type=int,
            default=100,
            help='Signature image height (default: 100)',
        )

    def handle(self, *args, **options):
        username = options.get('username')
        overwrite = options.get('overwrite', False)
        width = options.get('width', 400)
        height = options.get('height', 100)

        self.stdout.write("="*70)
        self.stdout.write("CREATING BASE64 SIGNATURES")
        self.stdout.write("="*70)

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

        created = 0
        skipped = 0
        failed = 0

        for user in users:
            if not hasattr(user, 'signature'):
                self.stdout.write(f"  ⏭  {user.username}: No signature object")
                skipped += 1
                continue

            sig = user.signature

            # Skip if already has signature (unless overwrite)
            if not overwrite and (sig.signature_data or sig.signature_image):
                self.stdout.write(f"  ⏭  {user.username}: Already has signature (use --overwrite to replace)")
                skipped += 1
                continue

            try:
                # Get user's full name
                if hasattr(user, 'userprofile') and hasattr(user.userprofile, 'get_full_name'):
                    full_name = user.userprofile.get_full_name()
                else:
                    full_name = f"{user.first_name} {user.last_name}".strip() or user.username

                # Create signature image
                img = Image.new('RGB', (width, height), color='white')
                draw = ImageDraw.Draw(img)

                # Try to use a nice font
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", 24)
                except:
                    try:
                        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 24)
                    except:
                        font = ImageFont.load_default()

                # Draw signature text
                draw.text((20, 40), full_name, fill='black', font=font)

                # Convert to base64
                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
                buffer.seek(0)

                image_data = buffer.read()
                base64_encoded = base64.b64encode(image_data).decode('utf-8')
                base64_data = f"data:image/png;base64,{base64_encoded}"

                # Save to database
                sig.signature_data = base64_data
                sig.is_user_drawn = True
                sig.is_active = True
                sig.save()

                # Update profile
                try:
                    profile = user.userprofile
                    profile.has_uploaded_signature = True
                    profile.save()
                except:
                    pass

                self.stdout.write(self.style.SUCCESS(
                    f"  ✓ Created signature for {user.username} ({full_name})"
                ))
                created += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed for {user.username}: {e}"))
                import traceback
                self.stdout.write(traceback.format_exc())
                failed += 1

        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS(f"✅ Created: {created}"))
        self.stdout.write(f"⏭  Skipped: {skipped}")
        if failed > 0:
            self.stdout.write(self.style.WARNING(f"⚠️  Failed: {failed}"))
        self.stdout.write("="*70)


# =============================================================================
# users/management/commands/verify_signatures.py
# =============================================================================

"""
Management command to verify signature integrity and accessibility.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = 'Verify signature integrity for all users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Verify signature for specific user only',
        )

    def handle(self, *args, **options):
        username = options.get('username')

        self.stdout.write("="*70)
        self.stdout.write("SIGNATURE VERIFICATION REPORT")
        self.stdout.write("="*70)

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
            if not hasattr(user, 'signature'):
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
        self.stdout.write("\n" + "="*70)
        self.stdout.write("SUMMARY")
        self.stdout.write("="*70)
        self.stdout.write(f"Total users with signature object: {total}")
        self.stdout.write(self.style.SUCCESS(f"  ✓ Has base64 (PyInstaller ready): {has_base64 + has_both}"))
        self.stdout.write(self.style.WARNING(f"  ⚠ Has ImageField only: {has_imagefield}"))
        self.stdout.write(self.style.ERROR(f"  ✗ No signature data: {has_none}"))
        self.stdout.write("="*70)

        if has_imagefield > 0:
            self.stdout.write("\n" + self.style.WARNING("RECOMMENDATION:"))
            self.stdout.write("Run: python manage.py convert_signatures_to_base64")

        if has_none > 0:
            self.stdout.write("\n" + self.style.WARNING("RECOMMENDATION:"))
            self.stdout.write("Run: python manage.py create_base64_signatures")


