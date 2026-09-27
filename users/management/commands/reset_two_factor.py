"""Turn off two-factor sign-in for someone who lost their authenticator and
their recovery codes. They can sign in with their password and set it up again.

    python manage.py reset_two_factor jdoe
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from users.models import TwoFactorDevice, UserSecurityLog


class Command(BaseCommand):
    help = "Remove a user's two-factor authenticator so they can register a new one."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username, **options):
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"No user named {username!r}.")
        deleted, _ = TwoFactorDevice.objects.filter(user=user).delete()
        if not deleted:
            raise CommandError(f"{username} does not have two-factor sign-in turned on.")
        UserSecurityLog.log_event(user, "TWO_FACTOR_DISABLED", "Reset by an administrator (reset_two_factor)")
        self.stdout.write(self.style.SUCCESS(
            f"Two-factor sign-in removed for {username}. They should set it up again after signing in."))
