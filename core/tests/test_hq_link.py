"""Tests for the online-first HQ push (:mod:`core.hq_link`).

HQ is mocked throughout; nothing here touches the network. ``build_events``
relies on Postgres ``to_jsonb`` and is not exercised by this SQLite suite.

    ./venv/bin/python manage.py test core --settings=Equiper.test_settings
"""
import json
from unittest import mock

import requests
from django.db import transaction
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core import hq_link
from Inventory.models import Manufacturer

HQ = "https://hq.test/api/sync"
MANUFACTURER_TABLE = "public.Inventory_manufacturer"


def hq_response(status_code=200, **payload):
    response = mock.Mock(status_code=status_code)
    response.json.return_value = payload
    return response


def event(n=0):
    return {"event_id": f"e{n}", "table": MANUFACTURER_TABLE, "row_id": str(n),
            "operation": "u", "data": {"id": n}}


class HQLinkTestMixin:
    def setUp(self):
        super().setUp()
        hq_link._status.update(online=None, checked_at=0.0)
        patcher = mock.patch.object(hq_link, "get_client_id", return_value="device-test")
        self.get_client_id = patcher.start()
        self.addCleanup(patcher.stop)


@override_settings(HQ_SYNC_API_URL=HQ, SYNC_AUTH_TOKEN="token")
class PushEventsTests(HQLinkTestMixin, SimpleTestCase):
    @mock.patch("core.hq_link.requests.post")
    def test_accepted_when_hq_applies_every_event(self, post):
        post.return_value = hq_response(status="success", events_skipped=0)

        self.assertEqual(hq_link.push_events([event()]), (True, ""))
        self.assertEqual(post.call_args.args[0], f"{HQ}/upload")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Client-ID"], "device-test")
        self.assertEqual(headers["X-API-Key"], "token")

    @mock.patch("core.hq_link.requests.post")
    def test_skipped_events_are_a_failure(self, post):
        post.return_value = hq_response(status="success", events_skipped=1)
        ok, error = hq_link.push_events([event()])
        self.assertFalse(ok)
        self.assertIn("1 of 1", error)

    @mock.patch("core.hq_link.requests.post")
    def test_queued_upload_is_not_treated_as_applied(self, post):
        post.return_value = hq_response(status="queued")
        self.assertFalse(hq_link.push_events([event()])[0])

    @mock.patch("core.hq_link.requests.post")
    def test_http_error_is_a_failure(self, post):
        post.return_value = hq_response(status_code=500)
        self.assertFalse(hq_link.push_events([event()])[0])

    @mock.patch("core.hq_link.requests.post", side_effect=requests.ConnectionError("down"))
    def test_unreachable_hq_is_remembered_as_offline(self, post):
        self.assertFalse(hq_link.push_events([event()])[0])
        self.assertTrue(hq_link._known_offline())

    @mock.patch("core.hq_link.requests.post")
    def test_large_pushes_stay_under_hq_queue_threshold(self, post):
        post.return_value = hq_response(status="success", events_skipped=0)
        hq_link.push_events([event(n) for n in range(450)])
        sizes = [len(json.loads(c.kwargs["data"])["events"]) for c in post.call_args_list]
        self.assertEqual(sizes, [200, 200, 50])

    @mock.patch("core.hq_link.requests.post")
    def test_failed_chunk_stops_the_push(self, post):
        post.side_effect = [hq_response(status="success"), hq_response(status_code=502)]
        self.assertFalse(hq_link.push_events([event(n) for n in range(450)])[0])
        self.assertEqual(post.call_count, 2)

    @mock.patch("core.hq_link.requests.post")
    def test_no_client_id_means_no_push(self, post):
        self.get_client_id.return_value = None
        self.assertFalse(hq_link.push_events([event()])[0])
        post.assert_not_called()

    @mock.patch("core.hq_link.requests.post")
    def test_nothing_to_push_succeeds_without_calling_hq(self, post):
        self.assertEqual(hq_link.push_events([]), (True, ""))
        post.assert_not_called()


@override_settings(HQ_SYNC_API_URL=HQ)
class OnlineCheckTests(HQLinkTestMixin, SimpleTestCase):
    @mock.patch("core.hq_link.requests.get")
    def test_answer_is_cached(self, get):
        get.return_value = hq_response()
        self.assertTrue(hq_link.is_hq_online())
        self.assertTrue(hq_link.is_hq_online())
        get.assert_called_once_with(f"{HQ}/health", timeout=hq_link.HEALTH_TIMEOUT)

    @mock.patch("core.hq_link.requests.get")
    def test_force_rechecks(self, get):
        get.return_value = hq_response()
        hq_link.is_hq_online()
        get.return_value = hq_response(status_code=503)
        self.assertFalse(hq_link.is_hq_online(force=True))

    @mock.patch("core.hq_link.requests.get", side_effect=requests.Timeout())
    def test_timeout_counts_as_offline(self, get):
        self.assertFalse(hq_link.is_hq_online())

    @override_settings(HQ_SYNC_API_URL="")
    @mock.patch("core.hq_link.requests.get")
    def test_unconfigured_is_offline(self, get):
        self.assertFalse(hq_link.is_hq_online())
        get.assert_not_called()


@override_settings(HQ_INSTANT_PUSH=True, SYNC_TABLES=[MANUFACTURER_TABLE])
class RequestPushTests(HQLinkTestMixin, TestCase):
    def run_request(self, view):
        middleware = hq_link.HQInstantPushMiddleware(lambda request: view() or HttpResponse())
        with mock.patch.object(hq_link, "_schedule_push") as schedule:
            middleware(RequestFactory().get("/"))
        return schedule

    def create_manufacturer(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.manufacturer = Manufacturer.objects.create(name="Acme")

    def test_committed_saves_are_pushed_after_the_request(self):
        schedule = self.run_request(self.create_manufacturer)
        schedule.assert_called_once_with({MANUFACTURER_TABLE: {str(self.manufacturer.pk)}})

    def test_rolled_back_saves_are_not_pushed(self):
        def view():
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        Manufacturer.objects.create(name="Acme")
                        raise RuntimeError
                except RuntimeError:
                    pass

        self.run_request(view).assert_not_called()

    def test_unsynced_tables_are_ignored(self):
        with override_settings(SYNC_TABLES=[]):
            self.run_request(self.create_manufacturer).assert_not_called()

    def test_suppressed_saves_are_not_collected(self):
        def view():
            with hq_link.suppressed():
                self.create_manufacturer()

        self.run_request(view).assert_not_called()

    @override_settings(HQ_INSTANT_PUSH=False)
    def test_disabled_setting_pushes_nothing(self):
        self.run_request(self.create_manufacturer).assert_not_called()

    def test_saves_outside_a_request_are_ignored(self):
        self.create_manufacturer()
        self.assertEqual(getattr(hq_link._local, "pending", {}), {})
