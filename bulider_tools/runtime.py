# Auto-generated refactor of the original Cirqen main.py bootstrap/runtime layer.
#!/usr/bin/env python3
# ENHANCED WITH CODE DIRECTORY UPDATE SYSTEM
import os
import sys
import subprocess
import time
import signal
import atexit
import socket
import logging
import threading
import psutil
import json
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


try:
    from sync.update_manager import CodeUpdateManager
    UPDATE_SYSTEM_V2_AVAILABLE = True
except ImportError:
    logger.warning("update_manager- update system disabled")
    UPDATE_SYSTEM_V2_AVAILABLE = False
import asyncio
from sync.startup_warmup import StartupStateManager


# ============================
# UPDATE MANAGER - Global variable for cleanup
# ============================
update_manager_instance = None

# ============================
# CRITICAL: SUBPROCESS DETECTION - MUST BE FIRST
# ============================

def is_subprocess():
    """
    Detect if this is a subprocess started by the main Cirqen process
    Returns True ONLY if parent explicitly marked this as subprocess
    """
    # Check environment variables set by parent process
    if os.environ.get('CIRQEN_SUBPROCESS') == '1':
        return True

    if os.environ.get('CIRQEN_PARENT_PID'):
        parent_pid = int(os.environ.get('CIRQEN_PARENT_PID'))

        # Verify parent process exists and is actually Cirqen
        try:
            parent = psutil.Process(parent_pid)
            parent_name = parent.name().lower()

            # Check if parent is Cirqen
            if 'cirqen' in parent_name or 'python' in parent_name:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return False


def should_skip_instance_lock():
    """Check if this process should skip instance lock"""
    return (
        is_subprocess() or
        os.environ.get('CIRQEN_SKIP_INSTANCE_LOCK') == '1' or
        os.environ.get('CIRQEN_MIGRATION_MODE') == '1' or
        'migrate' in sys.argv or
        'makemigrations' in sys.argv or
        'createsuperuser' in sys.argv or
        'runserver' in sys.argv or
        'shell' in sys.argv
    )


# ============================
# SUBPROCESS MODE: Skip GUI but continue execution
# ============================
if is_subprocess():
    # SUBPROCESS MODE: This process is a child of main Cirqen
    # Skip GUI initialization but continue to run Django

    # Set up minimal logging for subprocess — rotating file only, no terminal output
    from logging.handlers import RotatingFileHandler as _RotFH
    _sub_log_dir = Path(os.environ.get('CIRQEN_DATA_PATH', str(Path.home() / '.cirqen'))) / 'logs'
    _sub_log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.WARNING,
        format='[SUBPROCESS] %(asctime)s - %(levelname)s - %(message)s',
        handlers=[_RotFH(str(_sub_log_dir / 'cirqen_subprocess.log'), maxBytes=5*1024*1024, backupCount=3)]
    )
    logger = logging.getLogger('CirqenSubprocess')
    logger.propagate = False

    logger.info("="*70)
    logger.info("SUBPROCESS MODE DETECTED")
    logger.info("="*70)
    logger.info(f"PID: {os.getpid()}")
    logger.info(f"Parent PID: {os.environ.get('CIRQEN_PARENT_PID', 'Not set')}")
    logger.info(f"Command: {' '.join(sys.argv)}")
    logger.info("="*70)

    if 'runserver' in sys.argv:
        logger.info("Will run Django server without GUI...")
    elif 'manage.py' in sys.argv[0]:
        logger.info("Running Django management command without GUI...")

    # CRITICAL: DO NOT EXIT HERE!
    # Just skip the GUI imports below by NOT importing them
    # Django/manage.py will continue to execute normally

    SKIP_GUI_IMPORTS = True
else:
    SKIP_GUI_IMPORTS = False

if not SKIP_GUI_IMPORTS:
    # GUI imports will happen below after paths are set up
    pass

# ============================================================================
# UNIVERSAL RESTART HELPER – works in dev, PyInstaller, and DEB modes
# ============================================================================

def get_restart_command():
    """
    Build the correct command-line to relaunch the application after an update.
    Detects the packaging mode and returns a list suitable for
    subprocess.Popen (shell or direct).

    Returns:
        tuple: (command_list, use_shell, cwd, env_overrides)
               command_list  – list of strings (or string when use_shell=True)
               use_shell     – bool, True when a shell is needed
               cwd           – Path or None
               env_overrides – dict or None

    Packaging modes detected:
        - DEB/installed        → launch re-initialisation wrapper (start_cirqen.sh)
        - PyInstaller onedir   → sys.executable directly (self-contained)
        - PyInstaller onefile  → sys.executable directly (self-extracting)
        - Source / dev         → python main.py
    """
    exe = sys.executable
    frozen = getattr(sys, 'frozen', False)
    bundle_dir = Path(exe).parent.absolute()

    # -- 1. DEB / system-installed ----------------------------------------
    # Look for the launcher wrapper that the .desktop file or Debian
    # package installs alongside the binary.
    # Typical layout after install:
    #   /opt/cirqen/start_cirqen.sh   (or   /usr/share/cirqen/start_cirqen.sh)
    #   /opt/cirqen/Cirqen
    #   /opt/cirqen/launch_cirqen.py
    possible_roots = []
    if frozen:
        possible_roots.append(bundle_dir)               # /opt/cirqen
    possible_roots.extend([
        bundle_dir,
        Path('/opt/cirqen'),
        Path('/usr/share/cirqen'),
        Path('/usr/local/share/cirqen'),
    ])

    for root in possible_roots:
        launcher = root / 'start_cirqen.sh'
        if launcher.is_file() and os.access(launcher, os.X_OK):
            # We found the DEB launcher wrapper.
            # Spawning it as a subprocess produces a NEW process tree that
            # re-initialises services (PostgreSQL, Redis, etc.) before
            # starting the actual binary, so the restart works identically
            # to a fresh start from the .desktop file.
            return (['bash', str(launcher)], False, str(root), None)

    # -- 2. PyInstaller (onedir or onefile) -------------------------------
    if frozen:
        # sys.executable is the PyInstaller bundle; restarting it directly
        # is safe (bootloader re-extracts in onefile mode).
        return ([exe], False, str(bundle_dir), None)

    # -- 3. Source / development ----------------------------------------
    # Re-launch with the same interpreter and script arguments.
    script = Path(__file__)
    if not script.is_file():
        script = Path(sys.argv[0])
    return ([exe, str(script)] + sys.argv[1:], False, str(script.parent), None)


# ============================================================================
# ENHANCED: Dynamic Port Configuration
# ============================


# ============================================================================
# CODE DIRECTORY UPDATE SYSTEM INTEGRATION
# ============================================================================

async def check_for_code_updates(data_path: Path, app_root: Path, current_version: str):
    """
    Check for code updates at startup (non-blocking, max 5 seconds)

    This function:
    1. Initializes the CodeUpdateManager
    2. Checks for updates from HQ server (non-blocking)
    3. Starts background update checking (every 60 minutes)
    4. Sets up callbacks for update notifications

    Args:
        data_path: User data directory (for staging, backups, version tracking)
        app_root: Application root (_internal/code directory)
        current_version: Current application version
    """
    global update_manager_instance

    if not UPDATE_SYSTEM_V2_AVAILABLE:
        logger.info("⚠️  Update system v2 not available - skipping update check")
        return

    try:
        logger.info("="*70)
        logger.info("CHECKING FOR CODE UPDATES")
        logger.info("="*70)

        # Get update server URL from environment or use default HQ server
        update_server_url = os.environ.get('UPDATE_SERVER_URL', 'https://cirqen-hq.onrender.com')
        update_enabled = os.environ.get('UPDATE_CHECK_ENABLED', 'true').lower() == 'true'
        update_interval = int(os.environ.get('UPDATE_CHECK_INTERVAL_MINUTES', '60'))

        if not update_enabled:
            logger.info("Update checking is disabled via UPDATE_CHECK_ENABLED")
            return

        logger.info(f"Server URL: {update_server_url}")
        logger.info(f"Check Interval: {update_interval} minutes")
        logger.info(f"Current Version: {current_version}")
        logger.info(f"Code Directory: {app_root}")

        # Initialize update manager
        update_manager_instance = CodeUpdateManager(
            server_url=update_server_url,
            app_root=app_root,
            data_dir=data_path,
            current_version=current_version,
            code_subdir="",  # app_root already points to code directory
            check_interval_minutes=update_interval,
            max_startup_wait_seconds=5,  # Won't delay startup
            enable_background_checking=True
        )

        # Set up callbacks for update events
        def on_update_available(update_info):
            """Called when an update is available"""
            logger.info("="*70)
            logger.info("🆕 CODE UPDATE AVAILABLE!")
            logger.info("="*70)
            logger.info(f"   Current Version: {update_info.get('current_version', 'Unknown')}")
            logger.info(f"   New Version: {update_info.get('new_version', 'Unknown')}")
            logger.info(f"   Total Changes: {update_info.get('changes_count', 0)} files")
            logger.info(f"   • New Files: {len(update_info.get('new_files', []))}")
            logger.info(f"   • Modified Files: {len(update_info.get('modified_files', []))}")
            logger.info(f"   • Deleted Files: {len(update_info.get('deleted_files', []))}")
            logger.info("="*70)
            logger.info("Update will be downloaded and applied in background")
            logger.info("Application restart may be required after update completes")
            logger.info("="*70)

            # TODO: You can add UI notification here
            # For example, update a status indicator or show a message box

        def on_server_reconnect():
            """Called when update server comes back online after being offline"""
            logger.info("="*70)
            logger.info("✅ UPDATE SERVER RECONNECTED")
            logger.info("="*70)
            logger.info("Will check for updates shortly...")

        # Register callbacks
        update_manager_instance.on_update_available = on_update_available
        update_manager_instance.on_server_reconnect = on_server_reconnect

        # Check at startup (non-blocking, max 5 seconds)
        logger.info("Performing startup update check (max 5 seconds)...")
        update_info = await update_manager_instance.check_at_startup()

        if update_info:
            logger.info("✅ Startup update check complete")
        else:
            logger.info("⚠️  Update server not available (will retry in background)")

        # Start background checking (every 60 minutes)
        await update_manager_instance.start_background_checking()

        logger.info("✅ Update system initialized successfully")
        logger.info("="*70)

    except ImportError as e:
        logger.warning("="*70)
        logger.warning("⚠️  UPDATE SYSTEM NOT AVAILABLE")
        logger.warning("="*70)
        logger.warning(f"Error: {e}")
        logger.warning("To enable updates:")
        logger.warning("1. Ensure update_manager_v2.py is in project directory")
        logger.warning("2. Ensure update_client_v2.py is in project directory")
        logger.warning("3. Rebuild application with build_cirqen_FULL_ENHANCED.py")
        logger.warning("="*70)
        logger.info("Application will continue without update system")

    except Exception as e:
        logger.warning("="*70)
        logger.warning("⚠️  COULD NOT INITIALIZE UPDATE SYSTEM")
        logger.warning("="*70)
        logger.warning(f"Error: {e}")
        logger.warning("Application will continue without updates")
        logger.warning("="*70)
        import traceback
        logger.debug(traceback.format_exc())


class PortManager:
    """
    ENHANCED: Manages dynamic port allocation
    Automatically finds free ports if defaults are in use
    """

    DEFAULT_PORTS = {
        'postgresql_local': 2215,
        'postgresql_hq': 5432,
        'redis': 7788,
        'django': 8000,
        'instance_lock': 59999
    }

    PORT_RANGES = {
        'postgresql_local': (2215, 2250),
        'postgresql_hq': (5442, 6442),
        'redis': (7788, 7820),
        'django': (8000, 8050),
        'instance_lock': (59999, 60050)
    }
    def __init__(self, session_file: Path):
        self.session_file = session_file
        self.ports = self.DEFAULT_PORTS.copy()
        self.logger = logging.getLogger('PortManager')

    def is_port_free(self, port: int) -> bool:
        """Check if a port is available"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('127.0.0.1', port))
                return True
        except OSError:
            return False

    def _port_owner_is_postgres(self, port: int) -> bool:
        """
        Return True if a postgres process is listening on *port*.

        Uses three approaches in order so it works without root privileges:
          1. /proc/net/tcp + /proc/net/tcp6  (Linux, no privileges needed)
          2. systemctl is-active cirqen-postgres  (our own service)
          3. psutil connections  (fallback, may need privileges)
        """
        # --- Approach 1: /proc/net/tcp (works as any user on Linux) ---
        try:
            hex_port = format(port, '04X')
            for tcp_path in ('/proc/net/tcp', '/proc/net/tcp6'):
                try:
                    for line in open(tcp_path).readlines()[1:]:
                        parts = line.split()
                        if len(parts) > 5 and parts[3] == '0A':   # 0A = LISTEN
                            _, lport_hex = parts[1].split(':')
                            if lport_hex.upper() == hex_port:
                                # Something is listening.  Now confirm it is postgres
                                # by checking the owning UID's processes via /proc.
                                inode = parts[9]
                                try:
                                    import glob
                                    for fd in glob.glob(f'/proc/*/fd/*'):
                                        try:
                                            if str(os.stat(fd).st_ino) == inode:
                                                pid = int(fd.split('/')[2])
                                                name = open(f'/proc/{pid}/comm').read().strip()
                                                if 'postgres' in name.lower():
                                                    return True
                                        except Exception:
                                            continue
                                except Exception:
                                    # inode lookup failed but port is in LISTEN state —
                                    # optimistically assume postgres (don't kill it)
                                    return True
                except (FileNotFoundError, PermissionError):
                    pass
        except Exception:
            pass

        # --- Approach 2: our own systemd service ---
        try:
            r = subprocess.run(
                ['systemctl', 'is-active', 'cirqen-postgres'],
                capture_output=True, text=True, timeout=3
            )
            if r.stdout.strip() == 'active':
                return True
        except Exception:
            pass

        # --- Approach 3: psutil (may silently fail without root) ---
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    for conn in proc.connections(kind='tcp'):
                        if conn.laddr.port == port and conn.status == 'LISTEN':
                            if 'postgres' in (proc.info.get('name') or '').lower():
                                return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass

        return False


    def find_free_port(self, service: str) -> int:
        """Find a free port for the service within its range.

        If the default port is in use by a postgres process (e.g. a systemd-managed
        instance) we reuse that port rather than allocating a new one.
        """
        start_port, end_port = self.PORT_RANGES[service]
        default_port = self.DEFAULT_PORTS[service]

        # Try default first
        if self.is_port_free(default_port):
            self.logger.info(f"[port] {service}: Using default port {default_port}")
            return default_port

        # Default port is busy - if it's already postgres, reuse it (don't fight it)
        if service in ('postgresql_local', 'postgresql_hq') and self._port_owner_is_postgres(default_port):
            self.logger.info(
                f"[port] {service}: Port {default_port} already owned by postgres - reusing."
            )
            return default_port

        # Find an alternative port in range
        self.logger.warning(f"[port] {service}: Port {default_port} in use, searching for alternative...")
        for port in range(start_port, end_port + 1):
            if self.is_port_free(port):
                self.logger.info(f"[port] {service}: Found free port {port}")
                return port

        raise RuntimeError(f"No free ports available for {service} in range {start_port}-{end_port}")

    def allocate_ports(self) -> dict:
        """Allocate all ports dynamically"""
        self.logger.info("="*70)
        self.logger.info("DYNAMIC PORT ALLOCATION")
        self.logger.info("="*70)

        allocated_ports = {}

        for service in self.DEFAULT_PORTS.keys():
            try:
                port = self.find_free_port(service)
                allocated_ports[service] = port
            except RuntimeError as e:
                self.logger.error(f"âŒ Failed to allocate port for {service}: {e}")
                raise

        self.ports = allocated_ports

        # Save session
        self.save_session()

        self.logger.info("="*70)
        self.logger.info("PORT ALLOCATION COMPLETE")
        for service, port in allocated_ports.items():
            is_default = port == self.DEFAULT_PORTS[service]
            status = "DEFAULT" if is_default else "DYNAMIC"
            self.logger.info(f"  {service:20s}: {port:5d} [{status}]")
        self.logger.info("="*70)

        return allocated_ports

    def save_session(self):
        """Save current port session to file"""
        try:
            session_data = {
                'ports': self.ports,
                'pid': os.getpid(),
                'timestamp': datetime.now().isoformat(),
                'platform': sys.platform
            }
            self.session_file.write_text(json.dumps(session_data, indent=2))
            self.logger.info(f"ðŸ’¾ Session saved: {self.session_file}")
        except Exception as e:
            self.logger.warning(f"Could not save session: {e}")

    def load_session(self) -> bool:
        """Load previous session if valid"""
        try:
            if not self.session_file.exists():
                return False

            session_data = json.loads(self.session_file.read_text())

            # Check if PID still exists
            pid = session_data.get('pid')
            if pid and psutil.pid_exists(pid):
                self.logger.warning(f"Previous session still active (PID: {pid})")
                return False

            # Verify ports are still free
            old_ports = session_data.get('ports', {})
            all_free = all(self.is_port_free(port) for port in old_ports.values())

            if all_free:
                self.ports = old_ports
                self.logger.info("âœ… Loaded previous session ports")
                return True
            else:
                self.logger.info("Previous session ports no longer available")
                return False

        except Exception as e:
            self.logger.warning(f"Could not load session: {e}")
            return False

    def cleanup_session(self):
        """Clean up session file"""
        try:
            if self.session_file.exists():
                self.session_file.unlink()
                self.logger.info("ðŸ§¹ Session cleaned up")
        except Exception as e:
            self.logger.warning(f"Could not cleanup session: {e}")

    def get_port(self, service: str) -> int:
        """Get allocated port for service"""
        return self.ports.get(service, self.DEFAULT_PORTS[service])


# ============================
# Path Configuration
# ============================
if getattr(sys, 'frozen', False):
    BUNDLE_DIR = Path(sys.executable).parent.absolute()
    APPLICATION_PATH = Path(sys._MEIPASS)
    RUNTIME_DIR = BUNDLE_DIR / 'runtime'

    if sys.platform == 'win32':
        DATA_PATH = Path(os.getenv('APPDATA')) / 'cirqen'
    else:
        DATA_PATH = Path.home() / '.local/share/cirqen'
else:
    APPLICATION_PATH = Path(__file__).resolve().parents[1]
    RUNTIME_DIR = APPLICATION_PATH / 'runtime'
    DATA_PATH = APPLICATION_PATH / 'data'

# Create data directories
DATA_PATH.mkdir(parents=True, exist_ok=True)
(DATA_PATH / 'postgres').mkdir(exist_ok=True)
(DATA_PATH / 'redis').mkdir(exist_ok=True)
(DATA_PATH / 'logs').mkdir(exist_ok=True)
(DATA_PATH / 'media').mkdir(exist_ok=True)
(DATA_PATH / 'sync_state').mkdir(exist_ok=True)

# Set up logging — rotating file only, no terminal output
# Each log: 5 MB max, 3 backups kept (~20 MB cap per service)
from logging.handlers import RotatingFileHandler as _RotFH
LOG_FILE = DATA_PATH / 'logs' / 'cirqen_app.log'
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[_RotFH(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)]
)
# Strip any StreamHandlers so nothing ever reaches the terminal
logging.getLogger().handlers = [
    h for h in logging.getLogger().handlers
    if isinstance(h, logging.FileHandler)
]
logger = logging.getLogger('Cirqen')
logger.propagate = False

# Initialize port manager
SESSION_FILE = DATA_PATH / 'session.json'
port_manager = PortManager(SESSION_FILE)

# ============================================================
# POSTGRESQL SYSTEMD SERVICE MANAGER (BUILT-IN FIRST-RUN)
# On Linux, installs the embedded postgres binary as a
# persistent systemd service so it survives app restarts.
# Eliminates all stale postmaster.pid errors permanently.
# ============================================================

class PostgresSystemdManager:
    SERVICE_NAME = "cirqen-postgres"
    SERVICE_FILE = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")

    def __init__(self, port: int, pg_binary: Path, pg_data: Path, pg_lib: Path):
        self.port      = port
        self.pg_binary = pg_binary
        self.pg_data   = pg_data
        self.pg_lib    = pg_lib
        self.log_file  = DATA_PATH / 'logs' / 'postgres.log'
        self._log      = logging.getLogger('PostgresSystemd')
        try:
            self._user = os.getlogin()
        except OSError:
            import pwd
            self._user = pwd.getpwuid(os.getuid()).pw_name

    @property
    def is_installed(self) -> bool:
        return self.SERVICE_FILE.exists()

    @property
    def is_active(self) -> bool:
        try:
            r = subprocess.run(['systemctl', 'is-active', self.SERVICE_NAME],
                               capture_output=True, text=True)
            return r.stdout.strip() == 'active'
        except FileNotFoundError:
            return False

    def is_accepting_connections(self) -> bool:
        """Return True if PostgreSQL is up and accepting TCP connections on self.port."""
        # Quick TCP probe first — cheapest check
        try:
            with socket.create_connection(('127.0.0.1', self.port), timeout=3):
                pass
        except OSError:
            return False

        # TCP port is open — confirm it is actually PostgreSQL
        try:
            import psycopg2
            for user in ['postgres', self._user]:
                conn = None
                try:
                    conn = psycopg2.connect(
                        host='127.0.0.1', port=self.port,
                        database='postgres', user=user,
                        connect_timeout=5
                    )
                    return True
                except psycopg2.OperationalError as exc:
                    err = str(exc)
                    # Server is up but wrong user/db — still counts as accepting connections
                    if 'does not exist' in err or 'authentication failed' in err:
                        return True
                    # Any other error (role missing, pg_hba, etc.) — try next user
                    continue
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
        except ImportError:
            # psycopg2 not available — TCP open is good enough
            return True

        return False

    def install(self) -> bool:
        """Write unit file and enable+start service. Called once at first run."""
        if sys.platform != 'linux':
            return False
        if not self.pg_binary.exists():
            self._log.warning(f"Binary not found: {self.pg_binary}")
            return False

        self._log.info("=" * 60)
        self._log.info("INSTALLING CIRQEN-POSTGRES SYSTEMD SERVICE")
        self._log.info(f"  binary : {self.pg_binary}")
        self._log.info(f"  data   : {self.pg_data}")
        self._log.info(f"  port   : {self.port}")
        self._log.info(f"  user   : {self._user}")
        self._log.info("=" * 60)

        content = self._unit_file()
        written = self._write_direct(content) if os.geteuid() == 0 else self._write_sudo(content)
        if not written:
            self._log.warning("No root access — systemd service not installed.")
            return False
        try:
            subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
            subprocess.run(['systemctl', 'enable', self.SERVICE_NAME], check=True, capture_output=True)
            subprocess.run(['systemctl', 'start',  self.SERVICE_NAME], check=True, capture_output=True)
            self._log.info(f"Service '{self.SERVICE_NAME}' installed and started.")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            self._log.error(f"systemctl error: {e}")
            return False

    def port_owner_is_postgres(self) -> bool:
        """
        Return True when something listening on self.port looks like a postgres
        process.  Uses psutil when available, falls back to /proc/net/tcp on
        Linux, and returns True (optimistic / don't-kill) on any other failure.
        """
        # ---- psutil path (preferred) ----
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'connections']):
                try:
                    for conn in proc.connections(kind='tcp'):
                        if conn.laddr.port == self.port and conn.status in ('LISTEN', 'ESTABLISHED'):
                            if 'postgres' in (proc.info.get('name') or '').lower():
                                return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False
        except ImportError:
            pass

        # ---- /proc/net/tcp fallback (Linux only) ----
        try:
            hex_port = format(self.port, '04X')
            for path in ('/proc/net/tcp', '/proc/net/tcp6'):
                try:
                    for line in Path(path).read_text().splitlines()[1:]:
                        parts = line.split()
                        if len(parts) > 3 and parts[3] == '0A':   # LISTEN
                            _, lport = parts[1].split(':')
                            if lport.upper() == hex_port:
                                return True  # something is listening; assume postgres
                except FileNotFoundError:
                    pass
        except Exception:
            pass

        # Cannot inspect — optimistic: don't kill whatever is there
        return True

    def ensure(self, timeout: int = 30) -> bool:
        """
        Ensure postgres is running and accepting connections.

        Priority:
          1. systemd service installed AND active  → wait for it / reuse
          2. systemd service installed but inactive → start it, then wait
          3. Service file absent but port already owned by postgres → reuse
          4. Nothing running → return False so caller falls back to direct start
        """
        import time as _t

        # Fast path: already accepting connections (covers all cases)
        if self.is_accepting_connections():
            self._log.info("[PG] Already running via systemd.")
            return True

        # Case 1 & 2: service file present
        if self.is_installed:
            if not self.is_active:
                self._log.info(f"[PG] Service installed but inactive — starting {self.SERVICE_NAME}...")
                try:
                    subprocess.run(['systemctl', 'start', self.SERVICE_NAME],
                                   capture_output=True, check=False)
                except FileNotFoundError:
                    pass

            self._log.info(f"[PG] Waiting up to {timeout}s for port {self.port}...")
            end = _t.time() + timeout
            last_log = _t.time()
            while _t.time() < end:
                if self.is_accepting_connections():
                    self._log.info("[PG] PostgreSQL ready (systemd).")
                    return True
                _t.sleep(0.5)
                # Log progress every 10 seconds so it's clear we're still waiting
                now = _t.time()
                if now - last_log >= 10:
                    elapsed = int(now - (end - timeout))
                    remaining = int(end - now)
                    self._log.info(
                        f"[PG] Still waiting for PostgreSQL on port {self.port}... "
                        f"({elapsed}s elapsed, {remaining}s remaining)"
                    )
                    last_log = now
            self._log.error(f"[PG] Timeout after {timeout}s waiting for PostgreSQL on port {self.port}.")
            return False

        # Case 3: no service file, but the port is already owned by postgres
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            _s.settimeout(1)
            port_in_use = _s.connect_ex(('127.0.0.1', self.port)) == 0

        if port_in_use and self.port_owner_is_postgres():
            self._log.info(
                f"[PG] No systemd service file, but postgres is already listening "
                f"on port {self.port} — reusing existing instance."
            )
            return True

        # Case 4: nothing suitable running
        return False

    def stop(self):
        """Intentional no-op — leave PostgreSQL running after app closes."""
        if self.is_installed:
            self._log.info(f"[PG] Managed by systemd — staying alive after app exit. "
                           f"(sudo systemctl stop {self.SERVICE_NAME} to stop manually)")

    def _unit_file(self) -> str:
        return (
            "[Unit]\n"
            f"Description=Cirqen PostgreSQL (port {self.port})\n"
            "After=network.target\n"
            "Wants=network.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"User={self._user}\n"
            f"Environment=LD_LIBRARY_PATH={self.pg_lib}\n"
            # Remove stale postmaster.pid before each start to avoid startup failures
            f"ExecStartPre=/bin/bash -c 'rm -f {self.pg_data}/postmaster.pid'\n"
            # Explicitly pass -p PORT so postgres always binds to port 2215
            f"ExecStart={self.pg_binary} -D {self.pg_data} -p {self.port}\n"
            "Restart=on-failure\n"
            "RestartSec=5\n"
            "KillMode=process\n"
            "TimeoutStartSec=60\n"
            "TimeoutStopSec=30\n"
            f"StandardOutput=append:{self.log_file}\n"
            f"StandardError=append:{self.log_file}\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

    def _write_direct(self, content: str) -> bool:
        try:
            self.SERVICE_FILE.write_text(content)
            return True
        except PermissionError:
            return False

    def _write_sudo(self, content: str) -> bool:
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix='.service', text=True)
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(content)
            for cmd in (['sudo', '-n', 'cp', tmp, str(self.SERVICE_FILE)],
                        ['pkexec',      'cp', tmp, str(self.SERVICE_FILE)]):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    if r.returncode == 0:
                        self._log.info(f"Unit file written via {cmd[0]}.")
                        return True
                except Exception:
                    continue
            return False
        finally:
            try: os.unlink(tmp)
            except Exception: pass


# ============================
# ENHANCED: Single Instance Lock
# ============================
class SingleInstanceLock:
    """
    ENHANCED: Single instance enforcement with dynamic port support
    Uses both file lock and socket lock for reliability
    """

    def __init__(self, lock_file: Path, port_manager: PortManager):
        self.lock_file = lock_file
        self.port_manager = port_manager
        self.lock_socket = None
        self.acquired = False
        self.logger = logging.getLogger('InstanceLock')

    def acquire(self) -> tuple[bool, str]:
        """
        Acquire the instance lock
        Returns (success, message)
        """
        try:
            # Check and clean stale lock file
            if self.lock_file.exists():
                try:
                    lock_data = json.loads(self.lock_file.read_text())
                    pid = lock_data.get('pid')
                    lock_port = lock_data.get('lock_port')

                    if pid and psutil.pid_exists(pid):
                        try:
                            process = psutil.Process(pid)
                            process_name = process.name().lower()

                            # Check if it's actually Cirqen
                            if 'cirqen' in process_name or 'python' in process_name:
                                self.logger.error(f"Another Cirqen instance is running (PID: {pid}, Port: {lock_port})")
                                return False, f"Cirqen is already running (PID: {pid})\n\nUse cleanup script to force stop."
                            else:
                                self.logger.info(f"Stale lock from different process: {process_name}")
                                self.lock_file.unlink()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            self.logger.info(f"Stale lock file (PID {pid} inaccessible)")
                            self.lock_file.unlink()
                    else:
                        self.logger.info(f"Stale lock file (PID {pid} not running)")
                        self.lock_file.unlink()

                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    self.logger.warning(f"Invalid lock file format: {e}")
                    self.lock_file.unlink()

            # Acquire socket lock (PRIMARY LOCK)
            lock_port = self.port_manager.get_port('instance_lock')

            try:
                self.lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                self.lock_socket.bind(('127.0.0.1', lock_port))
                self.lock_socket.listen(1)
                self.logger.info(f"âœ… Acquired socket lock on port {lock_port}")
            except OSError as e:
                self.logger.error(f"Socket lock failed on port {lock_port}: {e}")
                return False, f"Another instance is using lock port {lock_port}\n\nUse cleanup script to force stop."

            # Write lock file (SECONDARY LOCK)
            try:
                lock_data = {
                    'pid': os.getpid(),
                    'lock_port': lock_port,
                    'timestamp': datetime.now().isoformat(),
                    'platform': sys.platform
                }
                self.lock_file.write_text(json.dumps(lock_data, indent=2))
                self.logger.info(f"âœ… Created lock file: {self.lock_file}")
            except Exception as e:
                self.logger.warning(f"Could not write lock file: {e}")

            self.acquired = True
            return True, "Lock acquired successfully"

        except Exception as e:
            self.logger.error(f"Failed to acquire lock: {e}")
            self.release()
            return False, f"Lock acquisition failed: {str(e)}"

    def release(self):
        """Release the lock"""
        if not self.acquired:
            return

        released_items = []

        # Release lock file
        try:
            if self.lock_file.exists():
                try:
                    lock_data = json.loads(self.lock_file.read_text())
                    if lock_data.get('pid') == os.getpid():
                        self.lock_file.unlink()
                        released_items.append("lock file")
                except:
                    self.lock_file.unlink()
                    released_items.append("lock file")
        except Exception as e:
            self.logger.warning(f"Error releasing lock file: {e}")

        # Release socket
        try:
            if self.lock_socket:
                self.lock_socket.close()
                released_items.append("socket lock")
        except Exception as e:
            self.logger.warning(f"Error releasing socket lock: {e}")

        self.acquired = False

        if released_items:
            self.logger.info(f"âœ… Released: {', '.join(released_items)}")

    def __del__(self):
        """Ensure cleanup on object destruction"""
        self.release()


# ============================
# ENHANCED: Port Process Killer
# ============================
def kill_process_on_port(port: int, force: bool = False) -> bool:
    """
    ENHANCED: Kill process using a port with better detection
    """
    try:
        killed_any = False
        for proc in psutil.process_iter(['pid', 'name', 'connections']):
            try:
                for conn in proc.connections():
                    if conn.laddr.port == port:
                        proc_name = proc.info['name']
                        proc_pid = proc.info['pid']

                        # Don't kill ourselves
                        if proc_pid == os.getpid():
                            continue

                        # Only kill if force or it's a known Cirqen process
                        if force or any(name in proc_name.lower() for name in ['postgres', 'redis', 'python', 'celery']):
                            logger.info(f"ðŸ”ª Killing {proc_name} (PID: {proc_pid}) on port {port}")
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except psutil.TimeoutExpired:
                                proc.kill()
                            killed_any = True

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return killed_any
    except Exception as e:
        logger.error(f"Error killing process on port {port}: {e}")
        return False


# ============================
# Environment Setup
# ============================
def setup_environment(port_manager: PortManager):
    """Set up environment variables with dynamic ports, reading DB creds from CirqenConfig."""

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Equiper.settings')
    os.environ['CIRQEN_DATA_DIR'] = str(DATA_PATH)
    os.environ['CIRQEN_APP_DIR'] = str(APPLICATION_PATH)
    os.environ['CIRQEN_RUNTIME_DIR'] = str(RUNTIME_DIR)

    # ── Load config (reads config.json / .env, falls back to defaults) ────────
    try:
        from sync.config import CirqenConfig
        _cfg = CirqenConfig(DATA_PATH)
        # Also export all env-vars the sync agent / Django settings expect
        _cfg.setup_environment_variables()
    except Exception as _e:
        logger.warning(f"CirqenConfig not available ({_e}), using built-in defaults")
        _cfg = None

    # ── Local DB — always on the embedded port allocated by PortManager ───────
    if _cfg:
        _local = _cfg.get('local_db', {})
        DB_CONFIG = {
            'host':     _local.get('host',     '127.0.0.1'),
            'port':     port_manager.get_port('postgresql_local'),   # dynamic
            'database': _local.get('database', 'cirqen1'),
            'user':     _local.get('user',     'cirqen1'),
            'password': _local.get('password', 'Btwelvetech@2024'),
        }
    else:
        DB_CONFIG = {
            'host':     '127.0.0.1',
            'port':     port_manager.get_port('postgresql_local'),
            'database': 'cirqen1',
            'user':     'cirqen1',
            'password': 'Btwelvetech@2024',
        }

    # ── HQ DB — Render PostgreSQL pulled straight from config ─────────────────
    if _cfg:
        _hq = _cfg.get('hq_db', {})
        HQ_DB_CONFIG = {
            'host':     _hq.get('host',     ''),
            'port':     _hq.get('port',     5432),
            'database': _hq.get('database', ''),
            'user':     _hq.get('user',     ''),
            'password': _hq.get('password', ''),
            'enabled':  _hq.get('enabled',  True),
        }
    else:
        # Fallback — Render creds from config DEFAULT_CONFIG
        HQ_DB_CONFIG = {
            'host':     'dpg-d7rk2sa8qa3s73diimb0-a',
            'port':     5432,
            'database': 'cirqen_hq',
            'user':     'cirqen_hq',
            'password': 'RyJEzkPmYWrdC2472TzWnFUMaOIueaye',
            'enabled':  True,
        }

    # ── Publish env-vars for Django settings / subprocesses ───────────────────
    for key, value in DB_CONFIG.items():
        os.environ[f'POSTGRES_LOCAL_{key.upper()}'] = str(value)

    for key, value in HQ_DB_CONFIG.items():
        os.environ[f'POSTGRES_HQ_{key.upper()}'] = str(value)

    # Django settings aliases
    os.environ['HQ_DB_HOST']     = str(HQ_DB_CONFIG['host'])
    os.environ['HQ_DB_PORT']     = str(HQ_DB_CONFIG['port'])
    os.environ['HQ_DB_NAME']     = str(HQ_DB_CONFIG['database'])
    os.environ['HQ_DB_USER']     = str(HQ_DB_CONFIG['user'])
    os.environ['HQ_DB_PASSWORD'] = str(HQ_DB_CONFIG['password'])

    os.environ['REDIS_HOST'] = '127.0.0.1'
    os.environ['REDIS_PORT'] = str(port_manager.get_port('redis'))

    logger.info("=" * 70)
    logger.info("DATABASE CONFIGURATION (setup_environment)")
    logger.info(f"  Local DB : {DB_CONFIG['host']}:{DB_CONFIG['port']}  db={DB_CONFIG['database']}")
    logger.info(f"  HQ DB    : {HQ_DB_CONFIG['host']}:{HQ_DB_CONFIG['port']}  db={HQ_DB_CONFIG['database']}")
    logger.info("=" * 70)

    return DB_CONFIG, HQ_DB_CONFIG

# ============================================================================
# DJANGO SERVER RUNNER — MODULE-LEVEL (required for multiprocessing 'spawn')
#
# With spawn, the child process re-imports this module fresh and looks up the
# target function by its qualified name.  A function defined *inside* another
# function (a closure) cannot be found by name and raises PicklingError before
# the child opens its log file — producing a completely silent crash.
#
# This function must remain at module level.  Do NOT move it inside start_django
# or any other class/function.
# ============================================================================
def run_django_server(port, db_config, redis_port, log_file_path, app_path):
    """
    Entry point for the Django web-server child process.

    Runs AFTER the spawn fork so there is no inherited Qt state.
    All output (stdout, stderr, logging) is redirected to django.log.
    """
    import sys
    import os
    import time
    from pathlib import Path

    # ------------------------------------------------------------------
    # 1. Redirect ALL output to log file before anything else.
    #    This catches C-extension writes, Django's direct fd writes, etc.
    # ------------------------------------------------------------------
    import logging as _logging
    from logging.handlers import RotatingFileHandler as _RotFH

    log_file = open(log_file_path, 'a')

    # OS-level redirect — catches anything writing to fd 1/2 directly
    os.dup2(log_file.fileno(), 1)   # stdout fd
    os.dup2(log_file.fileno(), 2)   # stderr fd

    # Python-level redirect for print() and traceback calls
    sys.stdout = log_file
    sys.stderr = log_file

    # Root logger in this subprocess — RotatingFileHandler only
    _root = _logging.getLogger()
    _root.handlers.clear()
    _rh = _RotFH(log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3)
    _rh.setFormatter(_logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    _root.addHandler(_rh)
    _root.setLevel(_logging.WARNING)

    log_file.write(f"\n{'=' * 70}\n")
    log_file.write(f"Django process started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_file.write(f"PID: {os.getpid()}\n")
    log_file.write(f"Port: {port}\n")
    log_file.write(f"{'=' * 70}\n\n")
    log_file.flush()

    try:
        # ------------------------------------------------------------------
        # 2. Environment variables
        # ------------------------------------------------------------------
        os.environ['DJANGO_SETTINGS_MODULE'] = 'Equiper.settings'
        os.environ['PYTHONUNBUFFERED'] = '1'

        os.environ['POSTGRES_LOCAL_HOST']     = str(db_config['host'])
        os.environ['POSTGRES_LOCAL_PORT']     = str(db_config['port'])
        os.environ['POSTGRES_LOCAL_DATABASE'] = str(db_config['database'])
        os.environ['POSTGRES_LOCAL_USER']     = str(db_config['user'])
        os.environ['POSTGRES_LOCAL_PASSWORD'] = str(db_config['password'])

        os.environ['REDIS_HOST'] = '127.0.0.1'
        os.environ['REDIS_PORT'] = str(redis_port)

        # ------------------------------------------------------------------
        # 3. Add application path to Python path
        # ------------------------------------------------------------------
        app_path_str = str(app_path)
        if app_path_str not in sys.path:
            sys.path.insert(0, app_path_str)

        # ------------------------------------------------------------------
        # 4. Setup Django
        # ------------------------------------------------------------------
        print(f"[DJANGO] Importing Django...", flush=True)
        from django.core.management import call_command
        from django.core.wsgi import get_wsgi_application   # noqa: F401  (ensures WSGI is ready)

        print(f"[DJANGO] Setting up Django...", flush=True)
        import django
        django.setup()

        # ------------------------------------------------------------------
        # 5. Run migrations (local + HQ)
        # ------------------------------------------------------------------
        print(f"[DJANGO] Checking for pending migrations (local)...", flush=True)
        try:
            from django.db.migrations.executor import MigrationExecutor
            from django.db import connections, DEFAULT_DB_ALIAS

            # Local DB
            connection = connections[DEFAULT_DB_ALIAS]
            connection.prepare_database()
            executor = MigrationExecutor(connection)
            targets = executor.loader.graph.leaf_nodes()
            plan = executor.migration_plan(targets)

            if plan:
                print(f"[DJANGO] Found {len(plan)} unapplied local migrations", flush=True)
                call_command('migrate', '--noinput', verbosity=1)
                print(f"[DJANGO] Local migrations completed", flush=True)
            else:
                print(f"[DJANGO] No pending local migrations", flush=True)

            # HQ DB (Render PostgreSQL) — optional
            hq_host = (
                os.environ.get('HQ_DB_HOST', '') or
                os.environ.get('POSTGRES_HQ_HOST', '')
            )
            if hq_host:
                print(f"[DJANGO] Checking HQ migrations ({hq_host})...", flush=True)
                try:
                    hq_conn = connections['hq']
                    hq_conn.prepare_database()
                    hq_exec = MigrationExecutor(hq_conn)
                    hq_plan = hq_exec.migration_plan(
                        hq_exec.loader.graph.leaf_nodes()
                    )
                    if hq_plan:
                        print(
                            f"[DJANGO] Found {len(hq_plan)} unapplied HQ migrations",
                            flush=True,
                        )
                        call_command('migrate', '--noinput', '--database=hq', verbosity=1)
                        print(f"[DJANGO] HQ migrations completed", flush=True)
                    else:
                        print(f"[DJANGO] No pending HQ migrations", flush=True)
                except Exception as hq_e:
                    print(f"[DJANGO] HQ migration skipped: {hq_e}", flush=True)
            else:
                print(f"[DJANGO] HQ DB not configured — skipping HQ migrations", flush=True)

        except Exception as mig_e:
            print(f"[DJANGO] Migration check/run failed: {mig_e}", flush=True)
            print(f"[DJANGO] Continuing with server startup...", flush=True)

        # ------------------------------------------------------------------
        # 6. Start Django development server
        # ------------------------------------------------------------------
        print(f"[DJANGO] Starting development server on port {port}...", flush=True)
        call_command(
            'runserver',
            f'127.0.0.1:{port}',
            use_reloader=False,
            use_threading=True,
            insecure=True,
        )

    except Exception as e:
        print(f"[DJANGO] ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)

