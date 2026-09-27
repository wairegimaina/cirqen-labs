"""Schedule grouping and history helpers behind the calibration form."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from calSchedules.grouping import next_due_date
from calSchedules.models import CalibrationSchedule
from CalSoft.models import (
    CalibrationParameter, CalibrationProcedure, CalibrationReading, CalibrationSession,
    HistoricalCalibration, SetValue,
)
from CalSoft.view_modules.calibration_helpers import (
    _create_next_schedule, _find_grouped_schedule_for_equipment, _get_grouped_schedule_if_exists,
    _store_historical_data,
)
from Inventory.models import Department, Equipment, EquipmentDescription
from workshop.models import Workshop


class HelperTests(TestCase):
    def setUp(self):
        workshop = Workshop.objects.create(name="Cal", category="calibration_center")
        ward = Department.objects.create(name="ICU", workshop=workshop)
        monitor = EquipmentDescription.objects.create(name="Monitor")
        self.a, self.b = (Equipment.objects.create(description=monitor, model="M", serial_number=f"MON-{n}",
                                                   department=ward, workshop=workshop, status="Working")
                          for n in (1, 2))
        # The scheduling planner schedules new equipment itself; these tests
        # set up their own schedules.
        CalibrationSchedule.objects.all().delete()
        self.user = get_user_model().objects.create_user(username="helper", password="x")
        self.procedure = CalibrationProcedure.objects.create(name="NIBP", created_by=self.user)
        self.month = timezone.localdate().replace(day=1)

    def test_grouped_schedule_is_found_for_the_same_type(self):
        self.assertIsNone(_find_grouped_schedule_for_equipment(self.a))
        theirs = CalibrationSchedule.objects.create(equipment=self.b, scheduled_month=self.month)
        self.assertEqual(_find_grouped_schedule_for_equipment(self.a), theirs)
        mine = CalibrationSchedule.objects.create(equipment=self.a, scheduled_month=self.month)
        self.assertEqual(_get_grouped_schedule_if_exists(mine, self.a), theirs)
        self.assertIsNone(_find_grouped_schedule_for_equipment(None))

    def test_next_schedule_lands_on_the_due_month(self):
        schedule = _create_next_schedule(self.a, self.procedure)
        self.assertEqual(schedule.equipment, self.a)
        expected = next_due_date(timezone.localdate(), 12)
        self.assertEqual((schedule.scheduled_month.year, schedule.scheduled_month.month),
                         (expected.year, expected.month))

    def test_history_is_stored_for_each_reading_with_a_mean(self):
        parameter = CalibrationParameter.objects.create(procedure=self.procedure, name="Systolic", unit="mmHg",
                                                        tolerance=3)
        point = SetValue.objects.create(parameter=parameter, value=120)
        session = CalibrationSession.objects.create(procedure=self.procedure, performed_by=self.user,
                                                    device_serial="MON-1",
                                                    timestamp=timezone.now() - datetime.timedelta(days=1))
        CalibrationReading.objects.create(session=session, parameter=parameter, set_value=point, mean=120.4,
                                          error=0.4, expanded_uncertainty=0.2)
        _store_historical_data(session)
        row = HistoricalCalibration.objects.get(device_serial="MON-1")
        self.assertEqual((row.parameter_name, float(row.measured_value)), ("Systolic", 120.4))
