"""Sign-in security events are kept for SECURITY_LOG_DAYS, then deleted."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.tasks import prune_security_log
from users.models import UserSecurityLog


class PruneSecurityLogTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("auditee", password="x")
        now = timezone.now()
        self.old = UserSecurityLog.objects.create(user=user, event_type="LOGIN_SUCCESS")
        self.recent = UserSecurityLog.objects.create(user=user, event_type="LOGIN_FAILURE")
        UserSecurityLog.objects.filter(pk=self.old.pk).update(timestamp=now - timedelta(days=400))
        UserSecurityLog.objects.filter(pk=self.recent.pk).update(timestamp=now - timedelta(days=300))

    @override_settings(SECURITY_LOG_DAYS=365)
    def test_older_events_go_newer_stay(self):
        self.assertEqual(prune_security_log(), 1)
        self.assertEqual(list(UserSecurityLog.objects.values_list("pk", flat=True)), [self.recent.pk])

    @override_settings(SECURITY_LOG_DAYS=0)
    def test_zero_keeps_everything(self):
        self.assertEqual(prune_security_log(), 0)
        self.assertEqual(UserSecurityLog.objects.count(), 2)
