"""The Smart Reorganizer view must forward special categories + period to the task."""
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class TriggerSmartReorganizeForwardingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="planner", password="pw12345!")
        self.client.force_login(self.user)

    @patch("calSchedules.views.smart_reorganize_on_logic_change")
    @patch("calSchedules.views.get_user_access_context", return_value={"can_schedule": True})
    def test_special_categories_and_period_are_forwarded(self, _access, mock_task):
        mock_task.delay.return_value = MagicMock(id="task-123")

        self.client.post(reverse("schedule:trigger_smart_reorganize"), {
            "new_planning_logic": "date_based",
            "base_month": "3",
            "max_departments": "10",
            "max_descriptions": "10",
            "calibration_period": "6",
            "special_class_departments": ["d1", "d2"],
            "special_class_descriptions": ["x9"],
        })

        self.assertTrue(mock_task.delay.called)
        kwargs = mock_task.delay.call_args.kwargs
        self.assertEqual(kwargs["new_planning_logic"], "date_based")
        self.assertEqual(kwargs["calibration_period"], 6)
        self.assertEqual(kwargs["special_class_departments"], ["d1", "d2"])
        self.assertEqual(kwargs["special_class_descriptions"], ["x9"])

    @patch("calSchedules.views.smart_reorganize_on_logic_change")
    @patch("calSchedules.views.get_user_access_context", return_value={"can_schedule": True})
    def test_bad_period_defaults_to_twelve(self, _access, mock_task):
        mock_task.delay.return_value = MagicMock(id="task-123")
        self.client.post(reverse("schedule:trigger_smart_reorganize"), {
            "new_planning_logic": "description_based",
            "calibration_period": "99",  # invalid → clamps to 12
        })
        self.assertEqual(mock_task.delay.call_args.kwargs["calibration_period"], 12)

    @patch("calSchedules.views.smart_reorganize_on_logic_change")
    @patch("calSchedules.views.get_user_access_context", return_value={"can_schedule": False})
    def test_no_permission_does_not_queue(self, _access, mock_task):
        self.client.post(reverse("schedule:trigger_smart_reorganize"), {
            "new_planning_logic": "date_based",
        })
        self.assertFalse(mock_task.delay.called)
