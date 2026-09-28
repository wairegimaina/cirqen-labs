"""Phase 5: each person chooses which emails they get (the bell always
comes); some reminders cannot be switched off; the HOD can search the outbox."""
from django.test import override_settings
from django.urls import reverse

from CalSoft.models import CalibrationNotification
from notifications import preferences
from notifications.mailer import queue
from notifications.models import EmailOutbox

from .tests import EMAIL, Base

URL = reverse("notifications:my_email")


def _q(kind, key, users, **extra):
    return queue(kind, key, users, "Subject", "simple", {"message": "m", "link": ""}, in_app_message="bell",
                 **extra)


@override_settings(**EMAIL)
class PreferenceTests(Base):
    def _mute(self, user, *keys):
        self.client.force_login(user)
        on = [k for k in preferences.MUTABLE if k not in keys]
        self.client.post(URL, {"on": on})
        user.userprofile.refresh_from_db()

    def test_everyone_can_open_it_and_everything_starts_on(self):
        self.client.force_login(self.tech)
        page = self.client.get(URL)
        self.assertContains(page, "Stock running low")
        self.assertTrue(all(r["on"] for r in page.context["rows"]))

    def test_switching_a_kind_off_stops_the_email_but_not_the_bell(self):
        self._mute(self.tech, "stock")
        self.assertEqual(self.tech.userprofile.email_muted, ["stock"])
        msg = _q("stock_low", "s1", [self.tech, self.deputy])
        self.assertEqual(msg.to, "n_deputy@hospital.test")
        self.assertTrue(CalibrationNotification.objects.filter(recipient=self.tech, notification_type="stock_low")
                        .exists())

    def test_other_kinds_still_come(self):
        self._mute(self.tech, "stock")
        self.assertEqual(_q("accessory_decided", "a1", [self.tech]).to, "n_tech@hospital.test")

    def test_the_hod_is_still_copied_when_every_recipient_switched_it_off(self):
        self._mute(self.tech, "stock")
        msg = _q("stock_low", "s2", [self.tech])
        self.assertEqual((msg.to, msg.cc), ("n_hod@hospital.test", ""))

    def test_a_copy_who_switched_it_off_is_left_out(self):
        self._mute(self.hod, "stock")
        msg = _q("stock_low", "s3", [self.tech])
        self.assertEqual((msg.to, msg.cc), ("n_tech@hospital.test", ""))

    def test_reminders_to_act_cannot_be_switched_off(self):
        self.client.force_login(self.deputy)
        self.client.post(URL, {"on": []})  # everything off
        self.deputy.userprofile.refresh_from_db()
        self.assertNotIn("reminders", self.deputy.userprofile.email_muted)
        self.assertEqual(_q("report_reminder", "r1", [self.deputy], copy_rule=False).to, "n_deputy@hospital.test")

    def test_the_choice_moves_updated_at_so_it_syncs(self):
        before = self.tech.userprofile.updated_at
        self._mute(self.tech, "digest")
        self.assertGreater(self.tech.userprofile.updated_at, before)

    def test_the_outbox_can_be_searched(self):
        _q("stock_low", "s4", [self.tech])
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
