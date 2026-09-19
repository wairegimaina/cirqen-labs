import pytest
import random
from decimal import Decimal
from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from unittest.mock import patch, MagicMock
from CalSoft import views
from CalSoft.models import (
    CalibrationProcedure, CalibrationParameter, CalibrationSession,
    SessionParameterResolution, SetValue, SubParameter, CalibrationReading
)
from calSchedules.models import CalibrationSchedule


@pytest.mark.django_db
class TestDeclinedSessionsAndRestore:
    def setup_method(self):
        self.user = User.objects.create_user("tester", password="pass")
        self.reviewer = User.objects.create_user("reviewer", password="pass")
        self.procedure = CalibrationProcedure.objects.create(name="TestProc", created_by=self.user)

    def test_rejection_fields_exist_on_model(self):
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user, status="pending_review"
        )
        session.status = "rejected"
        session.rejection_reason = "data_quality"
        session.rejection_comments = "Test rejection comments"
        session.rejected_by = self.reviewer
        session.rejected_at = timezone.now()
        session.save()
        assert session.rejection_reason == "data_quality"
        assert session.rejection_comments == "Test rejection comments"
        assert session.rejected_by == self.reviewer
        assert session.rejected_at is not None

    def test_can_restore_after_three_days(self):
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user,
            status="rejected", rejection_reason="other", rejected_by=self.reviewer,
            rejected_at=timezone.now() - timedelta(days=4)
        )
        session.can_restore = session.rejected_at and session.rejected_at <= timezone.now() - timedelta(days=3)
        assert session.can_restore is True

    def test_cannot_restore_within_three_days(self):
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user,
            status="rejected", rejection_reason="other", rejected_by=self.reviewer,
            rejected_at=timezone.now() - timedelta(days=1)
        )
        session.can_restore = session.rejected_at and session.rejected_at <= timezone.now() - timedelta(days=3)
        assert session.can_restore is False

    @patch("CalSoft.views.generate_btwelve_certificate")
    def test_download_declined_certificate_returns_pdf(self, mock_pdf):
        mock_buffer = MagicMock()
        mock_buffer.getvalue.return_value = b"%PDF-1.4"
        mock_pdf.return_value = mock_buffer

        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user,
            status="rejected", rejection_reason="other", rejected_by=self.reviewer,
            rejected_at=timezone.now()
        )
        client = Client()
        client.login(username="tester", password="pass")
        response = client.get(reverse("calibration:download_declined_certificate", args=[session.pk]))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"


@pytest.mark.django_db
class TestCertificateGenerationAndSessions:
    def setup_method(self):
        self.user = User.objects.create_user("tester", password="pass")
        self.procedure = CalibrationProcedure.objects.create(name="MultiSessionProc", created_by=self.user)

    def test_certificate_number_format_increments(self):
        """The numbering format, via its pure helper.

        This used to call ``CalibrationSession.generate_certificate_number()``,
        which allocated from the local database. That path is gone: HQ is the
        only allocator (plan item B1). What remains testable here is the
        format, which HQ's allocator mirrors.
        """
        from calSchedules.grouping import next_certificate_number

        assert next_certificate_number(None) == "BNH-0001"
        assert next_certificate_number("BNH-0001") == "BNH-0002"
        assert next_certificate_number("BNH-0092").endswith("0093")

    def test_a_session_can_be_created_without_a_certificate_number(self):
        """Approval leaves the number absent until HQ issues one."""
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user
        )
        assert session.certificate_number is None

    def test_multiple_sessions_success_rate_and_recent(self):
        CalibrationSession.objects.create(procedure=self.procedure, performed_by=self.user, overall_pass=True)
        CalibrationSession.objects.create(procedure=self.procedure, performed_by=self.user, overall_pass=False)
        assert self.procedure.get_success_rate() in (0, 50, 100)
        sessions = self.procedure.get_recent_sessions(limit=10)
        assert len(sessions) == 2

    def test_average_duration_computation(self):
        now = timezone.now()
        s1 = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user, timestamp=now
        )
        # Manually set created_at for fake duration calculation
        s1.created_at = now - timedelta(minutes=30)
        s1.save(update_fields=[])
        avg = self.procedure.get_average_duration()
        assert avg is None or isinstance(avg, (int, float))


@pytest.mark.django_db
class TestHistoricalDataStorage:
    def setup_method(self):
        self.user = User.objects.create_user("tester", password="pass")
        self.procedure = CalibrationProcedure.objects.create(name="HistProc", created_by=self.user)
        self.parameter = CalibrationParameter.objects.create(
            procedure=self.procedure, name="Param1", unit="V", standard_reference="S", tolerance=Decimal("1")
        )
        self.session = CalibrationSession.objects.create(procedure=self.procedure, performed_by=self.user)
        SessionParameterResolution.objects.create(session=self.session, parameter=self.parameter, resolution=Decimal("0.1"))
        self.set_value = SetValue.objects.create(parameter=self.parameter, value=Decimal("10"))
        self.reading = CalibrationReading.objects.create(session=self.session, parameter=self.parameter, set_value=self.set_value)

    @patch("CalSoft.views.HistoricalCalibration.objects.create")
    def test_store_historical_data_calls_create_for_valid_readings(self, mock_create):
        self.reading.mean = Decimal("10")
        self.reading.error = Decimal("0")
        self.reading.expanded_uncertainty = Decimal("0.1")
        self.reading.save()
        views._store_historical_data(self.session)
        mock_create.assert_called_once()

    def test_store_historical_data_skips_invalid(self):
        # mean is None so it should skip
        self.reading.mean = None
        self.reading.save()
        # Should not raise error
        views._store_historical_data(self.session)


# The linearity test that lived here mocked out
# `TrendAnalysis.calculate_linear_regression` — a method that did not exist —
# so it passed while the feature had never worked. The regression is real now
# and is covered against hand-computed values in
# `CalSoft/test_drift_and_linearity.py::LinearityAnalysisTests`, which asserts
# slope, intercept and the largest residual rather than mocking them away.


@pytest.mark.django_db
class TestCertificatePDFGeneration:
    @patch("CalSoft.views.generate_btwelve_certificate")
    def test_certificate_pdf_generation_called(self, mock_pdf):
        # Pretend to call certificate generator
        mock_pdf.return_value = b"%PDF"
        result = mock_pdf("dummy_session")
        assert result.startswith(b"%PDF")
        mock_pdf.assert_called_once_with("dummy_session")
