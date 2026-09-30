"""Money in, licence out: invoices, manual payments and reversals."""
from datetime import date

import pytest

import billing
import control_store as cs


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.setattr(billing, "today", lambda: date(2026, 10, 1))
    cs.init()
    cs.save_hospital("CH0001", {"name": "Pilot"}, create=True)
    billing.save_licence("CH0001", plan="standard", devices=10, starts_on="2025-10-01", ends_on="2026-10-31",
                         grace_days=14, status="active")


def pay(amount, ref, invoice=None, months=0):
    return billing.record_payment("CH0001", amount_kes=amount, method="mpesa", reference=ref, paid_on="2026-10-01",
                                  invoice_id=invoice, months=months, note="", recorded_by="finance")


def invoice(amount=120_000, months=12):
    return billing.create_invoice("CH0001", months=months, amount_kes=amount, description="", due_days=14,
                                  created_by="owner")


def test_invoice_numbers_run_in_order_and_are_never_reused():
    first, second = invoice(), invoice()
    billing.void_invoice(second["id"])
    assert [first["number"], second["number"], invoice()["number"]] == ["CQ-2026-0001", "CQ-2026-0002",
                                                                        "CQ-2026-0003"]


def test_paying_an_invoice_in_full_extends_the_licence():
    inv = invoice()
    result = pay(120_000, "SJK4H7Q2XB", inv["id"])
    assert result["invoice"]["status"] == "paid" and result["extended_months"] == 12
    lic = billing.get_licence("CH0001")
    assert lic["ends_on"] == "2027-10-31" and lic["licence_version"] == 2


def test_a_part_payment_extends_nothing_until_the_rest_arrives():
    inv = invoice()
    assert pay(50_000, "REF1", inv["id"])["extended_months"] == 0
    assert billing.get_licence("CH0001")["ends_on"] == "2026-10-31"
    assert pay(70_000, "REF2", inv["id"])["extended_months"] == 12


def test_a_lapsed_licence_is_extended_from_today():
    billing.save_licence("CH0001", plan="standard", devices=10, starts_on="2025-01-01", ends_on="2026-03-31",
                         grace_days=14, status="active")
    pay(10_000, "REF1", months=1)
    assert billing.get_licence("CH0001")["ends_on"] == "2026-11-01"


def test_the_same_reference_cannot_be_counted_twice():
    pay(10_000, "sjk4h7q2xb", months=1)
    with pytest.raises(billing.BillingError, match="already recorded"):
        pay(10_000, "SJK4H7Q2XB", months=1)


def test_reversing_takes_the_months_back_and_keeps_both_entries():
    inv = invoice()
    paid = pay(120_000, "REF1", inv["id"])["payment"]
    billing.reverse_payment(paid["id"], reason="bounced", recorded_by="owner")
    assert billing.get_invoice(invoice_id=inv["id"])["status"] == "open"
    assert billing.get_licence("CH0001")["ends_on"] == "2026-10-31"
    assert [p["amount_kes"] for p in billing.payments("CH0001")] == [-120_000, 120_000]
    with pytest.raises(billing.BillingError, match="already reversed"):
        billing.reverse_payment(paid["id"], reason="again", recorded_by="owner")


def test_reversing_an_earlier_part_payment_reopens_the_invoice():
    inv = invoice()
    first = pay(50_000, "REF1", inv["id"])["payment"]
    pay(70_000, "REF2", inv["id"])
    billing.reverse_payment(first["id"], reason="bounced", recorded_by="owner")
    assert billing.get_invoice(invoice_id=inv["id"])["status"] == "open"
    assert billing.get_licence("CH0001")["ends_on"] == "2026-10-31"


def test_month_report():
    pay(10_000, "A", months=1)
    second = pay(20_000, "B", months=2)["payment"]
    billing.reverse_payment(second["id"], reason="wrong hospital", recorded_by="owner")
    report = billing.month_report(2026, 10)
    assert report["rows"][0]["received"] == 30_000 and report["rows"][0]["reversed"] == 20_000
    assert report["total"] == 10_000


def test_month_arithmetic():
    assert billing.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert billing.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
