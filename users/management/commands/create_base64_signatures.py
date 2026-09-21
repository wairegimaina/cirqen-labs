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
    help = "Create base64 signatures for users who do not have any signature"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            help="Create signature for specific user only",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite existing signatures",
        )
        parser.add_argument(
            "--width",
            type=int,
            default=400,
            help="Signature image width (default: 400)",
        )
        parser.add_argument(
            "--height",
            type=int,
            default=100,
            help="Signature image height (default: 100)",
        )

    def handle(self, *args, **options):
        username = options.get("username")
        overwrite = options.get("overwrite", False)
        width = options.get("width", 400)
        height = options.get("height", 100)

        self.stdout.write("=" * 70)
        self.stdout.write("CREATING BASE64 SIGNATURES")
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

        created = 0
        skipped = 0
        failed = 0

        for user in users:
            if not hasattr(user, "signature"):
                self.stdout.write(f"  ⏭  {user.username}: No signature object")
                skipped += 1
                continue

            sig = user.signature

            # Skip if already has signature (unless overwrite)
            if not overwrite and (sig.signature_data or sig.signature_image):
                self.stdout.write(
                    f"  ⏭  {user.username}: Already has signature (use --overwrite to replace)"
                )
                skipped += 1
                continue

            try:
                # Get user's full name
                if hasattr(user, "userprofile") and hasattr(user.userprofile, "get_full_name"):
                    full_name = user.userprofile.get_full_name()
                else:
                    full_name = f"{user.first_name} {user.last_name}".strip() or user.username

                # Create signature image
                img = Image.new("RGB", (width, height), color="white")
                draw = ImageDraw.Draw(img)

                # Try to use a nice font
                try:
                    font = ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", 24
                    )
                except:
                    try:
                        font = ImageFont.truetype(
                            "/System/Library/Fonts/Supplemental/Arial.ttf", 24
                        )
                    except:
                        font = ImageFont.load_default()

                # Draw signature text
                draw.text((20, 40), full_name, fill="black", font=font)

                # Convert to base64
                buffer = io.BytesIO()
                img.save(buffer, format="PNG")
                buffer.seek(0)

                image_data = buffer.read()
                base64_encoded = base64.b64encode(image_data).decode("utf-8")
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

                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ Created signature for {user.username} ({full_name})")
                )
                created += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed for {user.username}: {e}"))
                import traceback

                self.stdout.write(traceback.format_exc())
                failed += 1

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS(f"✅ Created: {created}"))
        self.stdout.write(f"⏭  Skipped: {skipped}")
        if failed > 0:
            self.stdout.write(self.style.WARNING(f"⚠️  Failed: {failed}"))
        self.stdout.write("=" * 70)
