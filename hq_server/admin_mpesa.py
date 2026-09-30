"""Admin panel: M-Pesa inbox (mpesa.py). Owner and finance assign or set
aside payments that could not be matched; the owner registers our URLs with
Safaricom (with the authenticator code)."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

import admin_auth as auth
import billing
import control_store as cs
import mpesa
from admin_panel import _check_csrf, _current, _ip, _page, guarded, router

MONEY = ("owner", "finance")


@router.get("/mpesa", response_class=HTMLResponse)
@guarded
def mpesa_page(request: Request, note: str = ""):
    session, admin = _current(request, MONEY)
    hospitals = cs.list_hospitals()
    return _page(request, "mpesa.html", {
        "admin": admin, "session": session, "unmatched": mpesa.inbox("unmatched"), "recent": mpesa.inbox(),
        "hospitals": hospitals, "open_invoices": [i for i in billing.invoices() if i["status"] == "open"],
        "config": mpesa.configured(), "env": mpesa.env("MPESA_ENV", "sandbox"),
        "urls": mpesa.callback_urls() if mpesa.env("PUBLIC_BASE_URL") else {}, "note": note})


@router.post("/mpesa/{trans_id}/assign")
@guarded
async def mpesa_assign(request: Request, trans_id: str):
    session, admin = _current(request, MONEY)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    invoice_id = int(form.get("invoice_id") or 0) or None
    hospital = (form.get("hospital") or "").upper()
    if invoice_id:
        inv = billing.get_invoice(invoice_id=invoice_id)
        hospital = inv["hospital"] if inv else hospital
    try:
        months = int(form.get("months") or 0)
        mpesa.assign(trans_id, hospital, invoice_id, months, admin["username"])
    except (ValueError, billing.BillingError) as exc:
        return RedirectResponse(f"/admin/mpesa?note=Not assigned: {exc}", status_code=303)
    return RedirectResponse(f"/admin/mpesa?note={trans_id} assigned to {hospital}", status_code=303)


@router.post("/mpesa/{trans_id}/ignore")
@guarded
async def mpesa_ignore(request: Request, trans_id: str):
    session, admin = _current(request, MONEY)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    try:
        mpesa.ignore(trans_id, form.get("reason", ""))
    except billing.BillingError as exc:
        return RedirectResponse(f"/admin/mpesa?note={exc}", status_code=303)
    cs.audit(admin["username"], "mpesa_set_aside", trans_id, {"reason": form.get("reason")}, _ip(request))
    return RedirectResponse("/admin/mpesa", status_code=303)


@router.post("/mpesa/register")
@guarded
async def mpesa_register(request: Request):
    session, admin = _current(request, ("owner",))
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    if not auth.verify_totp(admin, form.get("code", "")):
        return RedirectResponse("/admin/mpesa?note=The authenticator code was not accepted.", status_code=303)
    try:
        answer = mpesa.register_urls()
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/admin/mpesa?note=Registration failed: {str(exc)[:160]}", status_code=303)
    cs.audit(admin["username"], "mpesa_urls_registered", mpesa.env("MPESA_SHORTCODE"), answer, _ip(request))
    return RedirectResponse(f"/admin/mpesa?note=Safaricom answered: {answer.get('ResponseDescription', answer)}",
                            status_code=303)
