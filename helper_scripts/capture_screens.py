"""Capture every Equiper module screen from the running dev server as a PNG.

Logs in as an HOD (so every workshop is visible), injects the session cookie into
QtWebEngine, then walks the shot list. Offscreen — no window appears.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                      "--disable-gpu --no-sandbox --disable-software-rasterizer")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Equiper.settings")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django
django.setup()

from django.conf import settings
settings.ALLOWED_HOSTS = ["*"]
from django.test import Client
from django.contrib.auth import get_user_model

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile

BASE = "http://127.0.0.1:8765"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "demo_frames")
W, H = 1920, 1080

# Several pages switch panels client-side, so the URL alone lands on the first
# tab. click_tab() picks the named one before the shot is taken — without it the
# "Tools" and "Manufacturers" screens silently capture the tab next door.
CLICK_TAB = """
(function () {
  var want = "%s";
  var els = document.querySelectorAll(
    'a, button, [role=tab], .nav-link, .nav-item, li[data-section], [data-bs-toggle=tab]');
  for (var i = 0; i < els.length; i++) {
    var t = (els[i].textContent || '').trim().toLowerCase();
    if (t.indexOf(want) === 0) { els[i].click(); return 'clicked: ' + t; }
  }
  return 'no tab matching ' + want;
})()
"""


def click_tab(name):
    return CLICK_TAB % name.lower()


# (slug, path, settle_ms, js_before_shot)
SHOTS = [
    ("01-hod-dashboard",   "/dashboard/hod-dashboard/",              3000, None),
    ("02-inventory",       "/Inventory/",                            3000, None),
    ("03-jobcard-new",     "/jobcard/",                              2500, None),
    ("04-jobcard-waiting", "/jobcard/jobcards/waiting/",             2500, None),
    ("05-jobcard-approved","/jobcard/jobcards/approved/",            2500, None),
    ("06-ppm",             "/ppms/",                                 3500, None),
    ("07-cal-schedules",   "/calSchedules/",                         3500, None),
    ("08-cal-dashboard",   "/calibration/",                          3500, None),
    ("09-cal-procedures",  "/calibration/procedures/",               2500, None),
    ("10-cal-standards",   "/calibration/standards/",                2500, None),
    ("11-cal-approvals",   "/calibration/sessions/pending-approval/",2500, None),
    ("12-cal-certificates","/calibration/certificates/",             2500, None),
    ("13-accessories",     "/accessories/",                          2500, None),
    ("14-tools",           "/accessories/",                          2500, click_tab("tools")),
    ("15-machine-reports", "/machineReports/",                       4000, None),
    ("16-manufacturers",   "/machineReports/",                       4000, click_tab("manufacturers")),
    ("17-report-hub",      "/reports/",                              2500, None),
    ("18-audit-log",       "/audit-log/",                            2500, None),
]

os.makedirs(OUT, exist_ok=True)

# ---- session ----
User = get_user_model()
user = User.objects.get(username="maina")
c = Client()
c.force_login(user)
session_key = c.cookies["sessionid"].value
print("session for", user.username, "->", session_key[:12] + "...")

# ---- browser ----
app = QApplication(sys.argv)
view = QWebEngineView()
view.resize(W, H)
view.show()

page = view.page()
profile = page.profile()
store = profile.cookieStore()
cookie = QNetworkCookie(b"sessionid", session_key.encode())
cookie.setDomain("127.0.0.1")
cookie.setPath("/")
store.setCookie(cookie, QUrl(BASE))

results = []
idx = 0


def next_shot():
    global idx
    if idx >= len(SHOTS):
        print("\n--- summary ---")
        for slug, path, ok, size in results:
            print(f"{'OK ' if ok else 'BLANK'} {slug:22s} {path:44s} {size}")
        app.quit()
        return
    slug, path, settle, js = SHOTS[idx]
    print(f"[{idx+1}/{len(SHOTS)}] {path}{'  + tab' if js else ''}")
    view.load(QUrl(BASE + path))


def on_load(ok):
    slug, path, settle, js = SHOTS[idx]

    def after_settle():
        if not js:
            shoot(slug, path)
            return
        # click the tab, then give the panel a moment to render before grabbing
        page.runJavaScript(js, lambda r: (print(f"      {r}"),
                                          QTimer.singleShot(1200,
                                                            lambda: shoot(slug, path))))

    QTimer.singleShot(settle, after_settle)


def shoot(slug, path):
    global idx
    pix = view.grab()
    target = os.path.join(OUT, slug + ".png")
    pix.save(target, "PNG")
    img = pix.toImage()
    colors = {img.pixelColor(x, y).name()
              for x in range(60, W - 60, 220) for y in range(60, H - 60, 160)}
    ok = len(colors) > 2
    results.append((slug, path, ok, os.path.getsize(target)))
    idx += 1
    QTimer.singleShot(150, next_shot)


view.loadFinished.connect(on_load)
QTimer.singleShot(200, next_shot)
QTimer.singleShot(240000, lambda: (print("TIMEOUT"), app.quit()))
sys.exit(app.exec())
