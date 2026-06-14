# Auto-generated refactor of the original Cirqen main.py UI layer.
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl
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
        self.sync_online_label = QLabel("⚫ HQ")
        self.sync_online_label.setStyleSheet("""
            QLabel {
                color: #606060;
                font-size: 10px;
                font-weight: 500;
                padding: 2px 8px;
                background-color: rgba(255,255,255,0.04);
                border-radius: 4px;
                border: 1px solid rgba(255,255,255,0.08);
            }
        """)
        self.sync_online_label.setToolTip("Sync agent: checking connection to HQ server…")
        bottom_layout.addWidget(self.sync_online_label)

        bottom_layout.addStretch()

        # Update Status + Restart button (no version badge shown to users)
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

        # Inline restart button — hidden until an update is ready
        self.restart_update_btn = QPushButton("↺ Restart")
        self.restart_update_btn.setFixedHeight(22)
        self.restart_update_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(34, 197, 94, 0.20);
                color: #22c55e;
                border: 1px solid rgba(34, 197, 94, 0.40);
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                padding: 2px 10px;
            }
            QPushButton:hover {
                background-color: rgba(34, 197, 94, 0.35);
                border-color: #22c55e;
            }
            QPushButton:pressed {
                background-color: rgba(34, 197, 94, 0.50);
            }
        """)
        self.restart_update_btn.setToolTip("Restart now to apply the downloaded update")
        self.restart_update_btn.setVisible(False)
        self.restart_update_btn.clicked.connect(
            lambda: self._show_restart_dialog(
                getattr(self, '_pending_update_version', '?')
            )
        )
        bottom_layout.addWidget(self.restart_update_btn)

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
        # Update status timer - check update manager status every 30 seconds
        self.update_status_timer = QTimer()
        self.update_status_timer.timeout.connect(self.update_update_status)
        self.update_status_timer.start(30000)  # 30 seconds

        # Sync online indicator timer - poll agent_status.json every 30 seconds
        self.sync_status_timer = QTimer()
        self.sync_status_timer.timeout.connect(self.update_sync_online_indicator)
        self.sync_status_timer.start(30000)  # 30 seconds

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

    def update_update_status(self):
        """
        Update the update status display in bottom bar.
        Reads from update_status.json written by the UpdateManager thread —
        same file-based pattern used by the sync agent indicator.
        """
        import json as _json

        def _set(text, color, tip=""):
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

        def _fmt_time(iso_str):
            if not iso_str:
                return ''
            try:
                from datetime import datetime as _dt
                return _dt.fromisoformat(iso_str).strftime('%H:%M')
            except Exception:
                return ''

        try:
            status_file = DATA_PATH / 'sync_state' / 'update_status.json'

            if not status_file.exists():
                _set("⚙️ Starting...", "#606060", "Update manager initialising…")
                self.restart_update_btn.setVisible(False)
                return

            status = _json.loads(status_file.read_text())

            server_available  = status.get('server_available', False)
            checking          = status.get('checking', False)
            downloading       = status.get('downloading', False)
            update_available  = status.get('update_available', False)
            update_ready      = status.get('update_ready', False)
            new_version       = status.get('new_version') or ''
            last_check        = _fmt_time(status.get('last_check', ''))
            progress          = status.get('download_progress', 0)
            error             = status.get('error') or ''
            last_check_text   = f"@ {last_check}" if last_check else ''

            if error:
                _set(
                    f"⚠️ Update failed",
                    "#f97316",
                    f"Update check failed: {error}\nLast checked: {last_check or 'recently'}"
                )
                self.restart_update_btn.setVisible(False)
            elif update_ready:
                _set(
                    f"✅ v{new_version} ready",
                    "#22c55e",
                    f"Update v{new_version} downloaded.\nClick Restart to apply."
                )
                self._pending_update_version = new_version
                self.restart_update_btn.setVisible(True)
                # Show the dialog once per version
                if not getattr(self, '_restart_dialog_shown_for', None) == new_version:
                    self._restart_dialog_shown_for = new_version
                    self._show_restart_dialog(new_version)
            elif downloading:
                prog_text = f" {progress}%" if progress else ""
                _set(
                    f"⬇️ Downloading update{prog_text}",
                    "#3b82f6",
                    f"Downloading v{new_version} from update server…\nDo not close the application."
                )
                self.restart_update_btn.setVisible(False)
            elif update_available and new_version:
                _set(
                    f"📦 v{new_version} available",
                    "#f59e0b",
                    f"Update v{new_version} is available.\nWill be downloaded automatically."
                )
                self.restart_update_btn.setVisible(False)
            elif checking:
                _set(
                    "⚙️ Checking updates…",
                    "#60a5fa",
                    "Checking for updates…"
                )
                self.restart_update_btn.setVisible(False)
            elif not server_available:
                _set(
                    f"⚠️ Server offline {last_check_text}".strip(),
                    "#f59e0b",
                    "Update server not reachable.\nWill retry automatically when connection is restored."
                )
                self.restart_update_btn.setVisible(False)
            else:
                _set(
                    f"✅ Up to date {last_check_text}".strip(),
                    "#22c55e",
                    f"Running latest version.\nLast checked: {last_check or 'recently'}"
                )
                self.restart_update_btn.setVisible(False)

        except Exception as e:
            logger.debug(f"Error reading update status: {e}")
            _set("⚙️ Checking…", "#606060", "Checking for updates…")

    def _show_restart_dialog(self, new_version: str):
        """
        Show a modal dialog asking the user to restart so the update takes effect.
        'Restart Now' re-executes the current process; 'Later' dismisses the dialog.
        The dialog is intentionally non-blocking (uses QTimer to defer so the main
        window finishes initialising before the dialog appears).
        """
        def _do_show():
            try:
                from PySide6.QtWidgets import QMessageBox, QPushButton
                from PySide6.QtCore import Qt

                dlg = QMessageBox(self)
                dlg.setWindowTitle("Update Ready")
                dlg.setIcon(QMessageBox.Icon.Information)
                dlg.setText(
                    f"<b>Cirqen v{new_version} has been downloaded and is ready to install.</b>"
                )
                dlg.setInformativeText(
                    "A restart is required to apply the update.\n\n"
                    "Click <b>Restart Now</b> to restart immediately, or "
                    "<b>Later</b> to continue and restart at your convenience."
                )
                dlg.setStandardButtons(QMessageBox.StandardButton.NoButton)

                restart_btn = dlg.addButton("Restart Now", QMessageBox.ButtonRole.AcceptRole)
                later_btn   = dlg.addButton("Later",        QMessageBox.ButtonRole.RejectRole)

                restart_btn.setStyleSheet(
                    "QPushButton { background-color: #22c55e; color: white; "
                    "font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
                    "QPushButton:hover { background-color: #16a34a; }"
                )
                later_btn.setStyleSheet(
                    "QPushButton { background-color: #374151; color: #d1d5db; "
                    "padding: 6px 18px; border-radius: 4px; }"
                    "QPushButton:hover { background-color: #4b5563; }"
                )

                dlg.setDefaultButton(restart_btn)
                dlg.exec()

                if dlg.clickedButton() == restart_btn:
                    logger.info(f"User requested restart to apply v{new_version}")
                    # Stop timers immediately so no more UI updates fire
                    try:
                        self.sync_status_timer.stop()
                        self.update_status_timer.stop()
                    except Exception:
                        pass

                    # Pre-emptively clear the update_ready flag and sentinel so
                    # that if the new process crashes before the update thread
                    # initialises, the banner doesn't reappear as a ghost.
                    try:
                        _status_file = DATA_PATH / 'sync_state' / 'update_status.json'
                        if _status_file.exists():
                            _sd = json.loads(_status_file.read_text())
                            _sd['update_ready'] = False
                            _sd['update_available'] = False
                            _sd['downloading'] = False
                            _status_file.write_text(json.dumps(_sd, indent=2))
                    except Exception:
                        pass
                    try:
                        if getattr(sys, 'frozen', False):
                            _ar = Path(sys.executable).parent / "_internal"
                        else:
                            _ar = APPLICATION_PATH
                        _sent = _ar / '.restart_required'
                        if _sent.exists():
                            _sent.unlink()
                    except Exception:
                        pass

                    def _do_restart():
                        # Stop services in background - use fast version to avoid blocking
                        try:
                            self.service_manager.stop_services_fast()
                        except Exception:
                            pass
                        # Spawn the new process
                        try:
                            restart_cmd, use_shell, cwd, env = get_restart_command()
                            subprocess.Popen(
                                restart_cmd,
                                cwd=cwd,
                                shell=use_shell,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True,
                            )
                        except Exception:
                            os.execv(sys.executable, [sys.executable] + sys.argv)
                        # Kill this process from background thread - bypasses Qt's exception catch
                        import time as _restart_time
                        _restart_time.sleep(1)
                        os.kill(os.getpid(), signal.SIGTERM)

                    threading.Thread(target=_do_restart, daemon=True).start()
                else:
                    logger.info("User chose to restart later")

            except Exception as _e:
                logger.warning(f"Could not show restart dialog: {_e}")

        # Defer by 2 seconds so the main window is fully visible first
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, _do_show)

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

        def _fmt_time(iso_str):
            if not iso_str:
                return ''
            try:
                from datetime import datetime as _dt
                return _dt.fromisoformat(iso_str).strftime('%H:%M')
            except Exception:
                return ''

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

        def _apply_indicator(hq_online, pending, last_sync):
            """Update UI — must run on main thread via QTimer.singleShot."""
            try:
                last_sync_text = _fmt_time(last_sync)
                if hq_online:
                    dot    = '🟢'
                    color  = '#22c55e'
                    bg     = 'rgba(34,197,94,0.12)'
                    border = 'rgba(34,197,94,0.35)'
                    label_text = f"{dot} Online"
                    if pending:
                        label_text = f"{dot} Syncing ({pending})"
                    tip = (
                        f"HQ server: connected\n"
                        f"Pending changes: {pending}\n"
                        f"Last sync: {last_sync_text or 'unknown'}"
                    )
                else:
                    dot    = '⚫'
                    color  = '#ef4444'
                    bg     = 'rgba(239,68,68,0.10)'
                    border = 'rgba(239,68,68,0.25)'
                    label_text = f"{dot} HQ Offline"
                    if pending:
                        label_text = f"{dot} Offline ({pending} pending)"
                    tip = (
                        f"HQ server: unreachable\n"
                        f"Pending changes: {pending}\n"
                        f"Will sync automatically when reconnected"
                    )

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
                _apply_indicator(hq_online, pending, last_sync)
            else:
                # ── Layer 3: HTTP ping — run in background thread ──────────
                hq_url = 'https://cirqen-hq.onrender.com'
                config_file = DATA_PATH / 'config.json'
                if config_file.exists():
                    cfg = _read_json(config_file)
                    hq_url = (
                        cfg.get('hq_server_url') or
                        cfg.get('server_url') or
                        cfg.get('hq_url') or
                        hq_url
                    )
                _pending  = pending
                _last     = last_sync

                def _bg_ping():
                    result = _ping_hq(hq_url)
                    QTimer.singleShot(0, lambda: _apply_indicator(result, _pending, _last))

                threading.Thread(target=_bg_ping, daemon=True).start()

        except Exception as e:
            logger.debug(f"Sync indicator update error: {e}")

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
            self.update_status_timer.stop()
        except Exception:
            pass
        QApplication.quit()


