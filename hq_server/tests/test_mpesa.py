"""M-Pesa payments arriving by themselves: matched only when certain,
counted once, and never from anyone but Safaricom at our secret address."""
from datetime import date
from unittest import mock

import pytest
from fastapi.testclient import TestClient

import billing
import control_store as cs
import mpesa


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.setenv("MPESA_SHORTCODE", "247247")
    monkeypatch.setenv("MPESA_CALLBACK_SECRET", "s3cret-path")
    monkeypatch.delenv("MPESA_ALLOWED_IPS", raising=False)
    monkeypatch.setattr(billing, "today", lambda: date(2026, 10, 1))
    cs.init()
    mpesa.init()
    for code in ("CH0001", "CH0002"):
        cs.save_hospital(code, {"name": code}, create=True)
        billing.save_licence(code, plan="standard", devices=10, starts_on="2025-11-01", ends_on="2026-10-31",
                             grace_days=14, status="active")


def confirmation(ref, amount="120000.00", trans_id="SJK4H7Q2XB", shortcode="247247"):
    return {"TransactionType": "Pay Bill", "TransID": trans_id, "TransTime": "20261001093012",
            "TransAmount": amount, "BusinessShortCode": shortcode, "BillRefNumber": ref,
            "MSISDN": "254712345678", "FirstName": "JANE", "LastName": "WANJIRU"}


def invoice(code="CH0001", amount=120_000):
    return billing.create_invoice(code, months=12, amount_kes=amount, description="", due_days=14, created_by="t")


def test_the_invoice_number_as_account_pays_that_invoice():
    inv = invoice()
    row = mpesa.receive(confirmation(inv["number"].lower()))
    assert row["status"] == "matched"
    assert billing.get_invoice(invoice_id=inv["id"])["status"] == "paid"
    assert billing.get_licence("CH0001")["ends_on"] == "2027-10-31"


def test_the_hospital_code_as_account_pays_its_oldest_open_invoice():
    first, second = invoice(amount=60_000), invoice(amount=60_000)
    mpesa.receive(confirmation("CH0001", amount="60000"))
    assert billing.get_invoice(invoice_id=first["id"])["status"] == "paid"
    assert billing.get_invoice(invoice_id=second["id"])["status"] == "open"


def test_a_retry_is_counted_once():
    invoice()
    mpesa.receive(confirmation("CH0001"))
    mpesa.receive(confirmation("CH0001"))
    assert len(billing.payments("CH0001")) == 1


@pytest.mark.parametrize("ref, why", [
    ("CH00O1", "not a hospital code"),         # typo: letter O
    ("CH0002", "no open invoice"),
    ("CQ-2026-0099", "no invoice"),
])
def test_anything_uncertain_waits_for_a_person(ref, why):
    invoice()
    row = mpesa.receive(confirmation(ref))
    assert row["status"] == "unmatched" and why in row["reason"]
    assert billing.payments() == []


def test_a_payment_to_another_paybill_is_not_counted():
    invoice()
    row = mpesa.receive(confirmation("CH0001", shortcode="999999"))
    assert row["status"] == "unmatched" and "not ours" in row["reason"]


def test_the_phone_number_is_kept_masked():
    row = mpesa.receive(confirmation("CH00O1"))
    assert row["payer"] == "JANE 254****678"
    assert "254712345678" not in row["raw"] and "WANJIRU" not in row["raw"]


def test_finance_assigns_and_sets_aside():
    row = mpesa.receive(confirmation("CH00O1", amount="10000"))
    mpesa.assign(row["trans_id"], "CH0001", None, 1, "books")
    assert mpesa.inbox_row(row["trans_id"])["status"] == "matched"
    assert billing.get_licence("CH0001")["ends_on"] == "2026-11-30"
    other = mpesa.receive(confirmation("?", trans_id="SJK000TEST"))
    mpesa.ignore(other["trans_id"], "sandbox test")
    assert mpesa.inbox_row("SJK000TEST")["status"] == "ignored"


# ── the callback ─────────────────────────────────────────────────────────────

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HQ_PACKAGES_DIR", str(tmp_path / "packages"))
    import main

    return TestClient(main.app)


def test_safaricom_gets_its_accepted_reply(server):
    invoice()
    resp = server.post("/api/pay/mpesa/confirm/s3cret-path", json=confirmation("CH0001"))
    assert resp.json() == {"ResultCode": 0, "ResultDesc": "Accepted"}
    assert billing.get_licence("CH0001")["ends_on"] == "2027-10-31"


def test_the_wrong_secret_does_not_exist(server):
    assert server.post("/api/pay/mpesa/confirm/guess", json=confirmation("CH0001")).status_code == 404
    assert billing.payments() == []


def test_only_safaricoms_addresses_when_listed(server, monkeypatch):
    monkeypatch.setenv("MPESA_ALLOWED_IPS", "196.201.214.200")
    blocked = server.post("/api/pay/mpesa/confirm/s3cret-path", json=confirmation("CH0001"),
                          headers={"x-forwarded-for": "10.0.0.1"})
    allowed = server.post("/api/pay/mpesa/confirm/s3cret-path", json=confirmation("CH0001"),
                          headers={"x-forwarded-for": "8.8.8.8, 196.201.214.200"})
    assert (blocked.status_code, allowed.status_code) == (403, 200)


def test_a_malformed_confirmation_is_still_accepted_and_not_counted(server):
    resp = server.post("/api/pay/mpesa/confirm/s3cret-path", json={"nonsense": True})
    assert resp.json()["ResultCode"] == 0 and billing.payments() == []


def test_registering_our_urls(monkeypatch):
    for k, v in {"MPESA_CONSUMER_KEY": "k", "MPESA_CONSUMER_SECRET": "s",
                 "PUBLIC_BASE_URL": "https://updates.cirqenlabs.com"}.items():
        monkeypatch.setenv(k, v)
    http = mock.Mock()
    http.get.return_value.json.return_value = {"access_token": "tok"}
    http.post.return_value.json.return_value = {"ResponseDescription": "success"}
    assert mpesa.register_urls(http)["ResponseDescription"] == "success"
    body = http.post.call_args.kwargs["json"]
    assert body["ConfirmationURL"] == "https://updates.cirqenlabs.com/api/pay/mpesa/confirm/s3cret-path"
    assert body["ShortCode"] == "247247" and http.post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"
