"""The app must keep working when Redis is down (IMPROVEMENT_PLAN.md 5.1).

Points both Redis caches at a port nothing listens on, using the same
fail-soft options as production settings, and checks that a user can still
log in and load a page, and that the startup check reports the outage.
"""
import logging

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase, override_settings
from django.urls import reverse

from core import cache_health
from users.models import UserProfile

User = get_user_model()

DEAD_REDIS = "redis://127.0.0.1:1"  # port 1: connection refused immediately


def _production_redis_cache(alias, db):
    """The production cache definition (fail-soft options included), re-pointed at a dead port."""
    from Equiper import settings as production

    conf = dict(production.CACHES[alias])
    conf["LOCATION"] = f"{DEAD_REDIS}/{db}"
    return conf


@override_settings(
    CACHES={
        "default": _production_redis_cache("default", 0),
        "sessions": _production_redis_cache("sessions", 1),
        "offline": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "throttle": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    },
    SESSION_ENGINE="django.contrib.sessions.backends.cached_db",
    SESSION_CACHE_ALIAS="sessions",
)
class RedisDownTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nredis", password="pw12345!")
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"role": "HOD", "must_change_password": False,
                      "has_uploaded_signature": True},
        )

    def test_cache_calls_degrade_to_misses(self):
        cache = caches["default"]
        cache.set("k", "v")
        self.assertIsNone(cache.get("k"))

    def test_login_and_page_load_without_redis(self):
        self.assertTrue(self.client.login(username="nredis", password="pw12345!"))
        response = self.client.get(reverse("dashboard:hod_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_startup_check_reports_outage(self):
        logging.disable(logging.NOTSET)  # test settings silence all logging
        self.addCleanup(logging.disable, logging.CRITICAL)
        with self.assertLogs("cirqen.cache", level="WARNING"):
            status = cache_health.log_cache_status()
        self.assertEqual(status, {"default": False, "sessions": False})
