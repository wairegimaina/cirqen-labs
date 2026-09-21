"""Failures surface — plan item P3.5.

The audit's most costly findings were not wrong formulas but silent ones: a
drift display that printed "No significant drift detected" for every device
because it read keys the engine never returned, and a linearity helper whose
`AttributeError` was swallowed by a bare `except Exception`. Neither raised.
Both looked like working features, and one actively reassured.

Two kinds of test here:

* a **source check** that no exception handler in the module swallows an error
  without recording it, which fixes the class of problem rather than the
  instances;
* **behavioural** tests that the repaired paths report a real failure rather
  than a reassuring default.

    ./venv/bin/python manage.py test CalSoft.test_failure_visibility \
        --settings=Equiper.test_settings
"""

import ast
import pathlib
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

CALSOFT = pathlib.Path(__file__).resolve().parent

# Only BROAD handlers are checked. A narrow `except Equipment.DoesNotExist` or
# `except (ValueError, TypeError)` is a statement that the author anticipated
# that specific condition and chose how to handle it — template filters
# coercing a bad value to "N/A", or an optional lookup falling through. Those
# are correct design and logging them would be noise.
#
# `except Exception`, `except BaseException` and bare `except:` are different:
# they catch everything, including the errors nobody anticipated. That is how a
# drift display comes to print "No significant drift detected" for every device,
# and how a linearity helper reports "unable to calculate" forever without
# anyone noticing. Those must leave a trace.
BROAD = {"Exception", "BaseException"}

# Broad handlers where swallowing is correct. Each needs a reason, so the list
# cannot quietly become a dumping ground.
ALLOWED_SILENT = {}


def source_files():
    for path in sorted(CALSOFT.rglob("*.py")):
        rel = path.relative_to(CALSOFT).as_posix()
        if rel.startswith("migrations/") or rel.startswith("test") or "/test" in rel:
            continue
        yield rel, path


def is_broad(handler):
    """True when the handler catches everything, not a named condition."""
    if handler.type is None:          # bare `except:`
        return True
    names = []
    if isinstance(handler.type, ast.Tuple):
        names = [ast.unparse(e) for e in handler.type.elts]
    else:
        names = [ast.unparse(handler.type)]
    return any(n.split(".")[-1] in BROAD for n in names)


class NoSilentlySwallowedErrorsTests(SimpleTestCase):
    """No broad handler may discard an exception without leaving a trace."""

    @staticmethod
    def _records_the_error(handler):
        """Does this handler log, re-raise, print, or report the failure?"""
        for node in ast.walk(handler):
            if isinstance(node, ast.Raise):
                return True
            if isinstance(node, ast.Call):
                # Unparse the whole call target rather than picking off one
                # attribute: `self.stderr.write` is nested two deep and a
                # single-level read misses it.
                try:
                    name = ast.unparse(node.func)
                except Exception:       # pragma: no cover - malformed node
                    name = ""
                if any(k in name for k in (
                    "logger.", "LOG.", "log.", "print", "record",
                    "stderr", "stdout", "messages.error", "self.fail", "warn",
                )):
                    return True
            if isinstance(node, ast.Name) and node.id in (
                "JsonResponse", "HttpResponseServerError", "HttpResponseForbidden"
            ):
                return True
        return False

    def test_no_broad_handler_discards_an_exception_silently(self):
        offenders = []
        for rel, path in source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler) or not is_broad(node):
                    continue
                if self._records_the_error(node):
                    continue
                if rel in ALLOWED_SILENT:
                    continue
                offenders.append(f"{rel}:{node.lineno}")

        self.assertEqual(
            offenders, [],
            "These broad handlers discard an exception without logging, raising "
            "or reporting it. A swallowed error becomes a feature that quietly "
            "stops working:\n  " + "\n  ".join(offenders),
        )

    def test_no_broad_bare_pass_handlers(self):
        """`except Exception: pass` is the strongest form of the same problem."""
        offenders = []
        for rel, path in source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler) or not is_broad(node):
                    continue
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(offenders, [], "bare pass handlers:\n  " + "\n  ".join(offenders))

    def test_narrow_handlers_are_not_penalised(self):
        """A guard on the rule itself: narrow handlers are exempt by design."""
        tree = ast.parse("try:\n    pass\nexcept ValueError:\n    pass\n")
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
        self.assertFalse(is_broad(handler))

        tree2 = ast.parse("try:\n    pass\nexcept Exception:\n    pass\n")
        handler2 = next(n for n in ast.walk(tree2) if isinstance(n, ast.ExceptHandler))
        self.assertTrue(is_broad(handler2))

    def test_the_allowlist_stays_small(self):
        """A growing allowlist would defeat the check above."""
        self.assertLessEqual(
            len(ALLOWED_SILENT), 3,
            "Silencing a broad exception should be rare. Fix the handler rather "
            "than adding to this list.",
        )


class RepairedPathsReportFailureTests(TestCase):
    """The two features that used to reassure now report reality."""

    def test_drift_reports_nothing_rather_than_reassurance(self):
        """With no history the answer is None, not 'No significant drift'."""
        from django.contrib.auth import get_user_model
        from CalSoft.models import CalibrationProcedure, CalibrationSession
        from CalSoft.view_modules.sessions import _build_drift_analysis

        user = get_user_model().objects.create_user(
            username="fv", password="x", email="fv@example.test"
        )
        procedure = CalibrationProcedure.objects.create(name="FV", created_by=user)
        session = CalibrationSession.objects.create(
            procedure=procedure, performed_by=user,
            device_serial="SN-NOHISTORY", overall_pass=True,
        )

        result = _build_drift_analysis(session)
        self.assertIsNone(result)

    def test_linearity_reports_a_reason_not_a_blanket_message(self):
        """A real data problem names itself instead of 'Unable to calculate'."""
        from CalSoft.view_modules.sessions import _calculate_linearity_analysis
        from types import SimpleNamespace

        # One usable point: too few to fit a line, so the parameter is skipped
        # rather than reported as an unexplained failure.
        reading = SimpleNamespace(
            set_value=SimpleNamespace(value=Decimal("1")), mean=Decimal("1.1")
        )
        result = _calculate_linearity_analysis({"only_one": [reading]})
        self.assertEqual(result, {})

    def test_a_verifier_does_not_claim_validity_it_cannot_establish(self):
        """`valid: True` for any input was the most misleading default of all."""
        from CalSoft.pdf_generators.verification import verify_certificate_qr

        result = verify_certificate_qr("CERT:BNH-0001|DATE:2026-01-01|SERIAL:X")
        self.assertIsNone(result["valid"])
        self.assertFalse(result["verification_available"])
        self.assertIn("not signed", result["reason"])

    def test_a_forged_payload_is_not_reported_as_valid(self):
        from CalSoft.pdf_generators.verification import verify_certificate_qr

        result = verify_certificate_qr("CERT:TOTALLY-MADE-UP|DATE:1999-01-01|SERIAL:NOPE")
        self.assertIsNot(result["valid"], True)
