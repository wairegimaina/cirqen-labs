"""Create the first head-of-department account on a hospital server.

The desktop setup wizard creates the first HOD; a server has no wizard. This
makes one with a random one-time password, printed once, which must be
changed (and a signature uploaded) at first login via force_setup.

    python manage.py create_hod --username jdoe --email jdoe@hospital.example \
        --first-name Jane --last-name Doe
"""
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from users.models import UserProfile


class Command(BaseCommand):
    help = "Create a head-of-department (HOD) account with a one-time password."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")

    def handle(self, *args, username, email, first_name, last_name, **options):
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            raise CommandError(f"User '{username}' already exists; nothing changed.")
        password = secrets.token_urlsafe(12)
        with transaction.atomic():
            user = User.objects.create_user(
                username=username, email=email, password=password,
                first_name=first_name, last_name=last_name,
                is_staff=True, is_superuser=False,
            )
            UserProfile.objects.update_or_create(user=user, defaults={
                "role": "HOD", "must_change_password": True,
                "has_uploaded_signature": False, "is_approved": True,
            })
        self.stdout.write(self.style.SUCCESS(f"Created HOD account '{username}'."))
        self.stdout.write(f"One-time password: {password}")
        self.stdout.write("It must be changed at first login. It is not shown again.")
