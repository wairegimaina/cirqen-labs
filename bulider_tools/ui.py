# Auto-generated refactor of the original Cirqen main.py UI layer.
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QLinearGradient, QPen, QPixmap
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QFileDialog,
    QProgressBar,
    QPushButton,
    QSplashScreen,
    QVBoxLayout,
    QWidget,
)

from .runtime import *
from .services import ServiceManager

#===========
#progress bar
#==========
class UpdateProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Downloading Update")

        layout = QVBoxLayout()

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        self.status = QLabel("Downloading...")
        layout.addWidget(self.status)

        self.setLayout(layout)

    def update_progress(self, current, total):
        percent = int((current / total) * 100)
        self.progress.setValue(percent)
        self.status.setText(f"Downloaded {current}/{total} files")
# ============================
# Splash Screen
# ============================
class CustomSplashScreen(QSplashScreen):
    """
    Animated splash screen — Cirqen medical physics theme.
    Woven double-C mark (static), isocenter crosshair with pulsing
    concentric glow rings (animated), Engineering Healthcare tagline.
    """

    _MSG_MAP = {
        "clearing stale states":          "Getting things ready\u2026",
        "startup warmup complete":         "Almost there\u2026",
        "starting postgresql local":       "Setting up your workspace\u2026",
        "starting postgresql hq":          "Connecting to the network\u2026",
        "starting redis":                  "Preparing fast storage\u2026",
        "starting web server":             "Warming up the interface\u2026",
        "starting task worker":            "Activating background workers\u2026",
        "starting task scheduler":         "Scheduling your automations\u2026",
        "starting sync agent":             "Syncing your data\u2026",
        "starting update manager":         "Checking for improvements\u2026",
        "postgresql ready":                "Database is ready\u2026",
        "django ready":                    "Interface is ready\u2026",
        "ready!":                          "You\u2019re all set!",
        "warmup":                          "Optimising performance\u2026",
        "checking postgresql":             "Verifying your database\u2026",
        "initialising database":           "Setting up your data\u2026",
        "configuring":                     "Finalising configuration\u2026",
        "creating application database":   "Building your workspace\u2026",
        "setting up database schema":      "Organising your data\u2026",
        "creating hod user":               "Configuring account settings\u2026",
        "installing postgresql":           "Installing database service\u2026",
        "finalising setup":                "Almost ready\u2026",
        "setup complete":                  "Setup complete!",
        "verifying user":                  "Verifying account\u2026",
        "starting postgresql (pid":        "Starting up your database\u2026",
    }

    # Palette
    CLR_BG     = QColor("#080A0F")
    CLR_WHITE  = QColor("#FFFFFF")
    CLR_BLUE   = QColor(79, 195, 255)
    CLR_MUTED  = QColor(80, 100, 120)
    CLR_BORDER = QColor(255, 255, 255, 18)
    W, H       = 900, 540

    def __init__(self, port_manager):
        pixmap = QPixmap(self.W, self.H)
        pixmap.fill(self.CLR_BG)
        super().__init__(pixmap)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.port_manager  = port_manager
        self.progress      = 0
        self.message       = "Getting things ready\u2026"
        # Animation state
        self._text_alpha   = 0     # 0->255 logo fade-in
        # Load SVG logo renderer
        try:
            from PySide6.QtSvg import QSvgRenderer
            _svg_path = APPLICATION_PATH / 'static' / 'images' / 'white.svg'
            self._svg_renderer = QSvgRenderer(str(_svg_path)) if _svg_path.exists() else None
        except Exception:
            self._svg_renderer = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(33)      # ~30 fps

    def _on_tick(self):
        self._text_alpha = min(255, self._text_alpha + 7)
        self.repaint()

    def drawContents(self, p):
        from PySide6.QtGui  import QBrush
        from PySide6.QtCore import QRectF

        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        ta  = self._text_alpha

        # ── Background ─────────────────────────────────────────────────────
        bg = QLinearGradient(0, 0, self.W, self.H)
        bg.setColorAt(0.0, self.CLR_BG)
        bg.setColorAt(1.0, QColor("#0d1018"))
        p.fillRect(0, 0, self.W, self.H, bg)
        p.setPen(QPen(self.CLR_BORDER, 1))
        p.drawLine(0, 0, self.W, 0)

        # ── Corner brackets ────────────────────────────────────────────────
        blen, bm = 20, 26
        bp = QPen(QColor(79, 195, 255, 50), 1.2, Qt.SolidLine, Qt.RoundCap)
        p.setPen(bp)
        for (x0, y0, dx, dy) in [
            (bm,          bm,          +1, +1),
            (self.W - bm, bm,          -1, +1),
            (bm,          self.H - bm, +1, -1),
            (self.W - bm, self.H - bm, -1, -1),
        ]:
            p.drawLine(int(x0), int(y0), int(x0 + dx * blen), int(y0))
            p.drawLine(int(x0), int(y0), int(x0), int(y0 + dy * blen))

        # ── SVG Logo (fades in) ────────────────────────────────────────────
        if ta > 0 and self._svg_renderer and self._svg_renderer.isValid():
            # Centre the logo in the available area above the progress bar
            logo_w, logo_h = 820, 379
            logo_x = (self.W - logo_w) / 2
            logo_y = (self.H - 80 - logo_h) / 2   # vertically centred above progress bar
            p.setOpacity(ta / 255.0)
            self._svg_renderer.render(p, QRectF(logo_x, logo_y, logo_w, logo_h))
            p.setOpacity(1.0)

        # ── Progress bar ───────────────────────────────────────────────────
        bx, by, bw, bh = 314, self.H - 64, 545, 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(bx, by, bw, bh, 1, 1)
        filled = int(bw * self.progress / 100)
        if filled > 0:
            fg = QLinearGradient(bx, 0, bx + filled, 0)
            fg.setColorAt(0.0, QColor(79, 195, 255, 130))
            fg.setColorAt(1.0, QColor(79, 195, 255, 255))
            p.setBrush(QBrush(fg))
            p.drawRoundedRect(bx, by, filled, bh, 1, 1)
        if 0 < filled < bw:
            p.setBrush(QColor(200, 235, 255, 200))
            p.drawEllipse(QRectF(bx + filled - 3, by - 1.5, 6, 6))

        # ── Status message ─────────────────────────────────────────────────
        font = QFont("Helvetica Neue", 9)
        p.setFont(font)
        p.setPen(self.CLR_MUTED)
        p.drawText(bx, self.H - 48, bw, 20, Qt.AlignLeft | Qt.AlignVCenter, self.message)
        p.setPen(QColor(79, 195, 255, 110))
        p.drawText(bx, self.H - 48, bw, 20, Qt.AlignRight | Qt.AlignVCenter,
                   f"{self.progress}%")

    def _friendly(self, raw: str) -> str:
        key = raw.lower().strip()
        for pattern, friendly in self._MSG_MAP.items():
            if pattern in key:
                return friendly
        for prefix in ("warmup:", "starting", "checking", "verifying",
                       "initialising", "initializing", "configuring"):
            if key.startswith(prefix):
                return "Optimising your experience\u2026"
        return raw

    def update_progress(self, message: str, progress: int):
        self.message  = self._friendly(message)
        self.progress = progress
        self.repaint()

# ============================
# ENHANCED: Main Window
# ============================
class MainWindow(QMainWindow):
    """Main window with embedded Django application - Modern Clean Design"""

    # The HQ-reachability ping in update_sync_online_indicator() runs on a
    # plain background thread (no Qt event loop), so it cannot safely touch
    # widgets directly or rely on QTimer.singleShot to hop back to the main
    # thread — that combination is what left the indicator stuck on
    # "Checking…" forever. A Qt signal is the one thing that IS safe to
    # emit cross-thread; the connected slot below runs on the main thread.
    _sync_check_result = Signal(bool, int, str)

    def __init__(self, port_manager: PortManager, service_manager: ServiceManager):
        """
        Initialize Main Window with modern, minimal UI
        """
        super().__init__()

        self.port_manager = port_manager
        self.service_manager = service_manager
        self.django_port = port_manager.get_port('django')
        self.django_ready = False
        self.update_info = None
        self._sync_check_result.connect(self._apply_sync_status)

        # ============================================================
        # WINDOW CONFIGURATION
        # ============================================================
        self.setWindowTitle("Cirqen - Calibration & Maintenance Management System")

        # Auto-detect screen size and fit the window to fill it fully
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        screen_w = screen_geometry.width()
        screen_h = screen_geometry.height()
        logger.info(f"Detected screen size: {screen_w}x{screen_h}")

        # Set minimum size relative to screen (90% floor) and resize to full screen
        self.setMinimumSize(int(screen_w * 0.9), int(screen_h * 0.9))
        self.setGeometry(screen_geometry)

        # NOTE: do NOT call show() or showMaximized() here.
        # The window is shown only after the splash finishes in show_window_when_ready().

        icon_path = APPLICATION_PATH / 'resources' / 'icon.png'
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # ============================================================
        # CENTRAL WIDGET SETUP
        # ============================================================
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ============================================================
        # WEB VIEW - Full Screen Content
        # ============================================================
        logger.info("Initializing web view (modern clean design)...")

        self.web_view = QWebEngineView()

        settings = self.web_view.settings()
        settings.setAttribute(settings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(settings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(settings.WebAttribute.PluginsEnabled, True)

        self.web_view.loadStarted.connect(self.on_load_started)
        self.web_view.loadProgress.connect(self.on_load_progress)
        self.web_view.loadFinished.connect(self.on_load_finished)

        self.setup_download_handler()
        self.setup_new_window_handler()

        # Add web view
        main_layout.addWidget(self.web_view)

        # ============================================================
        # MODERN BOTTOM BAR - Clean & Minimalist
        # ============================================================
        bottom_bar = QWidget()
        bottom_bar.setFixedHeight(32)
        bottom_bar.setStyleSheet("""
            QWidget {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #111111,
                    stop:1 #0a0a0a
                );
                border-top: 1px solid rgba(255, 255, 255, 0.08);
            }
        """)

        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(15, 0, 15, 0)
        bottom_layout.setSpacing(12)

        # Left side - Status message
        self.status_label = QLabel("⏳ Starting...")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #e8e8e8;
                font-size: 11px;
                font-weight: 500;
            }
        """)
        bottom_layout.addWidget(self.status_label)

        # Sync / HQ connection indicator (reads agent_status.json)
        # Initialise as "checking" until the first agent_status.json read confirms state.
        self.sync_online_label = QLabel("🔵 Checking…")
        self.sync_online_label.setStyleSheet("""
            QLabel {
                color: #60a5fa;
                font-size: 10px;
                font-weight: 500;
                padding: 2px 8px;
                background-color: rgba(96,165,250,0.12);
                border-radius: 4px;
                border: 1px solid rgba(96,165,250,0.35);
            }
        """)
        self.sync_online_label.setToolTip("Sync agent: checking connection to HQ server…")
        bottom_layout.addWidget(self.sync_online_label)

        bottom_layout.addStretch()

        # ── Update status label ──────────────────────────────────────────
        self.update_status_label = QLabel("⚙️ Checking updates…")
        self.update_status_label.setStyleSheet("""
            QLabel {
                color: #606060;
                font-size: 10px;
                padding: 4px 8px;
            }
        """)
        self.update_status_label.setToolTip("Update system status")
        bottom_layout.addWidget(self.update_status_label)

        # ── "Check Now" button ───────────────────────────────────────────
        self.check_update_btn = QPushButton("⟳ Check")
        self.check_update_btn.setFixedHeight(24)
        self.check_update_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(96, 165, 250, 0.12);
                color: #60a5fa;
                border: 1px solid rgba(96, 165, 250, 0.30);
                border-radius: 4px;
                font-size: 10px;
                padding: 0px 8px;
            }
            QPushButton:hover {
                background-color: rgba(96, 165, 250, 0.22);
                border-color: #60a5fa;
            }
            QPushButton:pressed { background-color: rgba(96, 165, 250, 0.35); }
            QPushButton:disabled {
                color: #404040;
                border-color: rgba(255,255,255,0.06);
                background: rgba(255,255,255,0.03);
            }
        """)
        self.check_update_btn.setToolTip("Check for updates now")
        self.check_update_btn.clicked.connect(self._on_check_now_clicked)
        bottom_layout.addWidget(self.check_update_btn)

        # ── "Restart & Update" button (backend / mixed updates) ──────────
        self.restart_update_btn = QPushButton("↺ Restart & Update")
        self.restart_update_btn.setFixedHeight(24)
        self.restart_update_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(34, 197, 94, 0.18);
                color: #22c55e;
                border: 1px solid rgba(34, 197, 94, 0.40);
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                padding: 0px 10px;
            }
            QPushButton:hover {
                background-color: rgba(34, 197, 94, 0.30);
                border-color: #22c55e;
            }
            QPushButton:pressed { background-color: rgba(34, 197, 94, 0.45); }
        """)
        self.restart_update_btn.setVisible(False)
        self.restart_update_btn.setToolTip("Restart now to apply the downloaded update")
        self.restart_update_btn.clicked.connect(
            lambda: self._show_restart_dialog(
                getattr(self, "_pending_update_version", ""),
                getattr(self, "_pending_change_type", "backend"),
            )
        )
        bottom_layout.addWidget(self.restart_update_btn)

        # ── "Apply Update" button (frontend-only updates) ────────────────
        self.apply_frontend_btn = QPushButton("✨ Apply Update")
        self.apply_frontend_btn.setFixedHeight(24)
        self.apply_frontend_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(139, 92, 246, 0.18);
                color: #a78bfa;
                border: 1px solid rgba(139, 92, 246, 0.40);
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                padding: 0px 10px;
            }
            QPushButton:hover {
                background-color: rgba(139, 92, 246, 0.30);
                border-color: #a78bfa;
            }
            QPushButton:pressed { background-color: rgba(139, 92, 246, 0.45); }
        """)
        self.apply_frontend_btn.setVisible(False)
        self.apply_frontend_btn.setToolTip("Apply UI update instantly — no restart needed")
        self.apply_frontend_btn.clicked.connect(
            lambda: self._apply_frontend_update(
                getattr(self, "_pending_update_version", ""),
            )
        )
        bottom_layout.addWidget(self.apply_frontend_btn)

        # ── transient state ──────────────────────────────────────────────
        self._pending_update_version  = ""
        self._pending_change_type     = "backend"
        self._restart_dialog_shown_for = None

        # Separator
        sep1 = self._create_separator()
        bottom_layout.addWidget(sep1)

        # Refresh Button
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setFixedSize(28, 24)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(34, 197, 94, 0.15);
                color: #22c55e;
                border: 1px solid rgba(34, 197, 94, 0.25);
                border-radius: 4px;
                font-size: 15px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(34, 197, 94, 0.25);
                border-color: #22c55e;
            }
            QPushButton:pressed {
                background-color: rgba(34, 197, 94, 0.35);
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.04);
                color: #3a3a3a;
                border-color: rgba(255, 255, 255, 0.06);
            }
        """)
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(self.refresh_page)
        self.refresh_btn.setToolTip("Refresh page")
        bottom_layout.addWidget(self.refresh_btn)

        # Logs Button


        main_layout.addWidget(bottom_bar)

        self.setCentralWidget(central_widget)

        # ============================================================
        # TIMERS
        # ============================================================
        # No update-status polling timer needed — we use Qt signals directly
        # from AppUpdateService.  We keep a dummy attribute so old code that
        # calls self.update_status_timer.stop() doesn't crash.
        self.update_status_timer = QTimer()   # unused but kept for compat

        # Sync online indicator timer
        self.sync_status_timer = QTimer()
        self.sync_status_timer.timeout.connect(self.update_sync_online_indicator)
        self.sync_status_timer.start(30000)  # 30 seconds

        # Wire AppUpdateService signals (connected later by app.py after start)
        # Stored so app.py can call:  main_window.connect_update_service(svc)
        self._update_service = None

        self.is_loading = False

        logger.info("✅ Main window initialized (modern clean design)")
        logger.info("=" * 70)

    def _create_separator(self):
        """Create a vertical separator for the bottom bar"""
        sep = QLabel("│")
        sep.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.10);
                font-size: 16px;
                padding: 0px 4px;
            }
        """)
        return sep

    # ------------------------------------------------------------------
    # AppUpdateService integration
    # ------------------------------------------------------------------

    def connect_update_service(self, svc):
        """
        Wire AppUpdateService signals into the UI.
        Called by app.py after both the service and the main window exist.
        """
        self._update_service = svc
        svc.update_available.connect(self._on_update_available)
        svc.update_ready.connect(self._on_update_ready)
        svc.frontend_applied.connect(self._on_frontend_applied)
        svc.status_changed.connect(self._on_status_changed)
        svc.error_occurred.connect(self._on_update_error)
        logger.info("MainWindow: connected to AppUpdateService signals")

    def _on_check_now_clicked(self):
        """'Check Now' button — disable briefly then delegate to service."""
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("⟳ Checking…")
        if self._update_service:
            self._update_service.check_now()
        # Re-enable after 15 s (the service will emit status_changed faster)
        QTimer.singleShot(15000, self._reset_check_button)

    def _reset_check_button(self):
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText("⟳ Check")

    def _on_update_available(self, version: str, changes: str, critical: bool):
        """Service found a new version — update the label, hide action buttons."""
        self._pending_update_version = version
        label = f"📦 v{version} available"
        color = "#f59e0b"
        tip   = f"Update v{version} available — downloading in background…"
        if critical:
            label = f"🚨 v{version} (critical)"
            color = "#ef4444"
            tip   = f"CRITICAL update v{version} — will be applied on next restart."
        self._set_update_label(label, color, tip)
        self.restart_update_btn.setVisible(False)
        self.apply_frontend_btn.setVisible(False)
        self._reset_check_button()

    def _on_update_ready(self, version: str, staged_path: str, change_type: str):
        """
        Package downloaded and verified.
        change_type: 'frontend' → show Apply button (no restart)
                     'backend' / 'mixed' / 'migration' → show Restart button
        """
        self._pending_update_version = version
        self._pending_change_type    = change_type
        self._reset_check_button()

        if change_type == "frontend":
            self._set_update_label(
                f"✨ v{version} ready (UI)",
                "#a78bfa",
                f"UI update v{version} ready — click Apply to refresh instantly, no restart needed.",
            )
            self.apply_frontend_btn.setVisible(True)
            self.restart_update_btn.setVisible(False)
            # Auto-apply frontend-only updates silently
            self._apply_frontend_update(version)

        elif change_type == "migration":
            self._set_update_label(
                f"🗄 v{version} ready (DB)",
                "#34d399",
                f"Database migration v{version} ready.",
            )
            self.apply_frontend_btn.setVisible(True)
            self.restart_update_btn.setVisible(False)

        else:
            # backend or mixed
            self._set_update_label(
                f"✅ v{version} ready",
                "#22c55e",
                f"Update v{version} downloaded. Click Restart to apply.",
            )
            self.restart_update_btn.setVisible(True)
            self.apply_frontend_btn.setVisible(False)
            # Auto-show restart dialog once per version
            if self._restart_dialog_shown_for != version:
                self._restart_dialog_shown_for = version
                self._show_restart_dialog(version, change_type)

    def _on_frontend_applied(self, version: str):
        """Frontend or migration update applied without restart — reload the web view."""
        logger.info("Frontend update v%s applied — reloading web view", version)
        self._set_update_label(f"✅ v{version} applied", "#22c55e",
                               f"v{version} applied. Page reloaded.")
        self.apply_frontend_btn.setVisible(False)
        self.restart_update_btn.setVisible(False)
        QTimer.singleShot(500, self.refresh_page)

    def _on_status_changed(self, status: dict):
        """Catch-all status update from the service."""
        checking  = status.get("checking", False)
        downloading = status.get("downloading", False)
        progress  = status.get("download_progress", 0)
        version   = status.get("new_version") or ""
        error     = status.get("error") or ""
        server_ok = status.get("server_available")
        last_check = status.get("last_check", "")

        def _fmt(iso):
            if not iso:
                return ""
            try:
                from datetime import datetime as _dt
                return _dt.fromisoformat(iso).strftime("%H:%M")
            except Exception:
                return ""

        lc = _fmt(last_check)
        lc_text = f"@ {lc}" if lc else ""

        if error and not downloading and not checking:
            self._set_update_label(
                "⚠️ Update failed", "#f97316",
                f"Update check failed: {error}"
            )
        elif downloading:
            prog = f" {progress}%" if progress else ""
            self._set_update_label(
                f"⬇️ Downloading{prog}", "#3b82f6",
                f"Downloading v{version}…  Do not close the app."
            )
        elif checking:
            self._set_update_label("⚙️ Checking…", "#60a5fa", "Checking for updates…")
        elif server_ok is False and not status.get("update_available"):
            self._set_update_label(
                f"⚠️ Server offline {lc_text}".strip(), "#f59e0b",
                "Update server unreachable. Will retry automatically."
            )
        elif server_ok and not status.get("update_available"):
            self._set_update_label(
                f"✅ Up to date {lc_text}".strip(), "#22c55e",
                f"Running latest version. Last checked: {lc or 'recently'}"
            )
        # update_available / update_ready cases are handled by dedicated slots

    def _on_update_error(self, message: str):
        self._set_update_label("⚠️ Update failed", "#f97316", message)
        self._reset_check_button()

    def _set_update_label(self, text: str, color: str, tip: str = ""):
        self.update_status_label.setText(text)
        self.update_status_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-size: 10px;
                padding: 4px 8px;
            }}
        """)
        if tip:
            self.update_status_label.setToolTip(tip)

    # kept for backward-compat (called from app.py QTimer.singleShot)
    def update_update_status(self):
        pass

    def _apply_frontend_update(self, version: str):
        """Kick off frontend apply in the service (no UI block needed)."""
        if self._update_service and version:
            self.apply_frontend_btn.setEnabled(False)
            self.apply_frontend_btn.setText("✨ Applying…")
            self._update_service.apply_and_restart(version)
            QTimer.singleShot(10000, lambda: (
                self.apply_frontend_btn.setEnabled(True),
                self.apply_frontend_btn.setText("✨ Apply Update"),
            ))

    def _show_restart_dialog(self, new_version: str, change_type: str = "backend"):
        """
        Confirm-and-apply dialog.
        For 'frontend'/'migration' change_type this calls apply_and_restart
        which will hot-reload rather than quit — but the user asked to apply,
        so we honour it.
        """
        import json as _j

        if not new_version:
            return

        is_frontend = change_type in ("frontend", "migration")

        dlg = QMessageBox(self)
        dlg.setWindowTitle("Update Ready")
        dlg.setIcon(QMessageBox.Information)
        dlg.setText(f"<b>Cirqen v{new_version} is ready to install.</b>")

        if is_frontend:
            dlg.setInformativeText(
                "This is a UI / database update and can be applied instantly "
                "without restarting the application.\n\nApply now?"
            )
        else:
            dlg.setInformativeText(
                "A restart is required to apply this update.\n\n"
                "Save any work before continuing."
            )

        dlg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dlg.setDefaultButton(QMessageBox.Yes)
        dlg.button(QMessageBox.Yes).setText("Apply Now" if is_frontend else "Restart Now")
        dlg.button(QMessageBox.No).setText("Later")

        if dlg.exec() != QMessageBox.Yes:
            return

        # Locate staged zip
        staged_zip = DATA_PATH / "update_staging" / f"cirqen_update_v{new_version}.zip"
        if not staged_zip.exists():
            try:
                sd  = _j.loads((DATA_PATH / "sync_state" / "update_status.json").read_text())
                alt = sd.get("staged_path", "")
                if alt and _Path(alt).exists():
                    staged_zip = _Path(alt)
            except Exception:
                pass

        if not staged_zip.exists():
            QMessageBox.warning(
                self, "Package Missing",
                f"The update package for v{new_version} was not found.\n"
                "It will be re-downloaded on next check.",
            )
            return

        # Delegate to service (handles frontend vs backend internally)
        if self._update_service:
            self.restart_update_btn.setEnabled(False)
            self.restart_update_btn.setText("↺ Applying…")
            self._update_service.apply_and_restart(new_version)
            return

        # Fallback when service not wired — write sentinel and quit
        from bulider_tools.runtime import restart_and_apply_update
        restart_and_apply_update(new_version)

    def update_sync_online_indicator(self):
        """
        Determine HQ / sync-agent connectivity and update the bottom-bar
        indicator. Layers 1 & 2 read local files (fast, main thread).
        Layer 3 HTTP ping runs in a background thread to avoid blocking Qt.
        """
        import json as _json
        import threading

        def _read_json(path):
            try:
                with open(path, 'r') as _f:
                    return _json.load(_f)
            except Exception:
                return {}

        def _ping_hq(url: str, timeout: float = 5.0) -> bool:
            import urllib.request as _req
            import urllib.error as _uerr
            try:
                _req.urlopen(url, timeout=timeout)
                return True
            except _uerr.HTTPError:
                return True
            except Exception:
                return False

        try:
            sync_state_dir = DATA_PATH / 'sync_state'
            hq_online = None
            pending   = 0
            last_sync = ''

            # ── Layer 1: agent_status.json ────────────────────────────────
            status_file = sync_state_dir / 'agent_status.json'
            if status_file.exists():
                status = _read_json(status_file)
                if 'hq_online' in status:
                    hq_online = bool(status['hq_online'])
                pending   = status.get('pending_changes', 0)
                last_sync = status.get('last_sync') or status.get('last_update', '')

            # ── Layer 2: alternative state files ─────────────────────────
            if hq_online is None:
                for alt_name in ('sync_state.json', 'heartbeat.json', 'status.json'):
                    alt_file = sync_state_dir / alt_name
                    if not alt_file.exists():
                        continue
                    alt = _read_json(alt_file)
                    for key in ('hq_online', 'online', 'connected',
                                'hq_connected', 'is_online', 'server_online'):
                        val = alt.get(key)
                        if val is not None:
                            hq_online = bool(val)
                            pending   = alt.get('pending_changes', pending)
                            last_sync = alt.get('last_sync') or alt.get('last_update', last_sync)
                            break
                    if hq_online is not None:
                        break

            if hq_online is not None:
                # Layers 1/2 gave us an answer — update immediately on main thread
                self._apply_sync_status(hq_online, pending, last_sync)
            else:
                # ── Layer 3: HTTP ping — run in background thread ──────────
                # Resolved the same way the updater resolves it (env, then
                # config.json, then provisioning, then the shipped default).
                # This used to read top-level keys that config.json never has,
                # so it always pinged a hardcoded host.
                from config import resolve_endpoints
                hq_url = resolve_endpoints(DATA_PATH)['update.server_url'][0]
                _pending  = pending
                _last     = last_sync

                def _bg_ping():
                    result = _ping_hq(hq_url)
                    # Emit, don't QTimer.singleShot — this runs on a plain
                    # background thread with no Qt event loop, so singleShot
                    # here silently never fires and the label was stuck on
                    # "Checking…" forever. A signal is safe to emit from any
                    # thread; the connected slot runs on the main thread.
                    self._sync_check_result.emit(result, _pending, _last)

                threading.Thread(target=_bg_ping, daemon=True).start()

        except Exception as e:
            logger.debug(f"Sync indicator update error: {e}")

    def _apply_sync_status(self, hq_online: bool, pending: int, last_sync: str):
        """Update the bottom-bar sync indicator. Always runs on the main
        thread (direct call from layers 1/2, or via _sync_check_result for
        the background-thread HTTP ping in layer 3)."""
        try:
            def _fmt_time(iso_str):
                if not iso_str:
                    return ''
                try:
                    from datetime import datetime as _dt
                    return _dt.fromisoformat(iso_str).strftime('%H:%M')
                except Exception:
                    return ''

            last_sync_text = _fmt_time(last_sync)

            # No raw pending-change counts here — the number reported by the
            # sync agent is a transient in-flight batch size, not a stable
            # backlog total, so it reads as arbitrary/untrustworthy. Only the
            # qualitative state (syncing vs. idle) is shown.
            if hq_online and pending:
                dot, color, bg, border = '🔄', '#60a5fa', 'rgba(96,165,250,0.12)', 'rgba(96,165,250,0.35)'
                label_text = f"{dot} Syncing"
                tip = "HQ server: connected\nUploading local changes…"
            elif hq_online:
                dot, color, bg, border = '🟢', '#22c55e', 'rgba(34,197,94,0.12)', 'rgba(34,197,94,0.35)'
                label_text = f"{dot} Online"
                tip = f"HQ server: connected\nLast sync: {last_sync_text or 'unknown'}"
            else:
                dot, color, bg, border = '⚫', '#ef4444', 'rgba(239,68,68,0.10)', 'rgba(239,68,68,0.25)'
                label_text = f"{dot} HQ Offline"
                tip = "HQ server: unreachable\nWill sync automatically when reconnected"

            self.sync_online_label.setText(label_text)
            self.sync_online_label.setStyleSheet(f"""
                QLabel {{
                    color: {color};
                    font-size: 10px;
                    font-weight: 500;
                    padding: 2px 8px;
                    background-color: {bg};
                    border-radius: 4px;
                    border: 1px solid {border};
                }}
            """)
            self.sync_online_label.setToolTip(tip)
        except Exception as e:
            logger.debug(f"Sync indicator apply error: {e}")

    def setup_new_window_handler(self):
        """Load target="_blank" / window.open() requests in the main view.

        QWebEngineView has no default window for these, so without this they
        are dropped without a trace and the link or button looks dead. There is
        one window in this app, so the request is handled in place; when it is
        an attachment, downloadRequested picks it up and nothing navigates.
        """
        try:
            self.web_view.page().newWindowRequested.connect(
                lambda request: self.web_view.setUrl(request.requestedUrl())
            )
            logger.info("✅ New-window handler configured")
        except AttributeError:
            # newWindowRequested landed in Qt 6.2; older builds keep the old
            # behaviour rather than crashing at startup.
            logger.warning("newWindowRequested unavailable; _blank links will not open")

    def setup_download_handler(self):
        """Set up download handling for the web view"""
        from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
        from PySide6.QtWidgets import QFileDialog

        profile = self.web_view.page().profile()

        def on_download_requested(download: QWebEngineDownloadRequest):
            """Handle download requests"""
            try:
                suggested_filename = download.downloadFileName()

                logger.info(f"Download requested: {suggested_filename}")

                download_dir = Path.home() / 'Downloads'
                save_path, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save File",
                    str(download_dir / suggested_filename),
                    "All Files (*.*)"
                )

                if save_path:
                    download.setDownloadDirectory(str(Path(save_path).parent))
                    download.setDownloadFileName(Path(save_path).name)
                    download.accept()

                    logger.info(f"✅ Download accepted: {save_path}")

                    self.status_label.setText(f"⬇️ Downloading {Path(save_path).name}...")

                    def on_progress(bytes_received, bytes_total):
                        if bytes_total > 0:
                            progress = int((bytes_received / bytes_total) * 100)
                            self.status_label.setText(
                                f"⬇️ {Path(save_path).name} - {progress}%"
                            )

                    def on_finished():
                        self.status_label.setText("✅ Download complete")
                        logger.info(f"✅ Download complete: {save_path}")

                        QTimer.singleShot(3000, lambda: self.status_label.setText("Ready"))

                        QMessageBox.information(
                            self,
                            "Download Complete",
                            f"File saved to:\n{save_path}",
                            QMessageBox.Ok
                        )

                    download.receivedBytesChanged.connect(on_progress)
                    download.isFinishedChanged.connect(on_finished)

                else:
                    logger.info("Download cancelled by user")

            except Exception as e:
                logger.error(f"Download error: {e}")
                QMessageBox.warning(
                    self,
                    "Download Error",
                    f"Could not download file:\n\n{str(e)}",
                    QMessageBox.Ok
                )

        profile.downloadRequested.connect(on_download_requested)
        logger.info("✅ Download handler configured")

    def refresh_page(self):
        """Refresh the current page"""
        logger.info("Refreshing page...")
        self.web_view.reload()
        self.status_label.setText("🔄 Refreshing...")
        self.update_sync_online_indicator()

    def open_logs_directory(self):
        """Open logs directory in system file explorer"""
        import subprocess
        import platform

        logs_dir = DATA_PATH / 'logs'

        try:
            if platform.system() == 'Windows':
                os.startfile(logs_dir)
            elif platform.system() == 'Darwin':  # macOS
                subprocess.Popen(['open', str(logs_dir)])
            else:  # Linux
                subprocess.Popen(['xdg-open', str(logs_dir)])

            logger.info(f"Opened logs directory: {logs_dir}")
        except Exception as e:
            logger.error(f"Could not open logs directory: {e}")
            QMessageBox.warning(
                self,
                "Cannot Open Logs",
                f"Could not open logs directory:\n{logs_dir}\n\nError: {str(e)}",
                QMessageBox.Ok
            )

    def on_load_started(self):
        """Handle page load start"""
        self.is_loading = True
        self.status_label.setText("⏳ Loading...")

    def on_load_progress(self, progress):
        """Handle page load progress"""
        if self.is_loading:
            self.status_label.setText(f"⏳ Loading... {progress}%")

    def on_load_finished(self, success):
        """Handle page load finished"""
        self.is_loading = False

        if success:
            self.status_label.setText("✅ Ready")

            # URL display removed (internal-only)

            # Enable buttons
            self.refresh_btn.setEnabled(True)

            current_url = self.web_view.url().toString()
            logger.debug(f"Page loaded: {current_url}")
        else:
            self.status_label.setText("❌ Load failed")
            logger.warning("Page failed to load")

    def closeEvent(self, event):
        """Handle window close — confirmation dialog before shutdown."""
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox(self)
        reply.setWindowTitle("Close Cirqen")
        reply.setText("Are you sure you want to close the app?")
        reply.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        reply.setDefaultButton(QMessageBox.No)
        reply.setStyleSheet("""
            QMessageBox {
                background-color: #111111;
                color: #e8e8e8;
                font-family: 'Segoe UI';
                font-size: 13px;
            }
            QMessageBox QLabel {
                color: #e8e8e8;
                font-size: 13px;
                font-family: 'Segoe UI';
            }
            QPushButton {
                background-color: #1e1e1e;
                color: #c0c0c0;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                padding: 8px 24px;
                font-family: 'Segoe UI';
                font-size: 13px;
                min-width: 80px;
            }
            QPushButton:hover { background-color: #252525; }
            QPushButton[text="Yes"] {
                background-color: #c0c0c0;
                color: #0a0a0a;
                font-weight: bold;
                border: none;
            }
            QPushButton[text="Yes"]:hover { background-color: #e0e0e0; }
        """)

        if reply.exec() != QMessageBox.Yes:
            event.ignore()
            return

        logger.info("User confirmed exit")
        event.accept()
        # Stop timers before exit
        try:
            self.sync_status_timer.stop()
        except Exception:
            pass
        if self._update_service:
            try:
                self._update_service.stop(timeout=2)
            except Exception:
                pass
        QApplication.quit()
