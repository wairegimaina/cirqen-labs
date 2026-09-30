"""The HOD can search the outbox; the bell list clears read notifications."""
from django.test import override_settings
from django.urls import reverse

from notifications.mailer import queue

from .tests import EMAIL, Base


@override_settings(**EMAIL)
class OutboxSearchTests(Base):
    def test_the_outbox_can_be_searched(self):
        queue("stock_low", "s4", [self.tech], "Subject", "simple", {"message": "m", "link": ""})
        queue("accessory_requested", "a2", [self.hod], "Flow Sensor requested", "simple",
              {"message": "m", "link": ""})
        self.client.force_login(self.hod)
        page = self.client.get(reverse("notifications:outbox"), {"q": "flow sensor"})
        self.assertEqual([m.subject for m in page.context["page"].object_list], ["Flow Sensor requested"])


class BellListTests(Base):
    """Read notifications are cleared from the bell list, for that user only."""

    def test_read_ones_leave_the_list_and_mark_all_empties_it(self):
        from CalSoft.models import CalibrationNotification

        for title in ("One", "Two", "Three"):
            CalibrationNotification.objects.create(recipient=self.tech, notification_type="t", title=title,
                                                   message="m")
        other = CalibrationNotification.objects.create(recipient=self.nic, notification_type="t", title="NIC's",
                                                       message="m")
        self.client.force_login(self.tech)
        data = self.client.get("/calibration/api/notifications/").json()
        self.assertEqual((data["unread_count"], len(data["notifications"])), (3, 3))

        first = data["notifications"][0]["id"]
        self.client.post(f"/calibration/api/notifications/{first}/read/")
        data = self.client.get("/calibration/api/notifications/").json()
        self.assertEqual(len(data["notifications"]), 2)
        self.assertNotIn(first, [n["id"] for n in data["notifications"]])

        self.client.post("/calibration/api/notifications/read-all/")
        data = self.client.get("/calibration/api/notifications/").json()
        self.assertEqual((data["unread_count"], data["notifications"]), (0, []))

        other.refresh_from_db()
        self.assertFalse(other.is_read)  # another user's notifications are untouched
