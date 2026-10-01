"""The admin panel: who gets in, what each role may do, and that what it
issues and serves is exactly what hospitals' desktops accept."""
import base64
import json
import re
from unittest import mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

import admin_auth as auth
import control_store as cs

PASSWORD = "correct horse battery"
SYNC = "https://hq-ch0001.example.com/api/sync"


class Clock:
    """Authenticator codes are single-use, so each use needs the next 30 s step."""

    def __init__(self):
        self.t = 1_900_000_000.0

    def tick(self):
        self.t += auth.STEP
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(auth.time, "time", lambda: c.t)
    return c


@pytest.fixture
def control_key(monkeypatch):
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    monkeypatch.setenv("HQ_SIGNING_PRIVATE_KEY", base64.b64encode(seed).decode())
    return key


@pytest.fixture
def app(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.setenv("ADMIN_INSECURE_COOKIES", "true")   # TestClient speaks http
    monkeypatch.delenv("ADMIN_HOSTS", raising=False)
    monkeypatch.delenv("FLEET_HOSPITALS", raising=False)
    import admin_panel

    async def fake_health(h):
        return {"state": "up", "hospital": h["code"], "identity": "ok", "prefix": h["cert_prefix"]}

    monkeypatch.setattr(admin_panel, "fetch_health", fake_health)
    application = FastAPI()
    admin_panel.install(application)
    return application


def make_admin(username, role):
    return auth.create_admin(username, PASSWORD, role)


def sign_in(app, clock, username, secret):
    client = TestClient(app)
    resp = client.post("/admin/login", data={"username": username, "password": PASSWORD,
                                             "code": auth.totp_now(secret, clock.tick())},
                       follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return client


def csrf(client, path="/admin/"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


@pytest.fixture
def owner(app, clock):
    secret = make_admin("owner", "owner")
    return sign_in(app, clock, "owner", secret), secret


def add_hospital(client, **overrides):
    form = {"csrf": csrf(client), "code": "ch0001", "name": "Pilot Hospital", "status": "active",
            "sync_url": SYNC + "/", "fallbacks": "https://old.example.com/api/sync", "cert_prefix": "bnh-"}
    form.update(overrides)
    return client.post("/admin/hospitals/new", data=form, follow_redirects=False)


# ── getting in ───────────────────────────────────────────────────────────────

def test_the_panel_needs_a_sign_in(app):
    resp = TestClient(app).get("/admin/", follow_redirects=False)
    assert (resp.status_code, resp.headers["location"]) == (303, "/admin/login")


def test_password_and_code_let_an_admin_in(owner):
    client, _ = owner
    page = client.get("/admin/")
    assert page.status_code == 200 and "Hospitals" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]


def test_a_wrong_code_is_refused_with_the_same_message(app, clock):
    make_admin("tech", "support")
    resp = TestClient(app).post("/admin/login", data={"username": "tech", "password": PASSWORD, "code": "000000"})
    assert resp.status_code == 401 and "Wrong username, password or code" in resp.text


def test_a_code_cannot_be_used_twice(app, clock):
    secret = make_admin("tech", "support")
    code = auth.totp_now(secret, clock.tick())
    first = TestClient(app).post("/admin/login", data={"username": "tech", "password": PASSWORD, "code": code},
                                 follow_redirects=False)
    again = TestClient(app).post("/admin/login", data={"username": "tech", "password": PASSWORD, "code": code})
    assert (first.status_code, again.status_code) == (303, 401)


def test_repeated_failures_lock_the_account(app, clock):
    secret = make_admin("tech", "support")
    client = TestClient(app)
    for _ in range(auth.MAX_FAILS_PER_USER):
        client.post("/admin/login", data={"username": "tech", "password": "wrong", "code": "000000"})
    resp = client.post("/admin/login", data={"username": "tech", "password": PASSWORD,
                                             "code": auth.totp_now(secret, clock.tick())})
    assert resp.status_code == 401 and "Too many failed attempts" in resp.text


def test_a_form_without_its_token_is_refused(owner):
    client, _ = owner
    resp = client.post("/admin/hospitals/new", data={"code": "CH0001", "name": "X"})
    assert resp.status_code == 403


def test_other_host_names_do_not_serve_the_panel(app, monkeypatch):
    monkeypatch.setenv("ADMIN_HOSTS", "admin.cirqenlabs.com")
    client = TestClient(app)
    assert client.get("/admin/login", headers={"host": "updates.cirqenlabs.com"}).status_code == 404
    assert client.get("/admin/login", headers={"host": "admin.cirqenlabs.com"}).status_code == 200


def test_signing_out_ends_the_session(owner):
    client, _ = owner
    client.post("/admin/logout", data={"csrf": csrf(client)})
    assert client.get("/admin/", follow_redirects=False).status_code == 303


# ── hospitals ────────────────────────────────────────────────────────────────

def test_adding_a_hospital(owner):
    client, _ = owner
    resp = add_hospital(client)
    assert (resp.status_code, resp.headers["location"]) == (303, "/admin/hospitals/CH0001")
    h = cs.get_hospital("CH0001")
    assert (h["sync_url"], h["fallbacks"], h["cert_prefix"]) == (SYNC, ["https://old.example.com/api/sync"], "BNH-")
    assert cs.audit_entries(target="CH0001")[0]["action"] == "hospital_created"


def test_a_wrong_address_is_explained_not_saved(owner):
    client, _ = owner
    resp = add_hospital(client, sync_url="http://hq.example.com/sync")
    assert resp.status_code == 400 and "https address ending in /api/sync" in resp.text
    assert cs.get_hospital("CH0001") is None


def test_editing_records_what_changed(owner):
    client, _ = owner
    add_hospital(client)
    token = csrf(client, "/admin/hospitals/CH0001/edit")
    client.post("/admin/hospitals/CH0001/edit", data={"csrf": token, "name": "Pilot Hospital", "status": "suspended",
                                                      "sync_url": SYNC})
    event = cs.audit_entries(target="CH0001")[0]
    assert event["action"] == "hospital_updated"
    assert json.loads(event["detail"])["status"] == {"from": "active", "to": "suspended"}


def test_finance_can_look_but_not_change(app, clock, owner):
    add_hospital(owner[0])
    client = sign_in(app, clock, "books", make_admin("books", "finance"))
    assert client.get("/admin/hospitals/CH0001").status_code == 200
    assert client.get("/admin/hospitals/new").status_code == 403
    assert client.get("/admin/audit").status_code == 403


# ── HQ identity ──────────────────────────────────────────────────────────────

def issue(client, clock, secret, action):
    return client.post("/admin/hospitals/CH0001/identity",
                       data={"csrf": csrf(client, "/admin/hospitals/CH0001"), "action": action,
                             "code": auth.totp_now(secret, clock.tick())})


def env_values(page):
    import html

    pairs = re.findall(r"<dt>([A-Z_]+)</dt><dd><textarea readonly rows=\"\d\">(.*?)</textarea>", page, re.S)
    return {k: html.unescape(v) for k, v in pairs}


def test_an_issued_identity_passes_the_desktops_check(owner, clock, control_key):
    client, secret = owner
    add_hospital(client)
    page = issue(client, clock, secret, "new")
    assert page.status_code == 200, page.text
    env = env_values(page.text)
    assert env["HOSPITAL_CODE"] == "CH0001" and env["CERT_PREFIX"] == "BNH-"

    import hq_handshake

    hq_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(env["HQ_IDENTITY_PRIVATE_KEY"]))
    nonce = b"n" * 32
    answer = {"hospital": "CH0001", "certificate": json.loads(env["HQ_CERTIFICATE"]),
              "proof": base64.b64encode(hq_key.sign(hq_handshake.hello_message(nonce, "CH0001"))).decode()}
    for url in (SYNC, "https://old.example.com/api/sync"):
        assert hq_handshake.check_answer(answer, nonce, url, "CH0001", control_key.public_key()) == ""
    # Control keeps the public half only: the private key is in no table.
    for table in ("hospitals", "hq_certificates", "audit", "enrollment_tokens"):
        assert env["HQ_IDENTITY_PRIVATE_KEY"] not in str([tuple(r) for r in cs.conn().execute(f"SELECT * FROM {table}")])


def test_renewing_keeps_the_key(owner, clock, control_key):
    client, secret = owner
    add_hospital(client)
    issue(client, clock, secret, "new")
    first = cs.latest_certificate("CH0001")["public_key"]
    page = issue(client, clock, secret, "renew")
    assert "HQ_IDENTITY_PRIVATE_KEY" not in env_values(page.text)
    assert cs.latest_certificate("CH0001")["public_key"] == first


def test_issuing_needs_a_fresh_code(owner, clock, control_key):
    client, _ = owner
    add_hospital(client)
    resp = client.post("/admin/hospitals/CH0001/identity",
                       data={"csrf": csrf(client, "/admin/hospitals/CH0001"), "action": "new", "code": "123456"})
    assert resp.status_code == 403 and cs.latest_certificate("CH0001") is None


def test_support_cannot_issue_identities(app, clock, owner, control_key):
    add_hospital(owner[0])
    secret = make_admin("helper", "support")
    client = sign_in(app, clock, "helper", secret)
    assert issue(client, clock, secret, "new").status_code == 403


# ── what desktops are sent ───────────────────────────────────────────────────

def test_desktops_get_the_panels_addresses(owner, control_key, monkeypatch):
    client, _ = owner
    add_hospital(client)
    monkeypatch.setenv("FLEET_HOSPITALS", json.dumps({"CH0001": {"sync": "https://stale.example.com/api/sync"}}))
    import endpoints

    document = endpoints.build_hospital_document("ch0001")
    assert document["endpoints"]["sync.api_url"] == SYNC          # the panel wins over the env
    assert document["fallbacks"] == {"sync.api_url": ["https://old.example.com/api/sync"]}

    token = csrf(client, "/admin/hospitals/CH0001/edit")
    client.post("/admin/hospitals/CH0001/edit", data={"csrf": token, "name": "Pilot", "status": "closed",
                                                      "sync_url": SYNC})
    assert endpoints.build_hospital_document("CH0001") is None   # closed: nothing at all


# ── admins ───────────────────────────────────────────────────────────────────

def test_an_owner_adds_an_admin_with_a_fresh_code(owner, clock):
    client, secret = owner
    page = client.post("/admin/admins/new", data={"csrf": csrf(client, "/admin/admins"), "username": "Mary",
                                                  "password": PASSWORD, "role": "support",
                                                  "code": auth.totp_now(secret, clock.tick())})
    assert page.status_code == 200 and "mary created" in page.text
    assert auth.get_admin(username="mary")["role"] == "support"


def test_a_deactivated_admin_is_signed_out(app, clock, owner):
    client, secret = owner
    helper_secret = make_admin("helper", "support")
    helper = sign_in(app, clock, "helper", helper_secret)
    target = auth.get_admin(username="helper")["id"]
    client.post(f"/admin/admins/{target}/deactivate",
                data={"csrf": csrf(client, "/admin/admins"), "code": auth.totp_now(secret, clock.tick())})
    assert helper.get("/admin/", follow_redirects=False).status_code == 303


def test_the_update_server_serves_the_panel_and_its_addresses(tmp_path, monkeypatch, clock, control_key):
    monkeypatch.setenv("CONTROL_DB", str(tmp_path / "control.db"))
    monkeypatch.setenv("HQ_PACKAGES_DIR", str(tmp_path / "packages"))
    monkeypatch.setenv("HQ_STATE_DB", str(tmp_path / "hq_state.db"))
    import main

    cs.init()
    cs.save_hospital("CH0001", {"name": "Pilot", "sync_url": SYNC, "status": "active"}, create=True)
    client = TestClient(main.app)                     # no startup: nothing is built
    assert client.get("/admin/login").status_code == 200
    assert client.get("/docs").status_code == 404
    payload = client.get("/api/endpoints/CH0001/").json()
    assert json.loads(payload["document"])["endpoints"]["sync.api_url"] == SYNC
    assert client.get("/api/endpoints/CH0009/").status_code == 404


def test_setting_a_hospitals_release(app, owner, monkeypatch):
    import admin_panel

    monkeypatch.setattr(admin_panel, "_versions", lambda: ["1.9.0", "1.8.0"])
    client, _ = owner
    add_hospital(client)
    token = csrf(client, "/admin/hospitals/CH0001")
    assert client.post("/admin/hospitals/CH0001/release", data={"csrf": token, "mode": "pin", "version": "2.0.0"}
                       ).status_code == 400                      # not a built version
    client.post("/admin/hospitals/CH0001/release", data={"csrf": token, "mode": "pin", "version": "1.8.0"})
    h = cs.get_hospital("CH0001")
    assert (h["release_mode"], h["release_version"]) == ("pin", "1.8.0")
    assert cs.audit_entries(target="CH0001")[0]["action"] == "release_changed"


def test_an_installer_file_enrolls_with_the_hospitals_hq(owner, control_key, monkeypatch):
    """The token in the downloaded provisioning.json is what the HQ accepts
    (checked with the HQ's own rule, re-implemented here from its format)."""
    monkeypatch.setenv("HQ_API_KEY", "update-key")
    client, _ = owner
    add_hospital(client)
    token = csrf(client, "/admin/hospitals/CH0001")
    assert client.post("/admin/hospitals/CH0001/installers",
                       data={"csrf": token, "max_uses": "5", "days": "14"}).status_code == 200
    row = cs.enrollment_tokens("CH0001")[0]
    resp = client.get(f"/admin/hospitals/CH0001/installers/{row['token_id']}/provisioning.json")
    assert "attachment" in resp.headers["content-disposition"]
    prov = resp.json()
    assert prov["sync"]["hospital_code"] == "CH0001" and prov["sync"]["api_url"] == SYNC
    assert prov["update"]["api_key"] == "update-key"

    tok = prov["sync"]["enrollment_code"]
    prefix, doc, sig = tok.split(".")
    raw = base64.urlsafe_b64decode(doc + "=" * (-len(doc) % 4))
    control_key.public_key().verify(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)),
                                    b"cirqen-enrollment-v1\n" + raw)
    fields = json.loads(raw)
    assert (prefix, fields["hospital"], fields["max_uses"]) == ("cqe1", "CH0001", 5)
    # the same file downloads again identically (the token is re-derived, not stored)
    assert client.get(f"/admin/hospitals/CH0001/installers/{row['token_id']}/provisioning.json").json() == prov


def test_the_identity_page_includes_controls_public_key(owner, clock, control_key):
    client, secret = owner
    add_hospital(client)
    env = env_values(issue(client, clock, secret, "new").text)
    expected = base64.b64encode(control_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    assert env["CONTROL_PUBLIC_KEY"] == expected


def test_publishing_a_profile_that_its_pcs_accept(owner, control_key, tmp_path, monkeypatch):
    client, _ = owner
    add_hospital(client)
    token = csrf(client, "/admin/hospitals/CH0001")
    on = ["jobcard", "inventory", "ppms", "reports", "machine_reports", "parts_tools"]   # calibration off
    client.post("/admin/hospitals/CH0001/profile",
                data={"csrf": token, "on": on, "label_jobcard": "Job cards"})
    h = cs.get_hospital("CH0001")
    assert h["profile_version"] == 1 and h["profile"]["modules"]["calibration"] is False
    assert json.loads(cs.audit_entries(target="CH0001")[0]["detail"])["off"] == ["calibration"]

    monkeypatch.setenv("HQ_PACKAGES_DIR", str(tmp_path / "packages"))
    import main

    payload = TestClient(main.app).get("/api/profiles/CH0001/").json()
    import hospital_profile   # the desktop's own check

    fields, why = hospital_profile.verify(payload, "CH0001", control_key.public_key())
    assert fields is not None, why
    states = hospital_profile.module_states(fields)
    assert states["calibration"]["on"] is False and states["jobcard"]["label"] == "Job cards"
    assert hospital_profile.verify(payload, "CH0002", control_key.public_key())[0] is None

    client.post("/admin/hospitals/CH0001/profile", data={"csrf": token, "on": on})
    assert cs.get_hospital("CH0001")["profile_version"] == 2      # every publish goes up
    assert TestClient(main.app).get("/api/profiles/CH0009/").status_code == 404


# ── licences and payments ────────────────────────────────────────────────────

def set_licence(client, clock, secret, **overrides):
    form = {"csrf": csrf(client, "/admin/hospitals/CH0001"), "plan": "standard", "devices": "10",
            "starts_on": "2025-11-01", "ends_on": "2026-10-31", "grace_days": "14", "status": "active",
            "code": auth.totp_now(secret, clock.tick())}
    form.update(overrides)
    return client.post("/admin/hospitals/CH0001/licence", data=form)


def test_finance_bills_and_the_pcs_get_the_new_end_date(app, clock, owner, control_key, tmp_path, monkeypatch):
    import billing
    from datetime import date

    monkeypatch.setattr(billing, "today", lambda: date(2026, 10, 1))
    add_hospital(owner[0])
    books_secret = make_admin("books", "finance")
    books = sign_in(app, clock, "books", books_secret)
    assert set_licence(books, clock, books_secret, code="000000").status_code == 403   # needs a real code
    set_licence(books, clock, books_secret)

    token = csrf(books, "/admin/hospitals/CH0001")
    books.post("/admin/hospitals/CH0001/invoices", data={"csrf": token, "months": "12", "amount_kes": "120,000",
                                                          "due_days": "14"})
    inv = billing.invoices("CH0001")[0]
    assert (inv["number"], inv["amount_kes"]) == ("CQ-2026-0001", 120_000)
    assert "CQ-2026-0001" in books.get("/admin/invoices/CQ-2026-0001").text

    receipt = books.post("/admin/hospitals/CH0001/payments",
                         data={"csrf": token, "amount_kes": "120000", "method": "mpesa", "reference": "sjk4h7q2xb",
                               "paid_on": "2026-10-01", "invoice_id": str(inv["id"]), "months": "0"})
    assert "Receipt" in receipt.text and "now valid to 2027-10-31" in receipt.text
    assert json.loads(cs.audit_entries(target="CH0001")[0]["detail"])["extended_months"] == 12

    monkeypatch.setenv("HQ_PACKAGES_DIR", str(tmp_path / "packages"))
    import licence   # the desktop's own check
    import main

    payload = TestClient(main.app).get("/api/licences/CH0001/").json()
    fields, why = licence.verify(payload, "CH0001", control_key.public_key())
    assert fields is not None, why
    assert licence.state(fields, today=date(2026, 10, 1))["state"] == "active"
    assert fields["ends_on"] == "2027-10-31" and fields["licence_version"] == 2


def test_support_cannot_record_money_and_only_owners_reverse(app, clock, owner, control_key):
    import billing

    client, secret = owner
    add_hospital(client)
    set_licence(client, clock, secret)
    token = csrf(client, "/admin/hospitals/CH0001")
    client.post("/admin/hospitals/CH0001/payments", data={"csrf": token, "amount_kes": "10000", "method": "bank",
                                                           "reference": "FT123", "paid_on": "2026-10-01", "months": "1"})
    paid = billing.payments("CH0001")[0]

    helper = sign_in(app, clock, "helper", make_admin("helper", "support"))
    assert helper.post("/admin/hospitals/CH0001/payments", data={"csrf": csrf(helper, "/admin/hospitals/CH0001"),
                                                                 "amount_kes": "1", "method": "bank",
                                                                 "reference": "X", "paid_on": "2026-10-01"}
                       ).status_code == 403
    books = sign_in(app, clock, "books", make_admin("books", "finance"))
    assert books.post(f"/admin/payments/{paid['id']}/reverse",
                      data={"csrf": csrf(books, "/admin/hospitals/CH0001"), "reason": "x", "code": "1"}
                      ).status_code == 403
    client.post(f"/admin/payments/{paid['id']}/reverse",
                data={"csrf": token, "reason": "wrong hospital", "code": auth.totp_now(secret, clock.tick())})
    assert [p["amount_kes"] for p in billing.payments("CH0001")] == [-10000, 10000]
    assert client.get("/admin/money").status_code == 200


def test_the_mpesa_inbox_page_assigns_a_payment(owner, monkeypatch):
    import billing
    import mpesa

    monkeypatch.setenv("MPESA_SHORTCODE", "247247")
    client, _ = owner
    add_hospital(client)
    mpesa.receive({"TransID": "SJK1", "TransAmount": "5000", "BusinessShortCode": "247247",
                   "BillRefNumber": "WRONG", "TransTime": "20261001093012"})
    page = client.get("/admin/mpesa")
    assert page.status_code == 200 and "SJK1" in page.text and "Waiting for a person (1)" in page.text
    client.post("/admin/mpesa/SJK1/assign", data={"csrf": csrf(client, "/admin/mpesa"), "hospital": "CH0001",
                                                  "months": "0"})
    assert mpesa.inbox_row("SJK1")["status"] == "matched"
    assert billing.payments("CH0001")[0]["reference"] == "SJK1"


def test_recording_a_closed_hospitals_data_deletion(owner, clock):
    client, secret = owner
    add_hospital(client)
    token = csrf(client, "/admin/hospitals/CH0001/edit")
    client.post("/admin/hospitals/CH0001/edit", data={"csrf": token, "name": "Pilot", "status": "closed",
                                                      "sync_url": SYNC})
    assert "exit checklist" in client.get("/admin/hospitals/CH0001").text
    client.post("/admin/hospitals/CH0001/deleted",
                data={"csrf": token, "note": "Export given to HOD 2026-11-02; Render services deleted",
                      "code": auth.totp_now(secret, clock.tick())})
    assert cs.audit_entries(target="CH0001")[0]["action"] == "hospital_data_deleted"


# ── first owner without a shell ──────────────────────────────────────────────

def test_setup_page_creates_the_first_owner_once(app, monkeypatch, clock):
    client = TestClient(app)
    assert client.get("/admin/setup").status_code == 404                    # no token set: no page
    monkeypatch.setenv("ADMIN_SETUP_TOKEN", "long-random-setup-token")
    assert client.get("/admin/login", follow_redirects=False).headers["location"] == "/admin/setup"
    wrong = client.post("/admin/setup", data={"token": "guess", "username": "moses", "password": PASSWORD,
                                              "password2": PASSWORD})
    assert wrong.status_code == 401 and auth.count_admins() == 0
    done = client.post("/admin/setup", data={"token": "long-random-setup-token", "username": "Moses",
                                             "password": PASSWORD, "password2": PASSWORD})
    assert done.status_code == 200 and "Owner moses created" in done.text
    secret = re.search(r'<p class="key">([A-Z2-7]+)</p>', done.text).group(1)
    assert client.get("/admin/setup").status_code == 404                    # gone once an admin exists
    sign_in(app, clock, "moses", secret)


def test_setup_attempts_are_limited(app, monkeypatch):
    monkeypatch.setenv("ADMIN_SETUP_TOKEN", "long-random-setup-token")
    client = TestClient(app)
    for _ in range(auth.MAX_FAILS_PER_USER):
        client.post("/admin/setup", data={"token": "x", "username": "a", "password": "b", "password2": "b"})
    blocked = client.post("/admin/setup", data={"token": "long-random-setup-token", "username": "moses",
                                                "password": PASSWORD, "password2": PASSWORD})
    assert blocked.status_code == 429 and auth.count_admins() == 0


def test_postgres_reconnects_after_the_server_drops_it(tmp_path, monkeypatch):
    """Supabase closes idle connections; the next query must just work."""
    if not cs.is_postgres():
        pytest.skip("PostgreSQL only (TEST_CONTROL_DATABASE_URL)")
    cs.save_hospital("CH0001", {"name": "Pilot"}, create=True)
    cs.conn()._conn.close()
    assert cs.get_hospital("CH0001")["name"] == "Pilot"


def test_pcs_without_a_code_follow_the_default_hospital_in_the_panel(owner, monkeypatch):
    import endpoints

    monkeypatch.setenv("FLEET_SYNC_API_URL", "https://old-render-address.example.com/api/sync")
    monkeypatch.setenv("FLEET_DEFAULT_HOSPITAL", "ch0001")
    # Not in the panel yet: the old setting still answers, nothing breaks.
    assert endpoints.build_document()["endpoints"]["sync.api_url"] == "https://old-render-address.example.com/api/sync"

    client, _ = owner
    add_hospital(client)
    doc = endpoints.build_document()
    assert doc["endpoints"]["sync.api_url"] == SYNC
    assert doc["fallbacks"] == {"sync.api_url": ["https://old.example.com/api/sync"]}
    assert "hospital" not in doc          # still the fleet document older PCs understand

    token = csrf(client, "/admin/hospitals/CH0001/edit")
    client.post("/admin/hospitals/CH0001/edit", data={"csrf": token, "name": "Pilot", "status": "active",
                                                      "sync_url": "https://hq-ch0001-new.example.com/api/sync"})
    assert endpoints.build_document()["endpoints"]["sync.api_url"] == "https://hq-ch0001-new.example.com/api/sync"


def test_a_broken_panel_database_closes_only_the_panel(tmp_path, monkeypatch):
    """A wrong CONTROL_DATABASE_URL must not stop the update server."""
    import admin_panel

    monkeypatch.setenv("CONTROL_DATABASE_URL", "postgresql://nobody:secret-pw@127.0.0.1:1/none?connect_timeout=1")
    monkeypatch.setenv("FLEET_SYNC_API_URL", "https://hq.example.com/api/sync")
    monkeypatch.setenv("FLEET_DEFAULT_HOSPITAL", "CH0001")
    monkeypatch.setitem(admin_panel._store, "error", "")
    monkeypatch.setitem(admin_panel._store, "tried", 0.0)
    app = FastAPI()
    admin_panel.install(app)                              # does not raise
    import endpoints

    panel = TestClient(app).get("/admin/login")
    assert panel.status_code == 503 and "secret-pw" not in panel.text
    assert endpoints.build_document()["endpoints"]["sync.api_url"] == "https://hq.example.com/api/sync"


def test_revoking_an_installer_is_what_its_hq_is_told(owner, control_key, tmp_path, monkeypatch):
    client, _ = owner
    add_hospital(client)
    token = csrf(client, "/admin/hospitals/CH0001")
    client.post("/admin/hospitals/CH0001/installers", data={"csrf": token, "max_uses": "5", "days": "14"})
    token_id = cs.enrollment_tokens("CH0001")[0]["token_id"]
    monkeypatch.setenv("HQ_PACKAGES_DIR", str(tmp_path / "packages"))
    import main

    server = TestClient(main.app)

    def status():
        answer = server.get(f"/api/hq/CH0001/enrollment/{token_id}/status").json()
        control_key.public_key().verify(base64.b64decode(answer["signature"]),
                                        b"cirqen-enrollment-status-v1\n" + answer["document"].encode())
        return json.loads(answer["document"])

    first = status()
    assert (first["hospital"], first["token_id"], first["revoked"]) == ("CH0001", token_id, False)
    client.post(f"/admin/hospitals/CH0001/installers/{token_id}/revoke", data={"csrf": token})
    assert status()["revoked"] is True
    assert client.get(f"/admin/hospitals/CH0001/installers/{token_id}/provisioning.json").status_code == 404
    assert server.get(f"/api/hq/CH0002/enrollment/{token_id}/status").status_code == 404   # another hospital
    assert cs.audit_entries(target="CH0001")[0]["action"] == "installer_revoked"


def test_retiring_the_shared_key_is_what_its_hq_is_told(owner, control_key, tmp_path, monkeypatch):
    client, _ = owner
    add_hospital(client)
    monkeypatch.setenv("HQ_PACKAGES_DIR", str(tmp_path / "packages"))
    import main

    server = TestClient(main.app)

    def settings(code="CH0001"):
        answer = server.get(f"/api/hq/{code}/settings").json()
        control_key.public_key().verify(base64.b64decode(answer["signature"]),
                                        b"cirqen-hq-settings-v1\n" + answer["document"].encode())
        return json.loads(answer["document"])

    first = settings()
    assert (first["type"], first["hospital"], first["shared_sync_key"]) == ("cirqen-hq-settings", "CH0001", "accepted")
    token = csrf(client, "/admin/hospitals/CH0001")
    client.post("/admin/hospitals/CH0001/shared-key", data={"csrf": token, "action": "retire"})
    assert settings()["shared_sync_key"] == "retired"
    assert "retired" in client.get("/admin/hospitals/CH0001").text
    client.post("/admin/hospitals/CH0001/shared-key", data={"csrf": token, "action": "accept"})
    assert settings()["shared_sync_key"] == "accepted"
    assert [e["action"] for e in cs.audit_entries(target="CH0001")[:2]] == ["shared_key_accepted", "shared_key_retired"]
    assert server.get("/api/hq/CH0002/settings").status_code == 404


def test_finance_cannot_retire_the_shared_key(app, clock, owner):
    client, _ = owner
    add_hospital(client)
    other = sign_in(app, clock, "fin", make_admin("fin", "finance"))
    other.post("/admin/hospitals/CH0001/shared-key", data={"csrf": csrf(other, "/admin/hospitals/CH0001"),
                                                          "action": "retire"})
    assert cs.get_hospital("CH0001")["shared_key_retired_at"] is None
