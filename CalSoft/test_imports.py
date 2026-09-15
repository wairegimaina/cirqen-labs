"""Tests for the standards and parameters Excel imports (:mod:`CalSoft.view_modules.imports`)."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from CalSoft.models import (
    CalibrationParameter, CalibrationProcedure, Parameter, Standard, StandardParameter,
)
from core.tests.excel_helpers import ExcelImportTestMixin, read_response_workbook
from Inventory.models import Department
from workshop.models import Workshop

PARAMETER_HEADER = ["Name", "Symbol", "Unit"]
STANDARD_HEADER = [
    "Name", "Model Number", "Serial Number", "Manufacturer",
    "Certificate Number", "Calibration Date", "Due Date", "Calibration Agency",
]
LINK_HEADER = ["Standard Serial Number", "Parameter", "Unit", "Symbol", "Uncertainty (k=2)"]

CAL = datetime.date(2026, 1, 10)
DUE = datetime.date(2027, 1, 10)


def standard_row(serial, name="Pressure Calibrator", cal=CAL, due=DUE, certificate="C-1"):
    return [name, "PC-100", serial, "Fluke", certificate, cal, due, "KEBS"]


class CalibrationImportTestBase(ExcelImportTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.workshop = Workshop.objects.create(name="Cal Lab", category="calibration_center")
        self.tech = self.make_user("tech", "Tech", workshop=self.workshop, level="Engineer")
        self.pressure = Parameter.objects.create(name="Pressure", symbol="P", unit="mmHg", created_by=self.tech)
        self.client.force_login(self.tech)

    def upload_parameters(self, rows, **kwargs):
        return self.post_workbook(
            reverse("calibration:upload_parameters_excel"), {"Parameters": [PARAMETER_HEADER, *rows]}, **kwargs
        )

    def upload_standards(self, standards, links, **kwargs):
        return self.post_workbook(
            reverse("calibration:upload_standards_excel"),
            {"Standards": [STANDARD_HEADER, *standards], "Standard Parameters": [LINK_HEADER, *links]},
            **kwargs,
        )


class PermissionTests(CalibrationImportTestBase):
    def test_in_charge_is_forbidden(self):
        ward = Department.objects.create(name="Ward 1", workshop=self.workshop)
        self.client.force_login(self.make_user("nic", "NIC", department=ward))
        for name in ("standards_import_template", "parameters_import_template"):
            self.assertEqual(self.client.get(reverse(f"calibration:{name}")).status_code, 403)
        resp = self.upload_parameters([["Mass", "m", "kg"]], commit=True)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Parameter.objects.filter(name="Mass").exists())

    def test_create_standard_page_offers_uploads_to_tech_but_not_in_charge(self):
        url = reverse("calibration:standard_create")
        page = self.client.get(url)
        # The uploads sit beside the manual standard and parameter forms.
        self.assertContains(page, 'id="standardForm"')
        self.assertContains(page, 'id="parameterForm"')
        self.assertContains(page, 'data-bs-target="#uploadStandardsModal"')
        self.assertContains(page, 'data-bs-target="#uploadParametersModal"')
        self.assertContains(page, reverse("calibration:upload_standards_excel"))
        self.assertContains(page, reverse("calibration:upload_parameters_excel"))

        list_page = self.client.get(reverse("calibration:StandardsParameters_lists"))
        self.assertNotContains(list_page, "uploadStandardsModal")

        ward = Department.objects.create(name="Ward 2", workshop=self.workshop)
        self.client.force_login(self.make_user("nic2", "NIC", department=ward))
        page = self.client.get(url)
        self.assertContains(page, 'id="standardForm"')
        self.assertNotContains(page, "uploadStandardsModal")

    def test_hod_may_import(self):
        self.client.force_login(self.make_user("hod", "HOD"))
        self.assertEqual(self.upload_parameters([["Mass", "m", "kg"]]).status_code, 200)


class ParameterImportTests(CalibrationImportTestBase):
    def test_template(self):
        resp = self.client.get(reverse("calibration:parameters_import_template"))
        self.assertEqual(resp.status_code, 200)
        wb = read_response_workbook(resp)
        self.assertEqual([cell.value for cell in wb["Parameters"][1]], PARAMETER_HEADER)
        self.assertIn("Pressure", [cell.value for cell in wb["Reference"]["A"]])

    def test_commit_creates_skips_and_reports_errors(self):
        data = self.upload_parameters([
            ["Temperature", "T", "°C"],  # row 2: new
            ["pressure", "P", "mmHg"],   # row 3: exists
            ["Voltage", "V", "mV"],      # row 4: new
            ["Voltage", "V", "MV"],      # row 5: new - units are case-sensitive
            ["Current", "I", ""],        # row 6: no unit
            ["temperature", "T", "°C"],  # row 7: duplicate of row 2
        ], commit=True).json()

        self.assertEqual(data["counts"], {"create": 3, "skip": 1, "error": 2})
        rows = self.rows_by_number(data)
        self.assertEqual(rows[3]["action"], "skip")
        self.assertIn("Unit is required.", rows[6]["messages"])
        self.assertIn("duplicated in this file (also on row 2)", rows[7]["messages"][0])
        self.assertEqual(Parameter.objects.filter(name="Voltage").count(), 2)
        self.assertEqual(Parameter.objects.get(unit="°C").created_by, self.tech)
        self.assertEqual(len(self.pushed()), 3)

    def test_preview_saves_nothing(self):
        self.upload_parameters([["Mass", "m", "kg"]])
        self.assertFalse(Parameter.objects.filter(name="Mass").exists())


class StandardTemplateTests(CalibrationImportTestBase):
    def test_template_has_linked_sheets(self):
        resp = self.client.get(reverse("calibration:standards_import_template"))
        self.assertEqual(resp.status_code, 200)
        wb = read_response_workbook(resp)
        self.assertEqual(wb.sheetnames, ["Standards", "Standard Parameters", "Reference", "Instructions"])
        self.assertEqual([cell.value for cell in wb["Standards"][1]], STANDARD_HEADER)
        self.assertEqual([cell.value for cell in wb["Standard Parameters"][1]], LINK_HEADER)


class StandardImportTests(CalibrationImportTestBase):
    def test_standards_saved_with_parameters_and_pushed_parents_first(self):
        data = self.upload_standards(
            [standard_row("SN1"), standard_row("SN2", name="Thermometer")],
            [
                ["SN1", "pressure", "mmHg", "", 0.5],
                ["SN2", "Temperature", "°C", "T", "0.2"],
            ],
            commit=True,
        ).json()

        self.assertEqual(data["counts"], {"create": 2, "skip": 0, "error": 0})
        self.assertEqual(data["created"], {"Parameters": ["Temperature (°C)"]})
        self.assertIn("Pressure (mmHg) ±0.5", data["rows"][0]["messages"][1])

        sn1 = Standard.objects.get(serial_number="SN1")
        self.assertEqual((sn1.calibration_date, sn1.calibration_due_date), (CAL, DUE))
        self.assertEqual(sn1.created_by, self.tech)
        link = StandardParameter.objects.get(standard=sn1)
        self.assertEqual((link.parameter, link.uncertainty), (self.pressure, Decimal("0.5")))
        self.assertTrue(StandardParameter.objects.filter(
            standard__serial_number="SN2", parameter__name="Temperature", parameter__unit="°C"
        ).exists())

        # SN1 was saved before SN2's new parameter, but HQ must get parents first.
        self.assertEqual(
            [type(obj).__name__ for obj in self.pushed()],
            ["Parameter", "Standard", "Standard", "StandardParameter", "StandardParameter"],
        )

    def test_preview_saves_nothing(self):
        data = self.upload_standards([standard_row("SN1")], [["SN1", "Temperature", "°C", "", 0.2]]).json()
        self.assertEqual(data["counts"]["create"], 1)
        self.assertFalse(Standard.objects.exists())
        self.assertFalse(Parameter.objects.filter(name="Temperature").exists())

    def test_text_dates_are_day_first_and_certificate_generated_when_blank(self):
        self.upload_standards(
            [standard_row("SN1", cal="05/03/2026", due="05/03/2027", certificate="")],
            [["SN1", "Pressure", "mmHg", "", 1]],
            commit=True,
        )
        standard = Standard.objects.get(serial_number="SN1")
        self.assertEqual(standard.calibration_date, datetime.date(2026, 3, 5))
        self.assertEqual(standard.certificate_number, "STD-SN1-20260305")

    def test_standard_not_saved_when_any_parameter_row_is_invalid(self):
        data = self.upload_standards(
            [standard_row("SN1")],
            [["SN1", "Temperature", "°C", "", 0.2], ["SN1", "Pressure", "mmHg", "", -1]],
            commit=True,
        ).json()
        self.assertEqual(data["counts"], {"create": 0, "skip": 0, "error": 1})
        self.assertIn("Standard Parameters row 3: Uncertainty must be positive.", data["rows"][0]["messages"])
        self.assertFalse(Standard.objects.exists())
        self.assertFalse(Parameter.objects.filter(name="Temperature").exists())

    def test_standard_without_parameters_rejected(self):
        data = self.upload_standards([standard_row("SN1")], []).json()
        self.assertIn("No parameters are listed", data["rows"][0]["messages"][0])

    def test_missing_parameters_are_errors_when_not_creating_them(self):
        data = self.upload_standards(
            [standard_row("SN1")], [["SN1", "Temperature", "°C", "", 0.2]],
            commit=True, create_missing="false",
        ).json()
        self.assertIn("'Temperature (°C)' does not exist", data["rows"][0]["messages"][0])
        self.assertFalse(Standard.objects.exists())

    def test_row_validation(self):
        Standard.objects.create(
            name="Old", model_number="M", serial_number="SN-OLD", manufacturer="X",
            certificate_number="C", calibration_date=CAL, calibration_due_date=DUE, created_by=self.tech,
        )
        data = self.upload_standards(
            [
                standard_row("SN1", due=CAL),             # row 2: due not after cal
                standard_row("sn-old"),                   # row 3: exists
                standard_row("SN3", cal="someday"),       # row 4: bad date
                standard_row("SN4"),                      # row 5: ok
                standard_row("sn4"),                      # row 6: duplicate
            ],
            [
                ["SN1", "Pressure", "mmHg", "", 1],
                ["SN3", "Pressure", "mmHg", "", 1],
                ["SN4", "Pressure", "mmHg", "", 1],
                ["SN4", "PRESSURE", "mmHg", "", 2],       # row 5: listed twice
                ["SN9", "Pressure", "mmHg", "", 1],       # row 6: no such standard
            ],
        ).json()

        standards = {row["row"]: row for row in data["rows"] if row["sheet"] == "Standards"}
        links = {row["row"]: row for row in data["rows"] if row["sheet"] == "Standard Parameters"}
        self.assertIn("Due Date must be after Calibration Date.", standards[2]["messages"])
        self.assertEqual(standards[3]["action"], "skip")
        self.assertIn("Calibration Date: 'someday' is not a date", standards[4]["messages"][0])
        self.assertIn("listed twice", standards[5]["messages"][0])
        self.assertIn("duplicated in this file (also on row 5)", standards[6]["messages"][0])
        self.assertIn("'SN9' is not on the 'Standards' sheet", links[6]["messages"][0])

    def test_missing_parameters_sheet_rejected(self):
        resp = self.post_workbook(
            reverse("calibration:upload_standards_excel"), {"Standards": [STANDARD_HEADER, standard_row("SN1")]}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("'Standard Parameters' sheet", resp.json()["error"])


class StandardUncertaintyTests(CalibrationImportTestBase):
    """Imported uncertainties must be exactly what the rest of CalSoft reads back."""

    def test_same_parameter_name_twice_on_one_standard_rejected(self):
        data = self.upload_standards(
            [standard_row("SN1")],
            [["SN1", "Voltage", "mV", "", 0.01], ["SN1", "voltage", "V", "", 0.001]],
            commit=True,
        ).json()
        self.assertEqual(data["counts"], {"create": 0, "skip": 0, "error": 1})
        self.assertIn("listed twice for this standard", data["rows"][0]["messages"][0])
        self.assertFalse(Standard.objects.exists())

    def test_same_parameter_on_different_standards_is_fine(self):
        data = self.upload_standards(
            [standard_row("SN1"), standard_row("SN2")],
            [["SN1", "Pressure", "mmHg", "", 0.5], ["SN2", "Pressure", "mmHg", "", 0.3]],
            commit=True,
        ).json()
        self.assertEqual(data["counts"]["create"], 2)
        self.assertEqual(
            {sp.standard.serial_number: sp.uncertainty for sp in StandardParameter.objects.all()},
            {"SN1": Decimal("0.5"), "SN2": Decimal("0.3")},
        )

    def test_unit_differing_only_by_case_is_flagged(self):
        data = self.upload_standards([standard_row("SN1")], [["SN1", "Pressure", "mmhg", "", 0.5]]).json()
        self.assertEqual(data["rows"][0]["action"], "create")
        self.assertIn("'Pressure' already exists in: mmHg", data["rows"][0]["messages"][-1])

    def test_imported_uncertainty_is_what_procedures_read(self):
        self.upload_standards([standard_row("SN1")], [["SN1", "Pressure", "mmHg", "", "0.25"]], commit=True)
        standard = Standard.objects.get(serial_number="SN1")

        # Auto-fill of reference uncertainty in the procedure form.
        auto_fill = self.client.get(
            reverse("calibration:api_standard_parameters"),
            {"standard_id": standard.id, "parameter_name": "Pressure"},
        ).json()
        self.assertEqual(Decimal(auto_fill["uncertainty"]), Decimal("0.25"))
        self.assertEqual(auto_fill["standard_serial"], "SN1")

        # Parameters shown in the standard's edit dialog.
        detail = self.client.get(
            reverse("calibration:api_standard_parameters_detail", args=[standard.id])
        ).json()
        self.assertEqual(
            [(p["parameter_name"], Decimal(p["uncertainty"])) for p in detail["parameters"]],
            [("Pressure", Decimal("0.25"))],
        )

        # Uncertainty lookup for a procedure that uses this standard.
        procedure = CalibrationProcedure.objects.create(name="NIBP", created_by=self.tech)
        CalibrationParameter.objects.create(
            procedure=procedure, name="Pressure", unit="mmHg", standard_reference="SN1", tolerance=Decimal("3"),
        )
        [parameter] = procedure.get_parameters_with_standards()
        self.assertEqual(parameter["standard"], standard)
        self.assertEqual(parameter["standard_uncertainty"], Decimal("0.25"))
