"""The admin panel: /admin on Cirqen Control (admin.cirqenlabs.com).

Hospitals, their HQ addresses and identity, admin accounts and the audit
log. It shows each HQ's health (counts and status, never records) and never
holds hospital data. Sign-in and sessions: admin_auth.py.

Roles: owner (everything), support (hospitals and their addresses),
finance (read-only here; payments arrive in a later phase). Issuing an HQ
identity and changing admin accounts ask for the authenticator code again.

ADMIN_HOSTS (comma-separated, e.g. admin.cirqenlabs.com) limits the panel to
those host names; everywhere else /admin answers 404. Unset: any host
(local development).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

import admin_auth as auth
import control_store as cs
import endpoints as fleet_endpoints

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates" / "admin"))

CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,31}$")
PREFIX = re.compile(r"^[A-Z0-9]{2,8}-$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CAN_EDIT = ("owner", "support")
HEALTH_TTL = 60
_health_cache: dict[str, tuple[float, dict]] = {}
# Versions the update server has built, newest first (set by install()).
_versions = lambda: []  # noqa: E731


def admin_hosts() -> set[str]:
    return {h.strip().lower() for h in os.getenv("ADMIN_HOSTS", "").split(",") if h.strip()}


def secure_cookies() -> bool:
    return os.getenv("ADMIN_INSECURE_COOKIES", "").lower() != "true"


def _ip(request: Request) -> str:
    # The last X-Forwarded-For entry is the one Render's proxy added; earlier
    # entries come from the client and can be anything.
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[-1].strip() if forwarded else "") or (request.client.host if request.client else "")


class Denied(Exception):
    def __init__(self, response):
        self.response = response


def _current(request: Request, roles=None):
    found = auth.session_for(request.cookies.get(auth.SESSION_COOKIE))
    if found is None:
        raise Denied(RedirectResponse("/admin/login", status_code=303))
    session, admin = found
    if roles and admin["role"] not in roles:
        raise Denied(_page(request, "error.html", {"admin": admin, "session": session,
                                                    "message": "Your role cannot do this."}, 403))
    return session, admin


def _check_csrf(session: dict, token: str):
    import hmac

    if not token or not hmac.compare_digest(token, session["csrf"]):
        raise Denied(Response("Form expired; go back and reload the page.", status_code=403))


def _page(request: Request, name: str, context: dict, status: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status)


def guarded(fn):
    """Turn Denied into its response (FastAPI handlers stay plain functions)."""
    import functools
    import inspect

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Denied as denied:
                return denied.response
    else:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Denied as denied:
                return denied.response
    return wrapper


# ── validation ───────────────────────────────────────────────────────────────

def _sync_url(value: str, label: str, errors: list) -> str:
    value = (value or "").strip().rstrip("/")
    if value and (not value.startswith("https://") or not value.endswith("/api/sync")):
        errors.append(f"{label} must be an https address ending in /api/sync")
    return value


def clean_hospital(form: dict, *, creating: bool) -> tuple[dict, list[str]]:
    errors: list[str] = []
    values = {
        "name": (form.get("name") or "").strip(),
        "county": (form.get("county") or "").strip(),
        "hod_name": (form.get("hod_name") or "").strip(),
        "hod_email": (form.get("hod_email") or "").strip(),
        "hod_phone": (form.get("hod_phone") or "").strip(),
        "notes": (form.get("notes") or "").strip(),
        "cert_prefix": (form.get("cert_prefix") or "").strip().upper(),
        "status": (form.get("status") or "active").strip(),
    }
    if creating:
        values["code"] = (form.get("code") or "").strip().upper()
        if not CODE.match(values["code"]):
            errors.append("Code: letters and digits, e.g. CH0002")
    if not values["name"]:
        errors.append("Name is required")
    if values["hod_email"] and not EMAIL.match(values["hod_email"]):
        errors.append("HOD e-mail is not an e-mail address")
    if values["cert_prefix"] and not PREFIX.match(values["cert_prefix"]):
        errors.append("Certificate prefix: 2-8 letters or digits and a dash, e.g. KRH-")
    if values["status"] not in ("active", "suspended", "closed"):
        errors.append("Status must be active, suspended or closed")
    values["sync_url"] = _sync_url(form.get("sync_url"), "HQ sync address", errors)
    values["fallbacks"] = [_sync_url(line, "Fallback address", errors)
                           for line in (form.get("fallbacks") or "").splitlines() if line.strip()]
    updates = (form.get("updates_url") or "").strip().rstrip("/")
    if updates and (not updates.startswith("https://") or "/api/" in updates):
        errors.append("Update server must be an https server address (no /api/ path)")
    values["updates_url"] = updates
    return values, errors


# ── HQ health (status and counts only) ───────────────────────────────────────

async def fetch_health(hospital: dict) -> dict:
    url = hospital.get("sync_url")
    if not url:
        return {"state": "no address"}
    cached = _health_cache.get(url)
    if cached and time.time() - cached[0] < HEALTH_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(f"{url}/health")
        checks = (resp.json() or {}).get("checks", {}) if resp.status_code == 200 else {}
        result = {
            "state": "up" if resp.status_code == 200 and checks.get("db") == "UP" else f"HTTP {resp.status_code}",
            "hospital": checks.get("hospital"),
            "identity": checks.get("identity"),
            "prefix": checks.get("certificate_prefix"),
            "sse": (checks.get("sse_clients") or {}).get("active") if isinstance(checks.get("sse_clients"), dict)
            else None,
        }
        if result["hospital"] and result["hospital"] != hospital["code"]:
            result["state"] = f"answers as {result['hospital']}"
    except Exception as exc:  # noqa: BLE001
        result = {"state": "unreachable", "error": str(exc)[:120]}
    _health_cache[url] = (time.time(), result)
    return result


# ── sign in ──────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if _setup_open():
        return RedirectResponse("/admin/setup", status_code=303)
    return _page(request, "login.html", {"error": "", "first_run": auth.count_admins() == 0})


@router.post("/login")
def login_submit(request: Request, username: str = Form(""), password: str = Form(""), code: str = Form("")):
    try:
        admin = auth.login(username, password, code, _ip(request))
    except auth.AuthError as exc:
        return _page(request, "login.html", {"error": str(exc), "first_run": False}, 401)
    token = auth.start_session(admin, _ip(request))
    resp = RedirectResponse("/admin/", status_code=303)
    resp.set_cookie(auth.SESSION_COOKIE, token, httponly=True, secure=secure_cookies(), samesite="strict",
                    path="/admin", max_age=auth.MAX_SECONDS)
    return resp


@router.post("/logout")
@guarded
def logout(request: Request, csrf: str = Form("")):
    session, admin = _current(request)
    _check_csrf(session, csrf)
    auth.end_session(request.cookies.get(auth.SESSION_COOKIE))
    cs.audit(admin["username"], "logout", ip=_ip(request))
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE, path="/admin")
    return resp


# ── first owner, without a server shell ──────────────────────────────────────

def _setup_open() -> bool:
    """Only while no admin exists, and only if ADMIN_SETUP_TOKEN is set."""
    return bool(os.getenv("ADMIN_SETUP_TOKEN", "").strip()) and auth.count_admins() == 0


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if not _setup_open():
        return Response("Not Found", status_code=404)
    return _page(request, "setup.html", {"error": "", "created": None})


@router.post("/setup", response_class=HTMLResponse)
def setup_submit(request: Request, token: str = Form(""), username: str = Form(""), password: str = Form(""),
                 password2: str = Form("")):
    """Create the first owner with ADMIN_SETUP_TOKEN (set in Render's
    Environment tab; no shell needed). Gone once any admin exists."""
    import hmac
    import time as _time

    if not _setup_open():
        return Response("Not Found", status_code=404)
    ip = _ip(request)
    if auth._recent_failures("ip", ip) >= auth.MAX_FAILS_PER_USER:
        return _page(request, "setup.html", {"error": "Too many failed attempts. Try again in 15 minutes.",
                                             "created": None}, 429)
    if not hmac.compare_digest(token.strip(), os.getenv("ADMIN_SETUP_TOKEN", "").strip()):
        cs.conn().execute("INSERT INTO login_attempts (username, ip, at, ok) VALUES (?, ?, ?, 0)",
                          ("(setup)", ip, _time.time()))
        cs.audit("(setup)", "setup_failed", ip=ip)
        return _page(request, "setup.html", {"error": "The setup token is wrong.", "created": None}, 401)
    if password != password2:
        return _page(request, "setup.html", {"error": "The passwords differ.", "created": None}, 400)
    try:
        secret = auth.create_admin(username, password, "owner")
    except auth.AuthError as exc:
        return _page(request, "setup.html", {"error": str(exc), "created": None}, 400)
    name = username.strip().lower()
    cs.audit(name, "admin_created", name, {"role": "owner", "via": "setup page"}, ip)
    return _page(request, "setup.html", {"error": "", "created": {
        "username": name, "secret": secret, "uri": auth.otpauth_uri(name, secret)}})


# ── dashboard and hospitals ──────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
@guarded
async def dashboard(request: Request):
    session, admin = _current(request)
    hospitals = cs.list_hospitals()
    health = await asyncio.gather(*(fetch_health(h) for h in hospitals))
    rows = [{"h": h, "health": hl, "cert": cs.latest_certificate(h["code"])} for h, hl in zip(hospitals, health)]
    return _page(request, "dashboard.html", {"admin": admin, "session": session, "rows": rows})


@router.get("/hospitals/new", response_class=HTMLResponse)
@guarded
def hospital_new(request: Request):
    session, admin = _current(request, CAN_EDIT)
    return _page(request, "hospital_form.html", {"admin": admin, "session": session, "creating": True,
                                                 "h": {"status": "active", "fallbacks": []}, "errors": []})


@router.post("/hospitals/new")
@guarded
async def hospital_create(request: Request):
    session, admin = _current(request, CAN_EDIT)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    values, errors = clean_hospital(form, creating=True)
    if not errors and cs.get_hospital(values["code"]):
        errors.append(f"{values['code']} already exists")
    if errors:
        return _page(request, "hospital_form.html", {"admin": admin, "session": session, "creating": True,
                                                     "h": values, "errors": errors}, 400)
    code = values.pop("code")
    cs.save_hospital(code, values, create=True)
    cs.audit(admin["username"], "hospital_created", code, values, _ip(request))
    return RedirectResponse(f"/admin/hospitals/{code}", status_code=303)


@router.get("/hospitals/{code}", response_class=HTMLResponse)
@guarded
async def hospital_page(request: Request, code: str):
    session, admin = _current(request)
    h = cs.get_hospital(code.upper())
    if h is None:
        return _page(request, "error.html", {"admin": admin, "session": session,
                                             "message": f"No hospital {code}."}, 404)
    document = fleet_endpoints.build_hospital_document(h["code"])
    return _page(request, "hospital.html", {
        "admin": admin, "session": session, "h": h, "health": await fetch_health(h),
        "cert": cs.latest_certificate(h["code"]), "document": json.dumps(document, indent=2) if document else "",
        "events": cs.audit_entries(50, target=h["code"]), "issued": None, "versions": _versions(),
        "tokens": cs.enrollment_tokens(h["code"]), "now": datetime.now(timezone.utc).isoformat(),
        "modules": _module_rows(h), **_billing_context(h),
    })


def _billing_context(h: dict) -> dict:
    import admin_billing

    return admin_billing.context(h)


def _module_rows(h: dict) -> list[dict]:
    import profiles

    modules = h["profile"].get("modules", {})
    labels = h["profile"].get("labels", {})
    return [{"key": k, "default": d, "on": modules.get(k, True), "label": labels.get(k, "")}
            for k, d in profiles.MODULES.items()]


@router.get("/hospitals/{code}/edit", response_class=HTMLResponse)
@guarded
def hospital_edit(request: Request, code: str):
    session, admin = _current(request, CAN_EDIT)
    h = cs.get_hospital(code.upper())
    if h is None:
        return RedirectResponse("/admin/", status_code=303)
    return _page(request, "hospital_form.html", {"admin": admin, "session": session, "creating": False,
                                                 "h": h, "errors": []})


@router.post("/hospitals/{code}/edit")
@guarded
async def hospital_update(request: Request, code: str):
    session, admin = _current(request, CAN_EDIT)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    before = cs.get_hospital(code.upper())
    if before is None:
        return RedirectResponse("/admin/", status_code=303)
    values, errors = clean_hospital(form, creating=False)
    if errors:
        return _page(request, "hospital_form.html", {"admin": admin, "session": session, "creating": False,
                                                     "h": {**before, **values}, "errors": errors}, 400)
    cs.save_hospital(before["code"], values, create=False)
    changed = {k: {"from": before.get(k), "to": v} for k, v in values.items() if before.get(k) != v}
    cs.audit(admin["username"], "hospital_updated", before["code"], changed, _ip(request))
    _health_cache.clear()
    return RedirectResponse(f"/admin/hospitals/{before['code']}", status_code=303)


@router.post("/hospitals/{code}/identity", response_class=HTMLResponse)
@guarded
async def hospital_identity(request: Request, code: str):
    """Issue the HQ's identity key and certificate (new) or a fresh
    certificate for its current key (renew). Values are shown once."""
    session, admin = _current(request, ("owner",))
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    h = cs.get_hospital(code.upper())
    if h is None:
        return RedirectResponse("/admin/", status_code=303)

    def back(message, status=400):
        return _page(request, "error.html", {"admin": admin, "session": session, "message": message,
                                             "back": f"/admin/hospitals/{h['code']}"}, status)

    if not auth.verify_totp(admin, form.get("code", "")):
        cs.audit(admin["username"], "identity_denied_bad_code", h["code"], ip=_ip(request))
        return back("The authenticator code was not accepted.", 403)
    urls = [u for u in [h["sync_url"], *h["fallbacks"]] if u]
    if not urls:
        return back("Set the HQ sync address first.")
    action = form.get("action")
    import hq_certificates

    try:
        signer = hq_certificates._signing_key(None)
    except SystemExit as exc:
        return back(f"No signing key on this server: {exc}", 500)

    private_b64 = ""
    if action == "new":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        private_b64 = base64.b64encode(key.private_bytes(serialization.Encoding.Raw,
                                                         serialization.PrivateFormat.Raw,
                                                         serialization.NoEncryption())).decode()
        public_b64 = hq_certificates._raw_public(key.public_key())
    elif action == "renew":
        latest = cs.latest_certificate(h["code"])
        if latest is None:
            return back("No certificate to renew yet; issue a new identity.")
        public_b64 = latest["public_key"]
    else:
        return back("Unknown action.")

    certificate = hq_certificates.issue(signer, h["code"], public_b64, urls)
    fields = json.loads(json.loads(certificate)["document"])
    cs.record_certificate(h["code"], public_b64, urls, fields["issued_at"], fields["expires_at"],
                          admin["username"])
    cs.audit(admin["username"], f"identity_{action}", h["code"],
             {"urls": urls, "expires_at": fields["expires_at"], "public_key": public_b64}, _ip(request))
    return _page(request, "identity_issued.html", {
        "admin": admin, "session": session, "h": h, "action": action,
        "env": [("HOSPITAL_CODE", h["code"]), *([("HQ_IDENTITY_PRIVATE_KEY", private_b64)] if private_b64 else []),
                ("HQ_CERTIFICATE", certificate),
                ("CONTROL_PUBLIC_KEY", hq_certificates._raw_public(signer.public_key())),
                *([("CERT_PREFIX", h["cert_prefix"])] if h["cert_prefix"] else [])],
        "expires": fields["expires_at"],
    })


@router.post("/hospitals/{code}/release")
@guarded
async def hospital_release(request: Request, code: str):
    """Follow the newest release, pin one, or hold (releases.py)."""
    session, admin = _current(request, CAN_EDIT)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    h = cs.get_hospital(code.upper())
    if h is None:
        return RedirectResponse("/admin/", status_code=303)
    mode = form.get("mode", "follow")
    version = (form.get("version") or "").strip() if mode == "pin" else ""
    if mode not in ("follow", "pin", "hold") or (mode == "pin" and version not in _versions()):
        return _page(request, "error.html", {"admin": admin, "session": session,
                                             "message": "Choose follow, hold, or a built version to pin.",
                                             "back": f"/admin/hospitals/{h['code']}"}, 400)
    cs.save_hospital(h["code"], {"release_mode": mode, "release_version": version}, create=False)
    cs.audit(admin["username"], "release_changed", h["code"],
             {"from": f"{h['release_mode']} {h['release_version']}".strip(), "to": f"{mode} {version}".strip()},
             _ip(request))
    return RedirectResponse(f"/admin/hospitals/{h['code']}", status_code=303)


@router.post("/hospitals/{code}/installers")
@guarded
async def installer_create(request: Request, code: str):
    """A new enrollment token for this hospital's installers (enrollment.py)."""
    session, admin = _current(request, CAN_EDIT)
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    h = cs.get_hospital(code.upper())
    if h is None:
        return RedirectResponse("/admin/", status_code=303)
    try:
        max_uses, days = int(form.get("max_uses", "")), int(form.get("days", ""))
    except ValueError:
        max_uses = days = 0
    if not (1 <= max_uses <= 500 and 1 <= days <= 90) or not h["sync_url"]:
        return _page(request, "error.html", {"admin": admin, "session": session, "back": f"/admin/hospitals/{h['code']}",
                                             "message": "Set the HQ sync address first; PCs 1-500, days 1-90."}, 400)
    import enrollment
    import hq_certificates

    try:
        hq_certificates._signing_key(None)
    except SystemExit as exc:
        return _page(request, "error.html", {"admin": admin, "session": session,
                                             "message": f"No signing key on this server: {exc}"}, 500)
    document = enrollment.new_document(h["code"], max_uses, days)
    fields = json.loads(document)
    cs.record_enrollment_token(fields["token_id"], h["code"], document, max_uses, fields["expires_at"],
                               admin["username"])
    cs.audit(admin["username"], "installer_created", h["code"],
             {"token_id": fields["token_id"], "max_uses": max_uses, "expires_at": fields["expires_at"]}, _ip(request))
    return RedirectResponse(f"/admin/hospitals/{h['code']}#installers", status_code=303)


@router.get("/hospitals/{code}/installers/{token_id}/provisioning.json")
@guarded
def installer_download(request: Request, code: str, token_id: str):
    session, admin = _current(request, CAN_EDIT)
    h = cs.get_hospital(code.upper())
    row = cs.enrollment_token(token_id)
    if h is None or row is None or row["hospital"] != h["code"]:
        return Response("Not Found", status_code=404)
    import enrollment
    import hq_certificates

    token = enrollment.token_for(hq_certificates._signing_key(None), row["document"])
    body = json.dumps(enrollment.provisioning(h, token, os.getenv("HQ_API_KEY", "")), indent=2)
    cs.audit(admin["username"], "installer_downloaded", h["code"], {"token_id": token_id}, _ip(request))
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="provisioning-{h["code"].lower()}.json"'})


@router.post("/hospitals/{code}/profile")
@guarded
async def hospital_profile(request: Request, code: str):
    """Publish which modules this hospital's PCs have and their menu labels.
    Each save is a new profile_version; PCs pick it up within ~15 minutes."""
    import profiles

    session, admin = _current(request, CAN_EDIT)
    form = await request.form()
    _check_csrf(session, form.get("csrf", ""))
    h = cs.get_hospital(code.upper())
    if h is None:
        return RedirectResponse("/admin/", status_code=303)
    on = set(form.getlist("on"))
    profile = {
        "modules": {k: k in on for k in profiles.MODULES},
        "labels": {k: str(form.get(f"label_{k}") or "").strip()[:40] for k in profiles.MODULES
                   if str(form.get(f"label_{k}") or "").strip()},
    }
    version = int(h["profile_version"] or 0) + 1
    cs.save_hospital(h["code"], {"profile": profile, "profile_version": version}, create=False)
    off = sorted(k for k, v in profile["modules"].items() if not v)
    cs.audit(admin["username"], "profile_published", h["code"],
             {"version": version, "off": off, "labels": profile["labels"]}, _ip(request))
    return RedirectResponse(f"/admin/hospitals/{h['code']}#profile", status_code=303)


@router.post("/hospitals/{code}/deleted")
@guarded
async def hospital_data_deleted(request: Request, code: str):
    """Record that a closed hospital's HQ data was handed over and deleted."""
    session, admin = _current(request, ("owner",))
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    h = cs.get_hospital(code.upper())
    if h is None or h["status"] != "closed":
        return RedirectResponse("/admin/", status_code=303)
    if not auth.verify_totp(admin, form.get("code", "")):
        return _page(request, "error.html", {"admin": admin, "session": session, "back": f"/admin/hospitals/{h['code']}",
                                             "message": "The authenticator code was not accepted."}, 403)
    note = (form.get("note") or "").strip()
    if len(note) < 10:
        return _page(request, "error.html", {"admin": admin, "session": session, "back": f"/admin/hospitals/{h['code']}",
                                             "message": "Say what was handed over and deleted, and when."}, 400)
    cs.audit(admin["username"], "hospital_data_deleted", h["code"], {"note": note}, _ip(request))
    return RedirectResponse(f"/admin/hospitals/{h['code']}", status_code=303)


# ── audit and admins ─────────────────────────────────────────────────────────

@router.get("/audit", response_class=HTMLResponse)
@guarded
def audit_page(request: Request):
    session, admin = _current(request, ("owner",))
    return _page(request, "audit.html", {"admin": admin, "session": session, "events": cs.audit_entries(500)})


@router.get("/admins", response_class=HTMLResponse)
@guarded
def admins_page(request: Request):
    session, admin = _current(request, ("owner",))
    return _page(request, "admins.html", {"admin": admin, "session": session, "admins": auth.list_admins(),
                                          "roles": auth.ROLES, "error": "", "created": None})


@router.post("/admins/new", response_class=HTMLResponse)
@guarded
async def admin_create(request: Request):
    session, admin = _current(request, ("owner",))
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    context = {"admin": admin, "session": session, "roles": auth.ROLES, "created": None, "error": ""}
    if not auth.verify_totp(admin, form.get("code", "")):
        context["error"] = "The authenticator code was not accepted."
    else:
        try:
            secret = auth.create_admin(form.get("username", ""), form.get("password", ""), form.get("role", ""))
            username = form.get("username", "").strip().lower()
            cs.audit(admin["username"], "admin_created", username, {"role": form.get("role")}, _ip(request))
            context["created"] = {"username": username, "secret": secret,
                                  "uri": auth.otpauth_uri(username, secret)}
        except auth.AuthError as exc:
            context["error"] = str(exc)
    context["admins"] = auth.list_admins()
    return _page(request, "admins.html", context, 400 if context["error"] else 200)


@router.post("/admins/{admin_id}/deactivate", response_class=HTMLResponse)
@guarded
async def admin_deactivate(request: Request, admin_id: int):
    session, admin = _current(request, ("owner",))
    form = dict(await request.form())
    _check_csrf(session, form.get("csrf", ""))
    context = {"admin": admin, "session": session, "roles": auth.ROLES, "created": None, "error": ""}
    target = auth.get_admin(admin_id=admin_id)
    if not auth.verify_totp(admin, form.get("code", "")):
        context["error"] = "The authenticator code was not accepted."
    elif target is None or target["id"] == admin["id"]:
        context["error"] = "You cannot deactivate your own account."
    else:
        cs.conn().execute("UPDATE admins SET active = 0 WHERE id = ?", (admin_id,))
        auth.end_all_sessions(admin_id)
        cs.audit(admin["username"], "admin_deactivated", target["username"], ip=_ip(request))
    context["admins"] = auth.list_admins()
    return _page(request, "admins.html", context, 400 if context["error"] else 200)


def install(app, versions=None) -> None:
    """Mount the panel, its stylesheet, the host restriction and headers.
    ``versions``: callable returning the built release versions, newest first."""
    global _versions
    from fastapi.staticfiles import StaticFiles

    if versions is not None:
        _versions = versions
    cs.init()
    import admin_billing  # noqa: F401  (adds the billing pages to the router)
    import admin_mpesa  # noqa: F401  (the M-Pesa inbox)
    import mpesa

    mpesa.init()

    app.include_router(router)
    app.mount("/admin/static", StaticFiles(directory=str(Path(__file__).parent / "static" / "admin")),
              name="admin-static")

    @app.middleware("http")
    async def _admin_guard(request: Request, call_next):
        if not request.url.path.startswith("/admin"):
            return await call_next(request)
        hosts = admin_hosts()
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if hosts and host not in hosts:
            return Response("Not Found", status_code=404)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if secure_cookies():
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response


def utc(value: str | None) -> str:
    """ISO time → 'DD Mon YYYY HH:MM EAT' for display."""
    if not value:
        return "—"
    try:
        from datetime import timedelta

        t = datetime.fromisoformat(value)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (t.astimezone(timezone(timedelta(hours=3)))).strftime("%d %b %Y %H:%M EAT")
    except ValueError:
        return value


templates.env.filters["eat"] = utc
