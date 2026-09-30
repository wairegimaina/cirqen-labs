"""Admin panel: licences, invoices and manual payments (billing.py).

Owner and finance record invoices and payments. Setting a licence by hand
and reversing a payment move paid time, so they also ask for the
authenticator code. Support can look. Invoices and receipts print from the
browser.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

import admin_auth as auth
import billing
import control_store as cs
from admin_panel import _check_csrf, _current, _ip, _page, guarded, router

MONEY = ("owner", "finance")


def _int(value, default=0):
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def licence_state(lic: dict | None, today: date | None = None) -> str:
    """What the hospital's PCs show (same rule as the desktop's licence.py)."""
    if lic is None:
        return "none"
    today = today or billing.today()
    if lic["status"] == "suspended":
        return "read-only (suspended)"
    ends = date.fromisoformat(lic["ends_on"])
    if today <= ends:
        return "renewal due" if (ends - today).days < 30 else "active"
    if (today - ends).days <= lic["grace_days"]:
        return "grace"
    return "read-only"


def context(h: dict) -> dict:
    """Billing parts of the hospital page."""
    lic = billing.get_licence(h["code"])
    return {"licence": lic, "licence_state": licence_state(lic), "invoices": billing.invoices(h["code"]),
            "payments": billing.payments(h["code"]), "methods": billing.METHODS, "today": billing.today().isoformat()}


def _back(request, admin, session, h, message, status=400):
    return _page(request, "error.html", {"admin": admin, "session": session, "message": message,
                                         "back": f"/admin/hospitals/{h['code']}#billing"}, status)


def _hospital(code):
    return cs.get_hospital(code.upper())


@router.post("/hospitals/{code}/licence")
@guarded
async def licence_save(request: Request, code: str):
    session, admin = _current(request, MONEY)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    h = _hospital(code)
    if h is None:
        return RedirectResponse("/admin/", status_code=303)
    if not auth.verify_totp(admin, form.get("code", "")):
        cs.audit(admin["username"], "licence_denied_bad_code", h["code"], ip=_ip(request))
        return _back(request, admin, session, h, "The authenticator code was not accepted.", 403)
    before = billing.get_licence(h["code"])
    try:
        lic = billing.save_licence(h["code"], plan=form.get("plan", "standard"), devices=_int(form.get("devices"), 0),
                                   starts_on=form.get("starts_on", ""), ends_on=form.get("ends_on", ""),
                                   grace_days=_int(form.get("grace_days"), -1), status=form.get("status", "active"))
    except billing.BillingError as exc:
        return _back(request, admin, session, h, str(exc))
    fields = ("plan", "devices", "starts_on", "ends_on", "grace_days", "status")
    changed = {k: {"from": (before or {}).get(k), "to": lic[k]} for k in fields if (before or {}).get(k) != lic[k]}
    cs.audit(admin["username"], "licence_set", h["code"], changed, _ip(request))
    return RedirectResponse(f"/admin/hospitals/{h['code']}#billing", status_code=303)


@router.post("/hospitals/{code}/invoices")
@guarded
async def invoice_create(request: Request, code: str):
    session, admin = _current(request, MONEY)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    h = _hospital(code)
    if h is None:
        return RedirectResponse("/admin/", status_code=303)
    try:
        inv = billing.create_invoice(h["code"], months=_int(form.get("months")), amount_kes=_int(form.get("amount_kes")),
                                     description=form.get("description", ""), due_days=_int(form.get("due_days"), 14),
                                     created_by=admin["username"])
    except billing.BillingError as exc:
        return _back(request, admin, session, h, str(exc))
    cs.audit(admin["username"], "invoice_created", h["code"],
             {"number": inv["number"], "amount_kes": inv["amount_kes"], "months": inv["months"]}, _ip(request))
    return RedirectResponse(f"/admin/invoices/{inv['number']}", status_code=303)


@router.post("/invoices/{number}/void")
@guarded
async def invoice_void(request: Request, number: str):
    session, admin = _current(request, MONEY)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    inv = billing.get_invoice(number=number)
    if inv is None:
        return RedirectResponse("/admin/", status_code=303)
    h = _hospital(inv["hospital"])
    try:
        billing.void_invoice(inv["id"])
    except billing.BillingError as exc:
        return _back(request, admin, session, h, str(exc))
    cs.audit(admin["username"], "invoice_voided", inv["hospital"], {"number": number}, _ip(request))
    return RedirectResponse(f"/admin/invoices/{number}", status_code=303)


@router.post("/hospitals/{code}/payments")
@guarded
async def payment_record(request: Request, code: str):
    session, admin = _current(request, MONEY)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    h = _hospital(code)
    if h is None:
        return RedirectResponse("/admin/", status_code=303)
    try:
        result = billing.record_payment(
            h["code"], amount_kes=_int(form.get("amount_kes")), method=form.get("method", ""),
            reference=form.get("reference", ""), paid_on=form.get("paid_on", ""),
            invoice_id=_int(form.get("invoice_id")) or None, months=_int(form.get("months")),
            note=form.get("note", ""), recorded_by=admin["username"])
    except billing.BillingError as exc:
        return _back(request, admin, session, h, str(exc))
    p = result["payment"]
    cs.audit(admin["username"], "payment_recorded", h["code"],
             {"amount_kes": p["amount_kes"], "method": p["method"], "reference": p["reference"],
              "invoice": (result["invoice"] or {}).get("number"), "extended_months": result["extended_months"],
              "ends_on": (result["licence"] or {}).get("ends_on")}, _ip(request))
    return RedirectResponse(f"/admin/payments/{p['id']}", status_code=303)


@router.post("/payments/{payment_id}/reverse")
@guarded
async def payment_reverse(request: Request, payment_id: int):
    session, admin = _current(request, ("owner",))
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    original = billing.payment(payment_id)
    if original is None:
        return RedirectResponse("/admin/", status_code=303)
    h = _hospital(original["hospital"])
    if not auth.verify_totp(admin, form.get("code", "")):
        return _back(request, admin, session, h, "The authenticator code was not accepted.", 403)
    try:
        rev = billing.reverse_payment(payment_id, reason=form.get("reason", ""), recorded_by=admin["username"])
    except billing.BillingError as exc:
        return _back(request, admin, session, h, str(exc))
    cs.audit(admin["username"], "payment_reversed", original["hospital"],
             {"payment": payment_id, "reference": original["reference"], "reason": rev["note"],
              "months_back": -rev["months_credited"]}, _ip(request))
    return RedirectResponse(f"/admin/hospitals/{original['hospital']}#billing", status_code=303)


@router.get("/invoices/{number}", response_class=HTMLResponse)
@guarded
def invoice_page(request: Request, number: str):
    session, admin = _current(request)
    inv = billing.get_invoice(number=number)
    if inv is None:
        return _page(request, "error.html", {"admin": admin, "session": session, "message": "No such invoice."}, 404)
    return _page(request, "invoice.html", {"admin": admin, "session": session, "inv": inv,
                                           "h": _hospital(inv["hospital"]),
                                           "paybill": _setting("PAYBILL_NUMBER"), "bank": _setting("BANK_DETAILS")})


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
@guarded
def receipt_page(request: Request, payment_id: int):
    session, admin = _current(request)
    p = billing.payment(payment_id)
    if p is None:
        return _page(request, "error.html", {"admin": admin, "session": session, "message": "No such payment."}, 404)
    return _page(request, "receipt.html", {
        "admin": admin, "session": session, "p": p, "h": _hospital(p["hospital"]),
        "inv": billing.get_invoice(invoice_id=p["invoice_id"]) if p["invoice_id"] else None,
        "licence": billing.get_licence(p["hospital"])})


@router.get("/money", response_class=HTMLResponse)
@guarded
def money_page(request: Request, month: str = ""):
    session, admin = _current(request, MONEY)
    try:
        year, mon = (int(x) for x in month.split("-")) if month else (billing.today().year, billing.today().month)
        date(year, mon, 1)
    except ValueError:
        year, mon = billing.today().year, billing.today().month
    return _page(request, "money.html", {
        "admin": admin, "session": session, "month": f"{year:04d}-{mon:02d}", "report": billing.month_report(year, mon),
        "open_invoices": [i for i in billing.invoices() if i["status"] == "open"],
        "payments": billing.payments()[:100], "now": datetime.now().isoformat()})


def _setting(name: str) -> str:
    import os

    return os.getenv(name, "").strip()
