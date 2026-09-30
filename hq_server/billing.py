"""Licences, invoices and manual payments (M-Pesa, bank, cash).

Money is whole Kenya shillings. The payment ledger is append-only: a
mistake is corrected with a reversing entry (negative amount, pointing at
the payment it reverses), never by editing or deleting. Invoice numbers
(CQ-2026-0001) are given out in order and never reused; an invoice is voided,
not deleted.

A payment against an invoice that brings what has been paid on it to the
invoiced amount marks it paid and extends the hospital's licence by the
invoice's months, from its end date or from today if it has already
lapsed. A payment without an invoice extends it by the months given.
Reversing a payment takes those months back off. Every change to a
licence raises licence_version, so its PCs replace their copy.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta, timezone

import control_store as cs

EAT = timezone(timedelta(hours=3))
METHODS = ("mpesa", "bank", "cash", "other")
CONTEXT = b"cirqen-licence-v1\n"


class BillingError(Exception):
    pass


def today() -> date:
    return datetime.now(EAT).date()


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


# ── licences ─────────────────────────────────────────────────────────────────

def get_licence(hospital: str) -> dict | None:
    row = cs.conn().execute("SELECT * FROM licences WHERE hospital = ?", (hospital,)).fetchone()
    return dict(row) if row else None


def save_licence(hospital: str, *, plan: str, devices: int, starts_on: str, ends_on: str, grace_days: int,
                 status: str) -> dict:
    if status not in ("active", "suspended"):
        raise BillingError("status must be active or suspended")
    if devices < 1 or not 0 <= grace_days <= 60:
        raise BillingError("devices at least 1; grace 0-60 days")
    try:
        if date.fromisoformat(ends_on) < date.fromisoformat(starts_on):
            raise BillingError("the licence cannot end before it starts")
    except ValueError as exc:
        raise BillingError("dates must be YYYY-MM-DD") from exc
    current = get_licence(hospital)
    version = (current["licence_version"] + 1) if current else 1
    cs.conn().execute(
        "INSERT INTO licences (hospital, plan, devices, starts_on, ends_on, grace_days, status, licence_version, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (hospital) DO UPDATE SET plan = excluded.plan, "
        "devices = excluded.devices, starts_on = excluded.starts_on, ends_on = excluded.ends_on, "
        "grace_days = excluded.grace_days, status = excluded.status, "
        "licence_version = excluded.licence_version, updated_at = excluded.updated_at",
        (hospital, plan.strip() or "standard", devices, starts_on, ends_on, grace_days, status, version, cs.now()),
    )
    return get_licence(hospital)


def _extend(hospital: str, months: int) -> dict | None:
    """Move the end date by ``months`` (negative to take back). Extending a
    lapsed licence counts from today, so the hospital gets what it paid for."""
    lic = get_licence(hospital)
    if lic is None or months == 0:
        return lic
    ends = date.fromisoformat(lic["ends_on"])
    base = max(ends, today()) if months > 0 else ends
    new_end = add_months(base, months)
    return save_licence(hospital, plan=lic["plan"], devices=lic["devices"], starts_on=lic["starts_on"],
                        ends_on=new_end.isoformat(), grace_days=lic["grace_days"], status=lic["status"])


def licence_document(lic: dict) -> str:
    return json.dumps({
        "type": "cirqen-licence", "v": 1, "hospital": lic["hospital"], "plan": lic["plan"],
        "devices": lic["devices"], "starts_on": lic["starts_on"], "ends_on": lic["ends_on"],
        "grace_days": lic["grace_days"], "status": lic["status"], "licence_version": lic["licence_version"],
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }, sort_keys=True, separators=(",", ":"))


def signed_licence(lic: dict, signing_key) -> dict:
    import base64

    raw = licence_document(lic)
    return {"document": raw, "signature": base64.b64encode(signing_key.sign(CONTEXT + raw.encode())).decode()}


# ── invoices ─────────────────────────────────────────────────────────────────

def create_invoice(hospital: str, *, months: int, amount_kes: int, description: str, due_days: int,
                   created_by: str) -> dict:
    if not 1 <= months <= 36 or amount_kes <= 0 or not 0 <= due_days <= 90:
        raise BillingError("months 1-36, amount above 0, due within 0-90 days")
    issued = today()
    prefix = f"CQ-{issued.year}-"
    c = cs.conn()
    c.execute("BEGIN IMMEDIATE")   # one number at a time, even across processes
    try:
        last = c.execute("SELECT number FROM invoices WHERE number LIKE ? ORDER BY number DESC LIMIT 1",
                         (prefix + "%",)).fetchone()
        number = f"{prefix}{(int(last[0].rsplit('-', 1)[1]) if last else 0) + 1:04d}"
        c.execute(
            "INSERT INTO invoices (number, hospital, issued_on, due_on, months, amount_kes, description, "
            "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (number, hospital, issued.isoformat(), (issued + timedelta(days=due_days)).isoformat(), months,
             amount_kes, description.strip() or f"Cirqen licence, {months} month(s)", created_by, cs.now()),
        )
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    return get_invoice(number=number)


def get_invoice(invoice_id: int | None = None, number: str | None = None) -> dict | None:
    if invoice_id is not None:
        row = cs.conn().execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    else:
        row = cs.conn().execute("SELECT * FROM invoices WHERE number = ?", (number,)).fetchone()
    if row is None:
        return None
    inv = dict(row)
    inv["paid_kes"] = cs.conn().execute(
        "SELECT COALESCE(SUM(amount_kes), 0) FROM payments WHERE invoice_id = ?", (inv["id"],)).fetchone()[0]
    return inv


def invoices(hospital: str | None = None) -> list[dict]:
    rows = (cs.conn().execute("SELECT id FROM invoices WHERE hospital = ? ORDER BY id DESC", (hospital,))
            if hospital else cs.conn().execute("SELECT id FROM invoices ORDER BY id DESC"))
    return [get_invoice(invoice_id=r[0]) for r in rows]


def void_invoice(invoice_id: int) -> dict:
    inv = get_invoice(invoice_id=invoice_id)
    if inv is None or inv["status"] != "open" or inv["paid_kes"]:
        raise BillingError("only an open invoice with nothing paid on it can be voided")
    cs.conn().execute("UPDATE invoices SET status = 'void' WHERE id = ?", (invoice_id,))
    return get_invoice(invoice_id=invoice_id)


# ── payments ─────────────────────────────────────────────────────────────────

def record_payment(hospital: str, *, amount_kes: int, method: str, reference: str, paid_on: str,
                   invoice_id: int | None, months: int, note: str, recorded_by: str) -> dict:
    """Record money received. Returns {"payment", "invoice", "licence", "extended_months"}."""
    reference = reference.strip().upper()
    if amount_kes <= 0 or method not in METHODS or not reference:
        raise BillingError("amount above 0, a method, and the M-Pesa or bank reference")
    try:
        date.fromisoformat(paid_on)
    except ValueError as exc:
        raise BillingError("payment date must be YYYY-MM-DD") from exc
    inv = None
    if invoice_id:
        inv = get_invoice(invoice_id=invoice_id)
        if inv is None or inv["hospital"] != hospital or inv["status"] == "void":
            raise BillingError("that invoice is not an open invoice of this hospital")
        if inv["status"] == "paid":
            raise BillingError("that invoice is already paid")
    elif not 0 <= months <= 36:
        raise BillingError("months 0-36")
    if cs.conn().execute("SELECT 1 FROM payments WHERE method = ? AND reference = ? AND reverses IS NULL",
                         (method, reference)).fetchone():
        raise BillingError(f"{method} reference {reference} is already recorded")

    extend = 0
    if inv:
        if inv["paid_kes"] + amount_kes >= inv["amount_kes"]:
            extend = inv["months"]
    else:
        extend = months
    cur = cs.conn().execute(
        "INSERT INTO payments (hospital, invoice_id, amount_kes, method, reference, paid_on, months_credited, "
        "note, recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (hospital, inv["id"] if inv else None, amount_kes, method, reference, paid_on, extend, note.strip(),
         recorded_by, cs.now()),
    )
    if inv and extend:
        cs.conn().execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (inv["id"],))
    licence = _extend(hospital, extend)
    return {"payment": payment(cur.lastrowid), "invoice": get_invoice(invoice_id=inv["id"]) if inv else None,
            "licence": licence, "extended_months": extend}


def payment(payment_id: int) -> dict | None:
    row = cs.conn().execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    return dict(row) if row else None


def payments(hospital: str | None = None) -> list[dict]:
    rows = (cs.conn().execute("SELECT * FROM payments WHERE hospital = ? ORDER BY id DESC", (hospital,))
            if hospital else cs.conn().execute("SELECT * FROM payments ORDER BY id DESC"))
    return [dict(r) for r in rows]


def reverse_payment(payment_id: int, *, reason: str, recorded_by: str) -> dict:
    """A reversing entry for a payment recorded by mistake (or bounced)."""
    original = payment(payment_id)
    if original is None or original["reverses"] is not None:
        raise BillingError("no such payment")
    if cs.conn().execute("SELECT 1 FROM payments WHERE reverses = ?", (payment_id,)).fetchone():
        raise BillingError("that payment is already reversed")
    if not reason.strip():
        raise BillingError("say why it is reversed")
    # Months to take back: what this payment credited, or, when it was an
    # earlier part-payment of an invoice another payment completed, the
    # invoice's months (the invoice is no longer fully paid).
    months_back = original["months_credited"]
    inv = get_invoice(invoice_id=original["invoice_id"]) if original["invoice_id"] else None
    reopen = bool(inv and inv["status"] == "paid" and inv["paid_kes"] - original["amount_kes"] < inv["amount_kes"])
    if reopen and months_back == 0:
        months_back = inv["months"]
    cur = cs.conn().execute(
        "INSERT INTO payments (hospital, invoice_id, amount_kes, method, reference, paid_on, months_credited, "
        "reverses, note, recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (original["hospital"], original["invoice_id"], -original["amount_kes"], original["method"],
         original["reference"], today().isoformat(), -months_back, payment_id, reason.strip(),
         recorded_by, cs.now()),
    )
    if reopen:
        cs.conn().execute("UPDATE invoices SET status = 'open' WHERE id = ?", (inv["id"],))
    _extend(original["hospital"], -months_back)
    return payment(cur.lastrowid)


def month_report(year: int, month: int) -> dict:
    """Received, reversed and net per hospital for one calendar month (paid_on)."""
    start = date(year, month, 1).isoformat()
    end = add_months(date(year, month, 1), 1).isoformat()
    rows = cs.conn().execute(
        "SELECT hospital, SUM(CASE WHEN amount_kes > 0 THEN amount_kes ELSE 0 END) AS received, "
        "SUM(CASE WHEN amount_kes < 0 THEN -amount_kes ELSE 0 END) AS reversed, SUM(amount_kes) AS net, "
        "COUNT(*) AS entries FROM payments WHERE paid_on >= ? AND paid_on < ? GROUP BY hospital ORDER BY hospital",
        (start, end),
    )
    per = [dict(r) for r in rows]
    return {"rows": per, "total": sum(r["net"] for r in per)}
