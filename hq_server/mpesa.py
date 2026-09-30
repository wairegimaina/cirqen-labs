"""M-Pesa Paybill payments arriving by themselves (Safaricom Daraja C2B).

Safaricom posts each payment to our confirmation URL. Every confirmation is
first kept in the inbox exactly once (by its M-Pesa receipt number, TransID),
then matched:

  Account (BillRefNumber) = an open invoice number (CQ-2026-0001)
      → paid against that invoice
  Account = a hospital code (CH0001) with one or more open invoices
      → paid against its oldest open invoice
  anything else (typo, no open invoice, wrong Paybill)
      → stays "unmatched" for finance to assign on the M-Pesa page

A matched payment goes through billing.record_payment like a manual one, so
a payment that completes an invoice extends the licence and is audited.
Nothing is guessed.

Protection: the callback URLs carry a secret path (MPESA_CALLBACK_SECRET),
optionally only Safaricom's addresses are accepted (MPESA_ALLOWED_IPS), the
Paybill must be ours (MPESA_SHORTCODE), and a receipt number is recorded
once however often Safaricom retries. The phone number is stored masked.

Environment: MPESA_ENV (sandbox|production), MPESA_CONSUMER_KEY,
MPESA_CONSUMER_SECRET, MPESA_SHORTCODE, MPESA_CALLBACK_SECRET,
MPESA_ALLOWED_IPS (comma-separated, optional), PUBLIC_BASE_URL (e.g.
https://updates.cirqenlabs.com, for registering the URLs).
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
from datetime import datetime

import billing
import control_store as cs

HOSTS = {"sandbox": "https://sandbox.safaricom.co.ke", "production": "https://api.safaricom.co.ke"}
INVOICE = re.compile(r"^CQ-\d{4}-\d{4,}$")

INBOX_SQL = """
CREATE TABLE IF NOT EXISTS mpesa_inbox (
    trans_id     TEXT PRIMARY KEY,
    amount_kes   INTEGER NOT NULL,
    bill_ref     TEXT NOT NULL DEFAULT '',
    shortcode    TEXT NOT NULL DEFAULT '',
    payer        TEXT NOT NULL DEFAULT '',
    trans_time   TEXT NOT NULL DEFAULT '',
    received_at  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'unmatched' CHECK (status IN ('unmatched', 'matched', 'ignored')),
    reason       TEXT NOT NULL DEFAULT '',
    payment_id   INTEGER REFERENCES payments(id),
    raw          TEXT NOT NULL
)
"""


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def init() -> None:
    cs.conn().execute(INBOX_SQL)


# ── callbacks ────────────────────────────────────────────────────────────────

def secret_ok(presented: str) -> bool:
    secret = env("MPESA_CALLBACK_SECRET")
    return bool(secret) and hmac.compare_digest(presented, secret)


def ip_ok(ip: str) -> bool:
    allowed = {a.strip() for a in env("MPESA_ALLOWED_IPS").split(",") if a.strip()}
    return not allowed or ip in allowed


def _mask(msisdn: str) -> str:
    digits = re.sub(r"\D", "", str(msisdn or ""))
    return f"{digits[:3]}****{digits[-3:]}" if len(digits) >= 9 else ""


def _paid_on(trans_time: str) -> str:
    try:
        return datetime.strptime(str(trans_time), "%Y%m%d%H%M%S").date().isoformat()
    except ValueError:
        return billing.today().isoformat()


def receive(payload: dict) -> dict:
    """Store one confirmation (idempotent) and try to match it.
    Returns the inbox row. Never raises on bad input: Safaricom must get its
    'accepted' reply, and a bad payment waits for a person."""
    trans_id = str(payload.get("TransID") or "").strip().upper()
    if not trans_id:
        raise ValueError("no TransID")
    existing = inbox_row(trans_id)
    if existing:
        return existing                          # Safaricom retried: already counted
    try:
        amount = int(round(float(payload.get("TransAmount") or 0)))
    except (TypeError, ValueError):
        amount = 0
    names = " ".join(str(payload.get(k) or "").strip() for k in ("FirstName", "MiddleName", "LastName")).strip()
    payer = " ".join(x for x in (names.split(" ")[0] if names else "", _mask(payload.get("MSISDN"))) if x)
    kept = {k: v for k, v in payload.items() if k not in ("MSISDN", "FirstName", "MiddleName", "LastName")}
    cs.conn().execute(
        "INSERT OR IGNORE INTO mpesa_inbox (trans_id, amount_kes, bill_ref, shortcode, payer, trans_time, "
        "received_at, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (trans_id, amount, str(payload.get("BillRefNumber") or "").strip().upper(),
         str(payload.get("BusinessShortCode") or "").strip(), payer, str(payload.get("TransTime") or ""),
         cs.now(), json.dumps(kept, sort_keys=True)),
    )
    row = inbox_row(trans_id)
    return auto_match(row)


def inbox_row(trans_id: str) -> dict | None:
    row = cs.conn().execute("SELECT * FROM mpesa_inbox WHERE trans_id = ?", (trans_id,)).fetchone()
    return dict(row) if row else None


def inbox(status: str | None = None) -> list[dict]:
    if status:
        rows = cs.conn().execute("SELECT * FROM mpesa_inbox WHERE status = ? ORDER BY received_at DESC", (status,))
    else:
        rows = cs.conn().execute("SELECT * FROM mpesa_inbox ORDER BY received_at DESC LIMIT 200")
    return [dict(r) for r in rows]


def _set(trans_id: str, **fields) -> dict:
    sets = ", ".join(f"{k} = ?" for k in fields)
    cs.conn().execute(f"UPDATE mpesa_inbox SET {sets} WHERE trans_id = ?", (*fields.values(), trans_id))
    return inbox_row(trans_id)


def _target(bill_ref: str) -> tuple[str | None, dict | None, str]:
    """(hospital, invoice, reason-if-none) for an account reference."""
    if INVOICE.match(bill_ref):
        inv = billing.get_invoice(number=bill_ref)
        if inv is None:
            return None, None, f"no invoice {bill_ref}"
        if inv["status"] != "open":
            return None, None, f"invoice {bill_ref} is {inv['status']}"
        return inv["hospital"], inv, ""
    hospital = cs.get_hospital(bill_ref) if bill_ref else None
    if hospital is None:
        return None, None, f"account {bill_ref or '(blank)'} is not a hospital code or invoice"
    open_invoices = [i for i in reversed(billing.invoices(hospital["code"])) if i["status"] == "open"]
    if not open_invoices:
        return None, None, f"{hospital['code']} has no open invoice; assign the months by hand"
    return hospital["code"], open_invoices[0], ""


def auto_match(row: dict) -> dict:
    if row["status"] != "unmatched":
        return row
    ours = env("MPESA_SHORTCODE")
    if ours and row["shortcode"] and row["shortcode"] != ours:
        return _set(row["trans_id"], reason=f"paid to Paybill {row['shortcode']}, not ours ({ours})")
    if row["amount_kes"] <= 0:
        return _set(row["trans_id"], reason="no amount")
    hospital, inv, why = _target(row["bill_ref"])
    if hospital is None:
        return _set(row["trans_id"], reason=why)
    return _apply(row, hospital, inv["id"] if inv else None, 0, "M-Pesa (automatic)")


def _apply(row: dict, hospital: str, invoice_id: int | None, months: int, by: str) -> dict:
    try:
        result = billing.record_payment(
            hospital, amount_kes=row["amount_kes"], method="mpesa", reference=row["trans_id"],
            paid_on=_paid_on(row["trans_time"]), invoice_id=invoice_id, months=months,
            note=f"Paybill account {row['bill_ref']}; {row['payer']}".strip("; "), recorded_by=by)
    except billing.BillingError as exc:
        return _set(row["trans_id"], reason=str(exc))
    p = result["payment"]
    cs.audit(by, "payment_recorded", hospital,
             {"amount_kes": p["amount_kes"], "method": "mpesa", "reference": p["reference"],
              "invoice": (result["invoice"] or {}).get("number"), "extended_months": result["extended_months"],
              "ends_on": (result["licence"] or {}).get("ends_on")})
    return _set(row["trans_id"], status="matched", reason="", payment_id=p["id"])


def assign(trans_id: str, hospital: str, invoice_id: int | None, months: int, by: str) -> dict:
    """Finance assigns an unmatched payment by hand."""
    row = inbox_row(trans_id)
    if row is None or row["status"] != "unmatched":
        raise billing.BillingError("that payment is not waiting to be assigned")
    if cs.get_hospital(hospital) is None:
        raise billing.BillingError("no such hospital")
    result = _apply(row, hospital, invoice_id, months, by)
    if result["status"] != "matched":
        raise billing.BillingError(result["reason"])
    return result


def ignore(trans_id: str, reason: str) -> dict:
    row = inbox_row(trans_id)
    if row is None or row["status"] != "unmatched" or not reason.strip():
        raise billing.BillingError("say why it is set aside")
    return _set(trans_id, status="ignored", reason=reason.strip())


# ── Daraja: register our URLs with Safaricom ─────────────────────────────────

def configured() -> dict:
    return {k: bool(env(k)) for k in ("MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_SHORTCODE",
                                       "MPESA_CALLBACK_SECRET", "PUBLIC_BASE_URL")}


def callback_urls() -> dict:
    base = env("PUBLIC_BASE_URL").rstrip("/")
    secret = env("MPESA_CALLBACK_SECRET")
    return {"ConfirmationURL": f"{base}/api/pay/mpesa/confirm/{secret}",
            "ValidationURL": f"{base}/api/pay/mpesa/validate/{secret}"}


def register_urls(http=None) -> dict:
    """Tell Daraja where to send payments (once per Paybill, and after moving
    the server). Returns Daraja's answer."""
    import httpx

    if not all(configured().values()):
        raise billing.BillingError("M-Pesa is not fully configured on this server")
    host = HOSTS.get(env("MPESA_ENV", "sandbox"), HOSTS["sandbox"])
    client = http or httpx.Client(timeout=30)
    basic = base64.b64encode(f"{env('MPESA_CONSUMER_KEY')}:{env('MPESA_CONSUMER_SECRET')}".encode()).decode()
    token = client.get(f"{host}/oauth/v1/generate?grant_type=client_credentials",
                       headers={"Authorization": f"Basic {basic}"}).json()["access_token"]
    resp = client.post(f"{host}/mpesa/c2b/v1/registerurl", headers={"Authorization": f"Bearer {token}"},
                       json={"ShortCode": env("MPESA_SHORTCODE"), "ResponseType": "Completed", **callback_urls()})
    return resp.json()
