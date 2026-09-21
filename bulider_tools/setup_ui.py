# Auto-generated refactor of the original Cirqen main.py setup dialogs and startup cleanup layer.
from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QLinearGradient, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from .runtime import *

def _themed_dialog(kind: str, title: str, body: str, detail: str = "") -> None:
    """
    Show a frameless on-theme modal.
    kind: "info" | "warn" | "error"
    """
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont

    ICONS  = {"info": "ℹ️", "warn": "⚠️", "error": "❌"}
    COLORS = {"info": "#c0c0c0",  "warn": "#e0a040",    "error": "#d04040"}
    icon_ch = ICONS.get(kind, "ℹ️")
    accent  = COLORS.get(kind, "#c0c0c0")

    dlg = QDialog()
    dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
    dlg.setFixedSize(520, 260 if not detail else 300)
    dlg.setStyleSheet(f"""
        QDialog {{
            background-color: #111111;
            border: 1px solid #2a2a2a;
            border-radius: 12px;
        }}
        QLabel#icon  {{ color: {accent}; font-size: 32px; }}
        QLabel#title {{ color: #e8e8e8; font-size: 16px; font-weight: bold;
                        font-family: 'Segoe UI'; }}
        QLabel#body  {{ color: #909090; font-size: 12px; font-family: 'Segoe UI'; }}
        QLabel#detail{{ color: #555555; font-size: 10px; font-family: 'Segoe UI'; }}
        QPushButton  {{
            font-family: 'Segoe UI'; font-size: 13px; border-radius: 6px;
            padding: 10px 36px; border: none;
            background-color: {accent}; color: #0a0a0a; font-weight: bold;
        }}
        QPushButton:hover {{ background-color: #e0e0e0; }}
    """)

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(40, 32, 40, 28)
    lay.setSpacing(8)

    lbl_icon = QLabel(icon_ch)
    lbl_icon.setObjectName("icon")
    lbl_icon.setAlignment(Qt.AlignCenter)
    lay.addWidget(lbl_icon)

    lbl_title = QLabel(title)
    lbl_title.setObjectName("title")
    lbl_title.setAlignment(Qt.AlignCenter)
    lay.addWidget(lbl_title)

    lbl_body = QLabel(body)
    lbl_body.setObjectName("body")
    lbl_body.setAlignment(Qt.AlignCenter)
    lbl_body.setWordWrap(True)
    lay.addWidget(lbl_body)

    if detail:
        lbl_detail = QLabel(detail)
        lbl_detail.setObjectName("detail")
        lbl_detail.setAlignment(Qt.AlignCenter)
        lbl_detail.setWordWrap(True)
        lay.addWidget(lbl_detail)

    lay.addSpacing(10)
    btn = QPushButton("OK")
    btn.clicked.connect(dlg.accept)
    lay.addWidget(btn, alignment=Qt.AlignCenter)

    dlg.exec()

class SetupDialog(QDialog):
    """
    First-run setup dialog — dark silver theme, spinner, friendly copy.
    """

    # Friendly message map (same pattern as splash)
    _MSG_MAP = {
        "checking postgresql":           "Checking your database…",
        "initializ":                     "Preparing your workspace…",
        "configuring":                   "Configuring settings…",
        "starting postgresql":           "Starting your database…",
        "creating application database": "Building your data store…",
        "setting up database schema":    "Organising your data…",
        "creating hod user":             "Setting up your account…",
        "installing postgresql service": "Installing background service…",
        "finaliz":                       "Almost ready…",
        "setup complete":                "All done!",
    }

    W, H = 640, 420

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("")
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFixedSize(self.W, self.H)
        self.setStyleSheet("QDialog { background-color: #0a0a0a; border: 1px solid #2a2a2a; border-radius: 14px; }")

        self._spin_angle = 0
        self._pct        = 0
        self._status_msg = "Getting things ready…"
        self._log_lines  = []

        # Canvas label — everything is hand-drawn
        self._canvas = QLabel(self)
        self._canvas.setGeometry(0, 0, self.W, self.H)

        # Spinner timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(25)
        self._render()

    def _friendly(self, raw: str) -> str:
        key = raw.lower()
        for pat, nice in self._MSG_MAP.items():
            if pat in key:
                return nice
        return raw

    def _tick(self):
        self._spin_angle = (self._spin_angle + 6) % 360
        self._render()

    def _render(self):
        from PySide6.QtGui import QPainter, QColor, QFont, QLinearGradient, QPen, QPixmap
        from PySide6.QtCore import Qt

        px = QPixmap(self.W, self.H)
        px.fill(QColor("#0a0a0a"))
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        # Gradient bg
        g = QLinearGradient(0, 0, self.W, self.H)
        g.setColorAt(0, QColor("#0a0a0a"))
        g.setColorAt(1, QColor("#0f0f0f"))
        p.fillRect(0, 0, self.W, self.H, g)

        # Top accent
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawLine(0, 0, self.W, 0)

        cx = self.W // 2

        # Spinner ring
        r = 36
        p.setPen(QPen(QColor("#1e1e1e"), 5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(cx - r, 28, r * 2, r * 2)

        arc_p = QPen(QColor("#c0c0c0"), 5, Qt.SolidLine, Qt.RoundCap)
        p.setPen(arc_p)
        span  = 110 * 16
        start = (-(self._spin_angle + 90)) * 16
        p.drawArc(cx - r, 28, r * 2, r * 2, start, span)

        # "C" inside spinner
        font = QFont("Segoe UI", 18, QFont.Bold)
        p.setFont(font)
        p.setPen(QColor("#c0c0c0"))
        p.drawText(cx - r, 28, r * 2, r * 2, Qt.AlignCenter, "C")

        # Title
        font = QFont("Segoe UI", 15, QFont.Bold)
        p.setFont(font)
        p.setPen(QColor("#e8e8e8"))
        p.drawText(0, 112, self.W, 30, Qt.AlignCenter, "Setting up Cirqen")

        # Subtitle
        font = QFont("Segoe UI", 10)
        p.setFont(font)
        p.setPen(QColor("#606060"))
        p.drawText(0, 142, self.W, 22, Qt.AlignCenter,
                   "This only happens once — we’re building your workspace")

        # Thin accent line
        ag = QLinearGradient(100, 0, self.W - 100, 0)
        ag.setColorAt(0,   QColor(192, 192, 192, 0))
        ag.setColorAt(0.5, QColor(192, 192, 192, 60))
        ag.setColorAt(1,   QColor(192, 192, 192, 0))
        p.setPen(Qt.NoPen); p.setBrush(ag)
        p.drawRect(100, 169, self.W - 200, 1)

        # Status message
        font = QFont("Segoe UI", 12)
        p.setFont(font)
        p.setPen(QColor("#d0d0d0"))
        p.drawText(0, 178, self.W, 28, Qt.AlignCenter, self._status_msg)

        # Progress bar track
        bar_x, bar_w, bar_y, bar_h = 60, self.W - 120, 218, 7
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1a1a1a"))
        p.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 4, 4)

        if self._pct > 0:
            filled = int(bar_w * self._pct / 100)
            fg = QLinearGradient(bar_x, 0, bar_x + filled, 0)
            fg.setColorAt(0, QColor("#c0c0c0"))
            fg.setColorAt(1, QColor("#e0e0e0"))
            p.setBrush(fg)
            p.drawRoundedRect(bar_x, bar_y, filled, bar_h, 4, 4)

        # Percentage
        font = QFont("Segoe UI", 9)
        p.setFont(font)
        p.setPen(QColor("#505050"))
        p.drawText(0, 230, self.W, 18, Qt.AlignCenter, f"{self._pct}%")

        # Log area (last 8 lines, subtle)
        log_y = 258
        p.setFont(QFont("Segoe UI", 8))
        for i, line in enumerate(self._log_lines[-8:]):
            alpha = 40 + int(180 * (i + 1) / 8)
            p.setPen(QColor(160, 160, 160, alpha))
            p.drawText(60, log_y + i * 16, self.W - 120, 16,
                       Qt.AlignLeft | Qt.AlignVCenter,
                       line[:72])

        p.end()
        self._canvas.setPixmap(px)

    def update_progress(self, message: str, percent: int):
        self._status_msg = self._friendly(message)
        self._pct        = percent
        self._render()

    def add_log(self, message: str):
        # Strip emoji / technical prefixes — keep it clean
        clean = message.strip()
        for pfx in ("✅ ", "❌ ", "⚠️ ", "📦 ", "🔧 ",
                    "✔ ", "✘ ", "[INFO]", "[WARNING]", "[ERROR]"):
            clean = clean.replace(pfx, "")
        if clean:
            self._log_lines.append(clean)
        self._render()



class SetupThread(QThread):
    """Thread for first-run setup"""

    def __init__(self, setup_manager):
        super().__init__()
        self.setup_manager = setup_manager

    def run(self):
        self.setup_manager.run_setup()



def perform_startup_cleanup():
    """
    Automatic cleanup before app starts - kills stale processes and cleans locks
    This ensures users can restart the app immediately without errors
    """
    logger.info("="*70)
    logger.info("AUTOMATIC STARTUP CLEANUP")
    logger.info("="*70)

    cleanup_actions = []

    # 1. Clean stale lock file
    try:
        lock_file = DATA_PATH / 'cirqen.lock'
        if lock_file.exists():
            try:
                lock_data = json.loads(lock_file.read_text())
                pid = lock_data.get('pid')

                if pid and psutil.pid_exists(pid):
                    try:
                        proc = psutil.Process(pid)
                        # Check if it's really Cirqen or just a stale PID
                        if 'cirqen' in proc.name().lower() or 'python' in proc.name().lower():
                            # Try gentle termination first
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                                cleanup_actions.append(f"✓ Terminated stale Cirqen process (PID: {pid})")
                            except psutil.TimeoutExpired:
                                proc.kill()
                                proc.wait()
                                cleanup_actions.append(f"✓ Force killed stale Cirqen process (PID: {pid})")
                    except psutil.NoSuchProcess:
                        pass

                lock_file.unlink()
                cleanup_actions.append("✓ Removed stale lock file")

            except Exception as e:
                logger.debug(f"Lock file cleanup: {e}")
                try:
                    lock_file.unlink()
                    cleanup_actions.append("✓ Removed corrupted lock file")
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Lock cleanup error: {e}")

    # 2. Kill stale PostgreSQL processes
    # IMPORTANT: Never kill a process that is managed by the cirqen-postgres
    # systemd service — it would be restarted by systemd immediately anyway,
    # and killing it mid-startup causes the next startup to see the port as
    # briefly unavailable, making the pg-reuse logic unreliable.
    try:
        _pg_svc_active = False
        try:
            import subprocess as _sp
            _r = _sp.run(['systemctl', 'is-active', '--quiet', 'cirqen-postgres'],
                         timeout=3, check=False)
            _pg_svc_active = (_r.returncode == 0)
        except Exception:
            pass

        if _pg_svc_active:
            logger.info("[cleanup] cirqen-postgres systemd service is active — skipping postgres kill.")
        else:
            pg_killed = False
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    proc_name = proc.info['name'].lower()
                    cmdline = ' '.join(proc.info['cmdline'] or []).lower()
                    if 'postgres' in proc_name and str(DATA_PATH) in cmdline:
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except psutil.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        pg_killed = True
                        cleanup_actions.append(f"✓ Killed stale PostgreSQL (PID: {proc.info['pid']})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Only clean PID file if postgres is NOT systemd-managed
            pg_data = DATA_PATH / 'postgres'
            postmaster_pid = pg_data / 'postmaster.pid'
            if postmaster_pid.exists() and not _pg_svc_active:
                postmaster_pid.unlink()
                cleanup_actions.append("✓ Removed stale PostgreSQL postmaster.pid")

    except Exception as e:
        logger.debug(f"PostgreSQL cleanup error: {e}")

    # 3. Kill stale Redis processes
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                proc_name = proc.info['name'].lower()
                cmdline = ' '.join(proc.info['cmdline'] or []).lower()

                if 'redis' in proc_name and str(DATA_PATH) in cmdline:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    cleanup_actions.append(f"✓ Killed stale Redis (PID: {proc.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        logger.debug(f"Redis cleanup error: {e}")

    # 4. Kill stale Django/Celery processes
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] or []).lower()

                # Check for Django runserver or Celery worker
                if ('runserver' in cmdline or 'celery' in cmdline) and str(APPLICATION_PATH) in cmdline:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    cleanup_actions.append(f"✓ Killed stale Django/Celery (PID: {proc.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        logger.debug(f"Django/Celery cleanup error: {e}")

    # 5. Free up ports by killing processes using them
    # PostgreSQL ports (2215, 5432) are excluded when the systemd service is
    # active — killing them would disrupt a perfectly healthy managed instance.
    try:
        _pg_ports_protected = set()
        try:
            import subprocess as _sp2
            _r2 = _sp2.run(['systemctl', 'is-active', '--quiet', 'cirqen-postgres'],
                           timeout=3, check=False)
            if _r2.returncode == 0:
                _pg_ports_protected = {2215, 5432}
                logger.info("[cleanup] Protecting postgres ports 2215/5432 (systemd-managed).")
        except Exception:
            pass

        default_ports = [7788, 8000, 59999]   # postgres ports handled separately above
        for port in default_ports:
            if port in _pg_ports_protected:
                continue
            for proc in psutil.process_iter(['pid', 'name', 'connections']):
                try:
                    for conn in proc.connections():
                        if conn.laddr.port == port and proc.pid != os.getpid():
                            proc_name = (proc.name() or '').lower()
                            if 'postgres' in proc_name and port in _pg_ports_protected:
                                break   # Never kill a postgres process on a protected port
                            proc.terminate()
                            try:
                                proc.wait(timeout=2)
                            except psutil.TimeoutExpired:
                                proc.kill()
                                proc.wait()
                            cleanup_actions.append(f"✓ Freed port {port} (killed PID: {proc.pid})")
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
    except Exception as e:
        logger.debug(f"Port cleanup error: {e}")

    # 6. Clean session file
    try:
        session_file = DATA_PATH / 'session.json'
        if session_file.exists():
            session_file.unlink()
            cleanup_actions.append("✓ Removed stale session file")
    except Exception as e:
        logger.debug(f"Session cleanup error: {e}")

    # 7. Clear stale update_ready flag from update_status.json
    # If the app has just restarted after an update, the status file will
    # still have update_ready=True from the previous session.  We clear it
    # here so the bottom bar doesn't keep showing "restart to apply" forever.
    try:
        status_file = DATA_PATH / 'sync_state' / 'update_status.json'
        if status_file.exists():
            try:
                status_data = json.loads(status_file.read_text())
                if status_data.get('update_ready'):
                    status_data['update_ready'] = False
                    status_data['update_available'] = False
                    status_data['downloading'] = False
                    # Keep new_version so UI can show "updated to vX.Y.Z" if desired
                    status_file.write_text(json.dumps(status_data, indent=2))
                    cleanup_actions.append(
                        f"✓ Cleared stale update_ready flag "
                        f"(was waiting for v{status_data.get('new_version', '?')})"
                    )
            except Exception:
                pass  # Don't remove the file — update thread will rewrite it
    except Exception as e:
        logger.debug(f"Update status cleanup error: {e}")

    # 8. Delete .restart_required sentinel if version.txt matches the version
    # inside the sentinel — meaning the restart already happened successfully.
    try:
        if getattr(sys, 'frozen', False):
            _app_root = Path(sys.executable).parent / "_internal"
        else:
            _app_root = APPLICATION_PATH

        sentinel = _app_root / '.restart_required'
        if sentinel.exists():
            sentinel.unlink(missing_ok=True)
            cleanup_actions.append("✓ Removed .restart_required sentinel")
    except Exception as e:
        logger.debug(f"Sentinel cleanup error: {e}")

    # Log results
    if cleanup_actions:
        logger.info("Cleanup actions performed:")
        for action in cleanup_actions:
            logger.info(f"  {action}")
    else:
        logger.info("✓ No cleanup needed - system is clean")

    logger.info("="*70)

    return len(cleanup_actions)

