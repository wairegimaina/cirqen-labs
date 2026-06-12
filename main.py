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
from sync.update_manager import UpdateManager  # Original

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
    APPLICATION_PATH = Path(__file__).parent.absolute()
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


# ============================
# CONDITIONAL GUI IMPORTS
# ============================
if not SKIP_GUI_IMPORTS:
    # Only import GUI libraries if this is the main process
    from PySide6.QtWidgets import (
        QApplication, QSplashScreen, QMainWindow, QMessageBox,
        QLabel, QVBoxLayout, QWidget, QPushButton, QHBoxLayout,
        QProgressBar, QTextEdit, QDialog, QDialogButtonBox
    )
    from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread, QUrl
    from PySide6.QtGui import QPixmap, QFont, QColor, QPainter, QPen, QLinearGradient, QIcon
    from PySide6.QtWebEngineWidgets import QWebEngineView

    # ============================
    # First Run Setup
    # ============================
    class FirstRunSetup(QObject):
        """Handles first-run database setup and HOD creation"""

        progress_update = Signal(str, int)
        setup_complete = Signal(bool, str)
        log_message = Signal(str)

        def __init__(self, port_manager: PortManager):
            super().__init__()
            self.port_manager = port_manager
            self.pg_data = DATA_PATH / 'postgres'
            self.pg_logs = DATA_PATH / 'logs'
            self.runtime_dir = RUNTIME_DIR
            self.pg_dir = self.runtime_dir / 'postgresql'
            self.db_config, _ = setup_environment(port_manager)

        def is_first_run(self):
            """Check if this is the first run"""
            is_first = not (self.pg_data / 'PG_VERSION').exists()
            logger.info(f"First run check: {is_first}")
            return is_first

        def run_setup(self):
            """Execute first-run setup"""
            try:
                if not self.is_first_run():
                    logger.info("Database already initialized")
                    self.setup_complete.emit(True, "Already configured")
                    return

                logger.info("Starting first-run setup...")

                # Step 1: Check PostgreSQL binaries
                self.progress_update.emit("Checking PostgreSQL binaries...", 10)
                self.log_message.emit("ðŸ“¦ Verifying PostgreSQL installation...")

                if not self._check_postgres_binaries():
                    error_msg = self._generate_binary_error_report()
                    logger.error(f"PostgreSQL binaries not found:\n{error_msg}")
                    self.setup_complete.emit(False, error_msg)
                    return

                # Step 2: Initialize database
                self.progress_update.emit("Initializing database cluster...", 25)
                self.log_message.emit("ðŸ”§ Creating database cluster...")

                if not self._initialize_database():
                    self.setup_complete.emit(False, "Database initialization failed. Check logs for details.")
                    return

                # Step 3: Configure PostgreSQL
                self.progress_update.emit("Configuring PostgreSQL...", 40)
                self.log_message.emit("âš™ï¸ Configuring database security...")
                self._configure_postgres()

                # Step 4: Start PostgreSQL
                self.progress_update.emit("Starting PostgreSQL...", 55)
                port = self.port_manager.get_port('postgresql_local')
                self.log_message.emit(f"ðŸš€ Starting PostgreSQL on port {port}...")

                pg_process = self._start_postgres()
                if not pg_process:
                    self.setup_complete.emit(False, "Failed to start PostgreSQL server")
                    return

                time.sleep(3)

                # Step 5: Create database
                self.progress_update.emit("Creating application database...", 70)
                self.log_message.emit("ðŸ’¾ Creating application database...")

                if not self._create_database():
                    self._stop_postgres(pg_process)
                    self.setup_complete.emit(False, "Failed to create application database")
                    return

                # Step 6: Run migrations
                self.progress_update.emit("Setting up database schema...", 80)
                self.log_message.emit("ðŸ“Š Running database migrations...")

                if not self._run_migrations():
                    self._stop_postgres(pg_process)
                    self.setup_complete.emit(False, "Failed to run database migrations")
                    return

                # Step 7: Create HOD user
                self.progress_update.emit("Creating HOD user...", 90)
                self.log_message.emit("ðŸ‘¤ Creating administrator account...")

                if not self._create_hod_user():
                    self._stop_postgres(pg_process)
                    self.setup_complete.emit(False, "Failed to create HOD user")
                    return

                # Step 8: Install systemd service (first-run, built-in)
                self.progress_update.emit("Installing PostgreSQL service...", 88)
                self.log_message.emit("Installing persistent systemd service...")
                try:
                    _pgd = self.runtime_dir / 'postgresql'
                    _svc = PostgresSystemdManager(
                        port      = self.port_manager.get_port('postgresql_local'),
                        pg_binary = _pgd / 'bin' / 'postgres',
                        pg_data   = self.pg_data,
                        pg_lib    = _pgd / 'lib',
                    )
                    if _svc.install():
                        self.log_message.emit("systemd service installed. PostgreSQL will persist after app close.")
                    else:
                        self.log_message.emit("systemd service skipped (no root). Stale PID cleanup runs on each start.")
                except Exception as _e:
                    logger.warning(f"systemd install non-fatal: {_e}")

                # Step 9: Stop direct PostgreSQL (systemd takes over)
                self.progress_update.emit("Finalizing setup...", 95)
                self.log_message.emit("âœ… Finalizing configuration...")
                self._stop_postgres(pg_process)

                self.progress_update.emit("Setup complete!", 100)

                success_msg = (
                    "First-run setup completed successfully!\n\n"
                    "HOD User Created:\n"
                    "â€¢ Username: maina.wairegi\n"
                    "â€¢ Email: mosemaina5@gmail.com\n"
                    "â€¢ Password: ChangeMe123!\n\n"
                    "âš ï¸ Please change the password on first login!\n\n"
                    f"Allocated Ports:\n"
                    f"â€¢ PostgreSQL Local: {self.port_manager.get_port('postgresql_local')}\n"
                    f"â€¢ PostgreSQL HQ: {self.port_manager.get_port('postgresql_hq')}\n"
                    f"â€¢ Redis: {self.port_manager.get_port('redis')}\n"
                    f"â€¢ Django: {self.port_manager.get_port('django')}"
                )
                logger.info("First-run setup completed successfully")
                self.setup_complete.emit(True, success_msg)

            except Exception as e:
                import traceback
                error_detail = traceback.format_exc()
                logger.error(f"Setup error: {error_detail}")
                self.setup_complete.emit(False, f"Setup error: {str(e)}\n\nCheck logs at:\n{LOG_FILE}")

        def _generate_binary_error_report(self):
            """Generate detailed error report for missing binaries"""
            report = ["PostgreSQL binaries not found!", ""]
            report.append(f"Expected location: {self.pg_dir}")
            report.append(f"Runtime dir exists: {self.runtime_dir.exists()}")
            report.append(f"PostgreSQL dir exists: {self.pg_dir.exists()}")
            report.append("")

            if self.runtime_dir.exists():
                report.append("Runtime directory contents:")
                for item in self.runtime_dir.iterdir():
                    report.append(f"  â€¢ {item.name}")
            else:
                report.append("âš ï¸ Runtime directory does not exist!")
                report.append(f"   Expected at: {self.runtime_dir}")

            return "\n".join(report)

        def _check_postgres_binaries(self):
            """Check if PostgreSQL binaries exist"""
            if sys.platform == 'win32':
                pg_bin = self.pg_dir / 'bin' / 'postgres.exe'
                initdb = self.pg_dir / 'bin' / 'initdb.exe'
            else:
                pg_bin = self.pg_dir / 'bin' / 'postgres'
                initdb = self.pg_dir / 'bin' / 'initdb'

            return pg_bin.exists() and initdb.exists()

        def _configure_postgres(self):
            """Configure PostgreSQL settings with dynamic port"""
            try:
                pg_hba = self.pg_data / 'pg_hba.conf'
                postgresql_conf = self.pg_data / 'postgresql.conf'
                port = self.db_config['port']

                # Update pg_hba.conf
                if pg_hba.exists():
                    content = pg_hba.read_text()
                    if 'host    all             all             127.0.0.1/32            md5' not in content:
                        content += '\n# Local connections with password\n'
                        content += 'host    all             all             127.0.0.1/32            md5\n'
                        pg_hba.write_text(content)

                # Set custom port
                if postgresql_conf.exists():
                    content = postgresql_conf.read_text()
                    import re
                    content = re.sub(r'#?port\s*=\s*\d+', f'port = {port}', content)
                    postgresql_conf.write_text(content)
                    logger.info(f"Configured PostgreSQL on port {port}")

            except Exception as e:
                logger.warning(f"PostgreSQL configuration warning: {e}")

        def _initialize_database(self):
            """
            Initialize PostgreSQL database cluster
            FIXED: Proper initialization with user creation and share file verification
            """
            import time  # CRITICAL: Import time

            try:
                if sys.platform == 'win32':
                    initdb = self.pg_dir / 'bin' / 'initdb.exe'
                    postgres_bin = self.pg_dir / 'bin' / 'postgres.exe'
                    psql_bin = self.pg_dir / 'bin' / 'psql.exe'
                else:
                    initdb = self.pg_dir / 'bin' / 'initdb'
                    postgres_bin = self.pg_dir / 'bin' / 'postgres'
                    psql_bin = self.pg_dir / 'bin' / 'psql'

                if not initdb.exists():
                    logger.error(f"initdb not found at: {initdb}")
                    return False

                # ====================================================================
                # CRITICAL: Verify PostgreSQL share files exist
                # ====================================================================
                pg_share = self.pg_dir / 'share'
                postgres_bki = None

                # Search for postgres.bki in share directory
                if pg_share.exists():
                    for bki_file in pg_share.rglob('postgres.bki'):
                        postgres_bki = bki_file
                        break

                if not postgres_bki or not postgres_bki.exists():
                    logger.error("âŒ CRITICAL: postgres.bki not found in PostgreSQL share directory!")
                    logger.error(f"   Searched in: {pg_share}")
                    logger.error("   This file is required for database initialization.")
                    logger.error("   PostgreSQL installation is incomplete.")
                    return False

                logger.info(f"âœ… Found postgres.bki: {postgres_bki}")

                import tempfile

                # ====================================================================
                # STEP 1: Initialize database cluster with current user
                # ====================================================================
                logger.info("Initializing PostgreSQL cluster...")

                # Get current username
                try:
                    current_user = os.getlogin()
                except:
                    current_user = os.getenv('USER', 'postgres')

                logger.info(f"Using superuser: {current_user}")

                fd, pwfile_path = tempfile.mkstemp(text=True, suffix='.pwd')

                try:
                    # Use a temporary password for initialization
                    with os.fdopen(fd, 'w') as pwfile:
                        pwfile.write("temp_init_password\n")

                    # Set up environment for PostgreSQL
                    env = os.environ.copy()
                    pg_lib = self.pg_dir / 'lib'
                    if pg_lib.exists() and sys.platform != 'win32':
                        current_ld = env.get('LD_LIBRARY_PATH', '')
                        env['LD_LIBRARY_PATH'] = f"{pg_lib}:{current_ld}" if current_ld else str(pg_lib)

                    cmd = [
                        str(initdb),
                        '-D', str(self.pg_data),
                        '-U', current_user,
                        '--pwfile', pwfile_path,
                        '--encoding=UTF8',
                        '--locale=C',
                        '--auth=trust'  # Use trust initially for setup
                    ]

                    logger.info(f"Running initdb command...")
                    logger.info(f"  User: {current_user}")
                    logger.info(f"  Data dir: {self.pg_data}")

                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=120,
                        env=env
                    )

                    logger.info("âœ… Database cluster initialized")
                    if result.stdout:
                        logger.debug(f"initdb output: {result.stdout[:500]}")

                finally:
                    try:
                        if os.path.exists(pwfile_path):
                            os.unlink(pwfile_path)
                    except:
                        pass

                # ====================================================================
                # STEP 2: Configure pg_hba.conf for trust authentication
                # ====================================================================
                logger.info("Configuring pg_hba.conf for trust authentication...")

                pg_hba = self.pg_data / 'pg_hba.conf'

                hba_content = f"""# TYPE  DATABASE        USER            ADDRESS                 METHOD

        # Trust authentication for setup
        local   all             {current_user}                          trust
        local   all             all                                     trust

        # IPv4 local connections
        host    all             {current_user}  127.0.0.1/32            trust
        host    all             all             127.0.0.1/32            trust

        # IPv6 local connections
        host    all             all             ::1/128                 trust
        """
                pg_hba.write_text(hba_content)
                logger.info("âœ… pg_hba.conf configured for trust authentication")

                # ====================================================================
                # STEP 3: Configure postgresql.conf with proper port
                # ====================================================================
                logger.info("Configuring postgresql.conf...")

                postgresql_conf = self.pg_data / 'postgresql.conf'
                port = self.db_config['port']

                if postgresql_conf.exists():
                    with open(postgresql_conf, 'r') as f:
                        lines = f.readlines()

                    updated_lines = []
                    port_set = False

                    for line in lines:
                        if line.strip().startswith('port') or line.strip().startswith('#port'):
                            updated_lines.append(f"port = {port}\n")
                            port_set = True
                        else:
                            updated_lines.append(line)

                    if not port_set:
                        updated_lines.append(f"\nport = {port}\n")

                    with open(postgresql_conf, 'w') as f:
                        f.writelines(updated_lines)

                    logger.info(f"âœ… Configured port: {port}")

                # ====================================================================
                # STEP 4: Start PostgreSQL temporarily
                # ====================================================================
                logger.info("Starting PostgreSQL temporarily to create users...")

                log_file = open(self.pg_logs / 'postgres_init.log', 'w')

                # Set up environment
                env = os.environ.copy()
                if pg_lib.exists() and sys.platform != 'win32':
                    current_ld = env.get('LD_LIBRARY_PATH', '')
                    env['LD_LIBRARY_PATH'] = f"{pg_lib}:{current_ld}" if current_ld else str(pg_lib)

                pg_process = subprocess.Popen(
                    [str(postgres_bin), '-D', str(self.pg_data)],
                    stdout=log_file,
                    stderr=log_file,
                    env=env
                )

                # Wait for PostgreSQL to be ready
                logger.info("Waiting for PostgreSQL to start...")

                pg_ready = False
                for i in range(40):  # 20 seconds max
                    try:
                        import psycopg2

                        conn = psycopg2.connect(
                            host='127.0.0.1',
                            port=port,
                            database='postgres',
                            user=current_user,
                            connect_timeout=2
                        )
                        conn.close()

                        pg_ready = True
                        logger.info(f"âœ… PostgreSQL is ready (attempt {i+1})")
                        break

                    except:
                        time.sleep(0.5)

                if not pg_ready:
                    logger.error("âŒ PostgreSQL failed to start for user creation")
                    pg_process.terminate()
                    pg_process.wait()
                    log_file.close()

                    # Show log
                    try:
                        with open(self.pg_logs / 'postgres_init.log', 'r') as f:
                            lines = f.readlines()
                            if lines:
                                logger.error("Last 20 lines of log:")
                                for line in lines[-20:]:
                                    logger.error(f"  {line.rstrip()}")
                    except:
                        pass

                    return False

                # ====================================================================
                # STEP 5: Create application user (cirqen1)
                # ====================================================================
                try:
                    logger.info(f"Creating PostgreSQL user: {self.db_config['user']}")

                    import psycopg2
                    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

                    conn = psycopg2.connect(
                        host='127.0.0.1',
                        port=port,
                        database='postgres',
                        user=current_user
                    )
                    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                    cursor = conn.cursor()

                    # Check if user exists
                    cursor.execute(
                        "SELECT 1 FROM pg_roles WHERE rolname = %s",
                        (self.db_config['user'],)
                    )

                    if not cursor.fetchone():
                        # Create user with password
                        cursor.execute(f"""
                            CREATE ROLE {self.db_config['user']}
                            WITH LOGIN PASSWORD %s
                            CREATEDB CREATEROLE SUPERUSER
                        """, (self.db_config['password'],))

                        logger.info(f"âœ… Created user: {self.db_config['user']}")
                    else:
                        logger.info(f"User {self.db_config['user']} already exists")

                    cursor.close()
                    conn.close()

                except Exception as e:
                    logger.error(f"Failed to create user: {e}")
                    pg_process.terminate()
                    pg_process.wait()
                    log_file.close()
                    return False

                # ====================================================================
                # STEP 6: Update pg_hba.conf for password authentication
                # ====================================================================
                logger.info("Updating pg_hba.conf for password authentication...")

                hba_content = f"""# TYPE  DATABASE        USER            ADDRESS                 METHOD

        # System superuser (trust)
        local   all             {current_user}                          trust
        host    all             {current_user}  127.0.0.1/32            trust

        # Application users (password)
        local   all             all                                     md5
        host    all             all             127.0.0.1/32            md5
        host    all             all             ::1/128                 md5
        """
                pg_hba.write_text(hba_content)
                logger.info("âœ… Updated pg_hba.conf for md5 authentication")

                # ====================================================================
                # STEP 7: Reload PostgreSQL configuration
                # ====================================================================
                logger.info("Reloading PostgreSQL configuration...")

                try:
                    conn = psycopg2.connect(
                        host='127.0.0.1',
                        port=port,
                        database='postgres',
                        user=current_user
                    )
                    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                    cursor = conn.cursor()
                    cursor.execute("SELECT pg_reload_conf()")
                    cursor.close()
                    conn.close()
                    logger.info("âœ… Configuration reloaded")
                except Exception as e:
                    logger.warning(f"Could not reload config: {e}")

                # ====================================================================
                # STEP 8: Stop PostgreSQL
                # ====================================================================
                logger.info("Stopping temporary PostgreSQL...")
                pg_process.terminate()
                try:
                    pg_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pg_process.kill()
                    pg_process.wait()

                log_file.close()
                time.sleep(2)

                logger.info("âœ… Database initialization complete")
                return True

            except Exception as e:
                logger.error(f"âŒ Database initialization failed: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False

        def _start_postgres(self):
            """Start PostgreSQL temporarily for setup"""
            try:
                if sys.platform == 'win32':
                    pg_bin = self.pg_dir / 'bin' / 'postgres.exe'
                else:
                    pg_bin = self.pg_dir / 'bin' / 'postgres'

                log_file = open(self.pg_logs / 'postgres_setup.log', 'a')
                port = self.db_config['port']

                process = subprocess.Popen(
                    [str(pg_bin), '-D', str(self.pg_data), '-p', str(port)],
                    stdout=log_file,
                    stderr=log_file
                )

                # Wait for ready
                for i in range(40):
                    try:
                        import psycopg2
                        conn = psycopg2.connect(
                            host=self.db_config['host'],
                            port=port,
                            database='postgres',
                            user=self.db_config['user'],
                            password=self.db_config['password'],
                            connect_timeout=3
                        )
                        conn.close()
                        logger.info(f"PostgreSQL ready on port {port}")
                        return process
                    except:
                        time.sleep(0.5)

                return process

            except Exception as e:
                logger.error(f"Failed to start PostgreSQL: {e}")
                return None

        def _stop_postgres(self, process):
            """Stop PostgreSQL"""
            try:
                if sys.platform == 'win32':
                    pg_ctl = self.pg_dir / 'bin' / 'pg_ctl.exe'
                else:
                    pg_ctl = self.pg_dir / 'bin' / 'pg_ctl'

                subprocess.run([
                    str(pg_ctl), '-D', str(self.pg_data),
                    'stop', '-m', 'fast'
                ], capture_output=True, check=False, timeout=30)

                time.sleep(2)

            except Exception as e:
                logger.warning(f"Error stopping PostgreSQL: {e}")

        def _create_database(self):
            """Create application database"""
            try:
                import psycopg2
                from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

                conn = psycopg2.connect(
                    host=self.db_config['host'],
                    port=self.db_config['port'],
                    database='postgres',
                    user=self.db_config['user'],
                    password=self.db_config['password']
                )
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                cursor = conn.cursor()

                cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.db_config['database'],))

                if not cursor.fetchone():
                    cursor.execute(f"CREATE DATABASE {self.db_config['database']}")
                    logger.info(f"Created database: {self.db_config['database']}")

                cursor.close()
                conn.close()
                return True

            except Exception as e:
                logger.error(f"Failed to create database: {e}")
                return False

        def _run_migrations(self):
            """
            Run Django migrations on BOTH the local database and the Render HQ database.
            Uses --database=hq flag for the second pass so Django applies each set
            to the right DB.  HQ failure is non-fatal — local app still works.
            """
            manage_py = APPLICATION_PATH / 'manage.py'
            if not manage_py.exists():
                logger.error(f"manage.py not found at: {manage_py}")
                return False

            # Base env — skip instance lock, pass local DB creds
            base_env = os.environ.copy()
            base_env['CIRQEN_SKIP_INSTANCE_LOCK'] = '1'
            base_env['CIRQEN_MIGRATION_MODE'] = '1'
            base_env['POSTGRES_LOCAL_HOST']     = str(self.db_config['host'])
            base_env['POSTGRES_LOCAL_PORT']     = str(self.db_config['port'])
            base_env['POSTGRES_LOCAL_DATABASE'] = str(self.db_config['database'])
            base_env['POSTGRES_LOCAL_USER']     = str(self.db_config['user'])
            base_env['POSTGRES_LOCAL_PASSWORD'] = str(self.db_config['password'])

            # ── 1. Local database (default) ──────────────────────────────────
            logger.info("=" * 60)
            logger.info("RUNNING MIGRATIONS — local database")
            logger.info("=" * 60)
            try:
                result = subprocess.run(
                    [sys.executable, str(manage_py), 'migrate', '--noinput'],
                    capture_output=True, text=True,
                    cwd=str(APPLICATION_PATH), env=base_env,
                    check=True, timeout=300,
                )
                logger.info("Local migrations completed")
                if result.stdout:
                    logger.debug(f"Output:\n{result.stdout}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Local migration failed (exit {e.returncode})")
                logger.error(f"stdout:\n{e.stdout}")
                logger.error(f"stderr:\n{e.stderr}")
                return False
            except subprocess.TimeoutExpired:
                logger.error("Local migration timeout (>5 min)")
                return False
            except Exception as e:
                logger.error(f"Local migration error: {e}")
                import traceback; logger.error(traceback.format_exc())
                return False

            # ── 2. HQ database (Render PostgreSQL from CirqenConfig) ─────────
            _, hq = setup_environment(self.port_manager)
            hq_enabled = hq.get('enabled', True)
            hq_host    = hq.get('host', '')

            if not hq_enabled or not hq_host:
                logger.info("HQ DB not configured / disabled — skipping HQ migrations")
                return True

            logger.info("=" * 60)
            logger.info("RUNNING MIGRATIONS — HQ database (Render)")
            logger.info(f"  {hq_host}:{hq['port']}  db={hq['database']}")
            logger.info("=" * 60)

            hq_env = base_env.copy()
            hq_env['POSTGRES_HQ_HOST']     = str(hq['host'])
            hq_env['POSTGRES_HQ_PORT']     = str(hq['port'])
            hq_env['POSTGRES_HQ_DATABASE'] = str(hq['database'])
            hq_env['POSTGRES_HQ_USER']     = str(hq['user'])
            hq_env['POSTGRES_HQ_PASSWORD'] = str(hq['password'])
            hq_env['HQ_DB_HOST']     = str(hq['host'])
            hq_env['HQ_DB_PORT']     = str(hq['port'])
            hq_env['HQ_DB_NAME']     = str(hq['database'])
            hq_env['HQ_DB_USER']     = str(hq['user'])
            hq_env['HQ_DB_PASSWORD'] = str(hq['password'])

            try:
                result = subprocess.run(
                    [sys.executable, str(manage_py), 'migrate', '--noinput', '--database=hq'],
                    capture_output=True, text=True,
                    cwd=str(APPLICATION_PATH), env=hq_env,
                    check=True, timeout=300,
                )
                logger.info("HQ migrations completed")
                if result.stdout:
                    logger.debug(f"Output:\n{result.stdout}")
            except subprocess.CalledProcessError as e:
                logger.warning(f"HQ migration failed (exit {e.returncode}) — continuing")
                logger.warning(f"stdout:\n{e.stdout}")
                logger.warning(f"stderr:\n{e.stderr}")
            except subprocess.TimeoutExpired:
                logger.warning("HQ migration timeout — continuing without HQ schema")
            except Exception as e:
                logger.warning(f"HQ migration error: {e} — continuing")

            return True


        def _create_hod_user(self):
            """
            Create HOD user WITHOUT starting a new instance
            FIXED: Use direct database connection instead of subprocess
            """
            try:
                logger.info("Creating HOD user via direct database connection...")

                # Setup Django in-process (no subprocess)
                import django
                os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Equiper.settings')

                # Add application path to Python path
                sys.path.insert(0, str(APPLICATION_PATH))

                django.setup()

                from django.contrib.auth import get_user_model

                # Import UserProfile - handle if it doesn't exist yet
                try:
                    from users.models import UserProfile
                    has_user_profile = True
                except ImportError:
                    logger.warning("UserProfile model not found - will create basic user only")
                    has_user_profile = False

                User = get_user_model()

                # Check if HOD user exists
                if User.objects.filter(email='mosemaina5@gmail.com').exists():
                    logger.info("âœ… HOD user already exists")
                    return True

                logger.info("Creating HOD user...")

                # Create HOD user
                user = User.objects.create_user(
                    username='maina.wairegi',
                    email='mosemaina5@gmail.com',
                    password='ChangeMe123!',
                    first_name='Maina',
                    last_name='Wairegi',
                    is_staff=True,
                    is_superuser=True
                )

                logger.info(f"âœ… Created user: {user.username}")

                # Create UserProfile if model exists
                if has_user_profile:
                    UserProfile.objects.create(
                        user=user,
                        role='HOD',
                        must_change_password=True,
                        has_uploaded_signature=False,
                        is_approved=True
                    )
                    logger.info("âœ… Created UserProfile for HOD")

                logger.info("âœ… HOD user creation complete")
                return True

            except Exception as e:
                logger.error(f"âŒ Failed to create HOD user: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False


    # ============================
    # Setup Dialog
    # ============================
    # ──────────────────────────────────────────────────────────────────
    # THEME HELPER — reusable themed dialog for info / warn / error
    # ──────────────────────────────────────────────────────────────────
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


    # ============================
    # ENHANCED: Service Manager
    # ============================
    class ServiceManager(QObject):
        """
        ENHANCED: Manages all services with dynamic port support
        """

        progress_update = Signal(str, int)
        service_ready = Signal()
        service_error = Signal(str)

        def __init__(self, port_manager: PortManager):
            """
            ENHANCED: Initialize with database info logging
            """
            super().__init__()
            self.port_manager = port_manager
            self.processes = []
            self.runtime_dir = RUNTIME_DIR
            self.startup_failed = False
            import threading
            self.stop_event = threading.Event()
            self.db_config, self.hq_db_config = setup_environment(port_manager)

            # ---- systemd PostgreSQL manager ----
            _pg_d = RUNTIME_DIR / 'postgresql'
            self._pg_systemd = PostgresSystemdManager(
                port      = port_manager.get_port('postgresql_local'),
                pg_binary = _pg_d / 'bin' / 'postgres',
                pg_data   = DATA_PATH / 'postgres',
                pg_lib    = _pg_d / 'lib',
            )

            # ====================================================================
            # NEW: Log database configuration immediately
            # ====================================================================
            logger.info("="*70)
            logger.info("SERVICE MANAGER INITIALIZED")
            logger.info("="*70)
            logger.info("DATABASE CONFIGURATION:")
            logger.info(f"  LOCAL DB:")
            logger.info(f"    Host:     {self.db_config['host']}")
            logger.info(f"    Port:     {port_manager.get_port('postgresql_local')}")
            logger.info(f"    Database: {self.db_config['database']}")
            logger.info(f"    User:     {self.db_config['user']}")
            logger.info(f"    Password: [REDACTED]")
            logger.info(f"")
            logger.info(f"  HQ DB:")
            logger.info(f"    Host:     {self.hq_db_config['host']}")
            logger.info(f"    Port:     {port_manager.get_port('postgresql_hq')}")
            logger.info(f"    Database: {self.hq_db_config['database']}")
            logger.info(f"    User:     {self.hq_db_config['user']}")
            logger.info(f"    Password: [REDACTED]")
            logger.info("="*70)

        def stop_services_fast(self):
            """
            Stop all services quickly without waiting
            For immediate shutdown scenarios
            """
            logger.info("Fast shutdown initiated...")

            for name, process, log_file in reversed(self.processes):
                try:
                    logger.info(f"Terminating {name}...")

                    # Terminate immediately, don't wait
                    if hasattr(process, 'terminate'):
                        process.terminate()

                    # Force kill after 2 seconds max
                    if hasattr(process, 'kill'):
                        def force_kill():
                            try:
                                if hasattr(process, 'poll') and process.poll() is None:
                                    process.kill()
                                elif hasattr(process, 'is_alive') and process.is_alive():
                                    process.kill()
                            except:
                                pass

                        QTimer.singleShot(2000, force_kill)

                    # Close log file immediately
                    if log_file:
                        try:
                            log_file.close()
                        except:
                            pass

                except Exception as e:
                    logger.debug(f"Error terminating {name}: {e}")

            self.processes.clear()
            logger.info("âœ… Fast shutdown complete")

        def start_services(self):
            """
            Start all services with dynamic ports

            Services started in order:
            1. PostgreSQL Local (embedded database)
            2. PostgreSQL HQ (remote database - optional)
            3. Redis (cache server)
            4. Django (web server)
            5. Celery Worker (background tasks)
            6. Celery Beat (task scheduler)
            7. Sync Agent (bi-directional synchronization)
            8. Update Manager (auto-update system)

            Each service is verified before proceeding to the next.
            """
            try:
                logger.info("=" * 70)
                logger.info("STARTING CIRQEN SERVICES")
                logger.info("=" * 70)

                # ============================================================
                # SERVICE 1: PostgreSQL Local (REQUIRED)
                # ============================================================
                self.progress_update.emit("Starting PostgreSQL Local...", 10)
                logger.info("")
                logger.info("📊 [1/8] Starting PostgreSQL Local...")

                if not self.start_postgresql():
                    raise Exception("PostgreSQL Local startup failed")

                logger.info("✅ PostgreSQL Local started successfully")
                time.sleep(3)

                # ============================================================
                # SERVICE 2: PostgreSQL HQ (OPTIONAL)
                # ============================================================
                self.progress_update.emit("Starting PostgreSQL HQ...", 20)
                logger.info("")
                logger.info("📊 [2/8] Starting PostgreSQL HQ...")

                if not self.start_postgresql_hq():
                    logger.warning("⚠️  PostgreSQL HQ startup failed")
                    logger.warning("   Application will continue without HQ database")
                    logger.warning("   Sync functionality will be limited")
                else:
                    logger.info("✅ PostgreSQL HQ started successfully")

                time.sleep(2)

                # ============================================================
                # SERVICE 3: Redis (REQUIRED)
                # ============================================================
                self.progress_update.emit("Starting Redis...", 30)
                logger.info("")
                logger.info("📊 [3/8] Starting Redis...")

                if not self.start_redis():
                    raise Exception("Redis startup failed")

                logger.info("✅ Redis started successfully")
                time.sleep(2)

                # ============================================================
                # APPLY CELERY ENVIRONMENT VARIABLES BEFORE DJANGO & CELERY
                # ============================================================
                logger.info("Applying Celery environment variables...")

                redis_port = self.port_manager.get_port('redis')

                os.environ["CELERY_BROKER_URL"] = f"redis://127.0.0.1:{redis_port}/2"
                os.environ["CELERY_RESULT_BACKEND"] = f"redis://127.0.0.1:{redis_port}/2"

                logger.info(f"CELERY_BROKER_URL set to: {os.environ['CELERY_BROKER_URL']}")
                logger.info(f"CELERY_RESULT_BACKEND set to: {os.environ['CELERY_RESULT_BACKEND']}")

                logger.info("Celery environment variables applied successfully.")

                # ============================================================
                # SERVICE 4: Django Web Server (REQUIRED)
                # ============================================================
                self.progress_update.emit("Starting web server...", 45)
                logger.info("")
                logger.info("📊 [4/8] Starting Django Web Server...")

                if not self.start_django():
                    raise Exception("Django startup failed")

                logger.info("✅ Django web server started successfully")
                time.sleep(3)

                # ============================================================
                # SERVICE 5: Celery Worker (OPTIONAL)
                # ============================================================
                self.progress_update.emit("Starting task worker...", 60)
                logger.info("")
                logger.info("📊 [5/8] Starting Celery Worker...")

                if not self.start_celery():
                    logger.warning("⚠️  Celery startup failed")
                    logger.warning("   Application will continue without background tasks")
                    logger.warning("   Scheduled jobs and async tasks will not run")
                else:
                    logger.info("✅ Celery worker started successfully")

                time.sleep(2)

                # ============================================================
                # SERVICE 6: Celery Beat (OPTIONAL)
                # ============================================================
                self.progress_update.emit("Starting task scheduler...", 70)
                logger.info("")
                logger.info("📊 [6/8] Starting Celery Beat Scheduler...")

                if not self.start_celery_beat():
                    logger.warning("⚠️  Celery Beat startup failed")
                    logger.warning("   Application will continue without scheduled tasks")
                    logger.warning("   Automated reports and notifications will not run")
                else:
                    logger.info("✅ Celery Beat scheduler started successfully")
                    logger.info("")
                    logger.info("📅 CELERY BEAT FEATURES:")
                    logger.info("   • ⏰ Automated task scheduling")
                    logger.info("   • 📧 Periodic email notifications")
                    logger.info("   • 📊 Automated reports")
                    logger.info("   • 🧹 Database maintenance")
                    logger.info("   • 🔔 Calibration reminders")

                time.sleep(2)


                # ============================================================
                # SERVICE 7: Update Manager (OPTIONAL)
                # ============================================================
                try:
                    self.progress_update.emit("Starting update manager...", 90)
                    logger.info("")
                    logger.info("📊 [7/8] Starting Update Manager...")
                    logger.info("="*70)

                    update_started = False

                    # Check if update_manager module exists
                    logger.info("Checking for update_manager module...")

                    try:
                        from sync.update_manager import UpdateManager
                        logger.info("✅ UpdateManager module found")
                        update_started = self.start_update_manager()
                    except ImportError as import_err:
                        logger.warning(f"⚠️  UpdateManager module not found: {import_err}")
                        logger.info("   Skipping update manager (optional service)")
                    except Exception as e:
                        logger.error(f"❌ Error importing UpdateManager: {e}")
                        import traceback
                        logger.error(traceback.format_exc())

                    if update_started:
                        logger.info("✅ Update manager started successfully")
                        logger.info("")
                        logger.info("🔄 UPDATE MANAGER ACTIVE:")
                        logger.info("   • ⚡ Non-blocking startup checks (5s timeout)")
                        logger.info("   • 🔄 Automatic server reconnection")
                        logger.info("   • 📊 Background monitoring (every 60 minutes)")
                        logger.info("   • 🔔 User notifications when updates available")
                        logger.info("   • 🛡️ Graceful degradation if server offline")
                    else:
                        logger.warning("⚠️  Update manager startup failed or not available")
                        logger.warning("   Application will continue without automatic updates")

                except Exception as e:
                    logger.error(f"❌ Update manager error: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    logger.warning("   Continuing without update manager...")

                logger.info("="*70)
                time.sleep(1)


                # ============================================================
                # SERVICE 8: Sync Agent (OPTIONAL)
                # ============================================================
                self.progress_update.emit("Starting sync agent...", 80)
                logger.info("")
                logger.info("📊 [8/8] Starting Sync Agent...")

                if not self.start_sync_agent():
                    logger.warning("⚠️  Sync agent startup failed")
                    logger.warning("   Application will continue without automatic synchronization")
                    logger.warning("   Data sync with HQ will not occur automatically")
                    logger.warning("   Check logs/sync_agent.log for details")
                    logger.warning("")
                    logger.warning("💡 TROUBLESHOOTING:")
                    logger.warning("   1. Verify sync_agent.py exists in application directory")
                    logger.warning("   2. Check if HQ server is reachable")
                    logger.warning("   3. Review sync_agent.log for error details")
                    logger.warning("   4. Ensure database credentials are correct")
                else:
                    logger.info("✅ Sync agent started successfully")
                    logger.info("")
                    logger.info("🔄 SYNC AGENT ACTIVE:")
                    logger.info("   • ⚡ Real-time change detection and upload")
                    logger.info("   • 📥 Periodic HQ updates download")
                    logger.info("   • 📜 Certificate synchronization")
                    logger.info("   • 🔄 Smart delete with cascade support")
                    logger.info("   • 🌐 Automatic offline/online handling")

                time.sleep(2)


                # ============================================================
                # ALL SERVICES STARTED - READY!
                # ============================================================
                self.progress_update.emit("Ready!", 100)
                time.sleep(1)

                logger.info("")
                logger.info("=" * 70)
                logger.info("✅ ALL SERVICES STARTED SUCCESSFULLY")
                logger.info("=" * 70)

                # Count successful services
                active_services = []
                for name, process, _ in self.processes:
                    if hasattr(process, 'poll'):
                        if process.poll() is None:
                            active_services.append(name)
                    elif hasattr(process, 'is_alive'):
                        if process.is_alive():
                            active_services.append(name)

                logger.info(f"📊 Active Services: {len(active_services)}/{len(self.processes)}")
                for service_name in active_services:
                    logger.info(f"   ✓ {service_name}")

                logger.info("")
                logger.info("🎯 APPLICATION STATUS:")
                logger.info(f"   • Web Interface: http://127.0.0.1:{self.port_manager.get_port('django')}")
                logger.info(f"   • PostgreSQL Local: Port {self.port_manager.get_port('postgresql_local')}")
                logger.info(f"   • Redis: Port {self.port_manager.get_port('redis')}")

                if any(name == 'sync_agent' for name in active_services):
                    logger.info("   • Sync Agent: Active ✅")
                else:
                    logger.info("   • Sync Agent: Not Running ⚠️")

                if any(name == 'update_manager' for name in active_services):
                    logger.info("   • Update Manager: Active ✅")
                else:
                    logger.info("   • Update Manager: Not Running ⚠️")

                if any(name == 'celery' for name in active_services):
                    logger.info("   • Celery Worker: Active ✅")
                else:
                    logger.info("   • Celery Worker: Not Running ⚠️")

                if any(name == 'celery_beat' for name in active_services):
                    logger.info("   • Celery Beat: Active ✅")
                else:
                    logger.info("   • Celery Beat: Not Running ⚠️")

                logger.info("")
                logger.info("=" * 70)
                logger.info("🚀 CIRQEN IS NOW READY!")
                logger.info("=" * 70)
                logger.info("")

                # Emit signal that services are ready
                self.service_ready.emit()

            except Exception as e:
                # ============================================================
                # ERROR HANDLING - CLEANUP ON FAILURE
                # ============================================================
                logger.error("")
                logger.error("=" * 70)
                logger.error("❌ SERVICE STARTUP FAILED")
                logger.error("=" * 70)
                logger.error(f"Error: {str(e)}")
                logger.error("")

                # Log which services were running
                logger.error("📊 Services status at time of failure:")
                for name, process, _ in self.processes:
                    try:
                        if hasattr(process, 'poll'):
                            if process.poll() is None:
                                logger.error(f"   ✓ {name}: Running")
                            else:
                                logger.error(f"   ✗ {name}: Stopped (exit code: {process.poll()})")
                        elif hasattr(process, 'is_alive'):
                            if process.is_alive():
                                logger.error(f"   ✓ {name}: Running")
                            else:
                                logger.error(f"   ✗ {name}: Stopped")
                    except:
                        logger.error(f"   ? {name}: Unknown status")

                logger.error("")
                logger.error("💡 TROUBLESHOOTING STEPS:")
                logger.error("   1. Check logs directory for service-specific errors:")
                logger.error(f"      {DATA_PATH / 'logs'}")
                logger.error("   2. Verify all required ports are available:")
                logger.error(f"      - PostgreSQL: {self.port_manager.get_port('postgresql_local')}")
                logger.error(f"      - Redis: {self.port_manager.get_port('redis')}")
                logger.error(f"      - Django: {self.port_manager.get_port('django')}")
                logger.error("   3. Run cleanup script to remove stale processes:")
                logger.error("      python cleanup_cirqen.py")
                logger.error("   4. Check system resources (RAM, disk space)")
                logger.error("   5. Review main application log:")
                logger.error(f"      {DATA_PATH / 'logs' / 'cirqen_app.log'}")
                logger.error("")
                logger.error("=" * 70)

                # Mark startup as failed
                self.startup_failed = True

                # Attempt to stop any services that did start
                logger.info("Attempting to stop any running services...")
                self.stop_services()

                # Emit error signal to UI
                self.service_error.emit(str(e))

        def monitor_sync_agent_health(self):
            """
            Monitor sync agent health and restart if needed
            Runs as background thread
            """
            import time

            logger = logging.getLogger('Cirqen.SyncAgent')

            logger.info("ðŸ” Sync agent health monitor started")

            check_interval = 60  # Check every 60 seconds
            restart_attempts = 0
            max_restart_attempts = 3

            while not self.stop_event.is_set():
                try:
                    # Find sync agent process
                    sync_process = None
                    sync_log_file = None

                    for name, process, log_file in self.processes:
                        if name == 'sync_agent':
                            sync_process = process
                            sync_log_file = log_file
                            break

                    if sync_process:
                        # Check if process is still running
                        if sync_process.poll() is not None:
                            # Process died!
                            exit_code = sync_process.poll()

                            logger.error("=" * 70)
                            logger.error("âŒ SYNC AGENT PROCESS DIED!")
                            logger.error(f"   Exit code: {exit_code}")
                            logger.error(f"   Restart attempts: {restart_attempts}/{max_restart_attempts}")
                            logger.error("=" * 70)

                            # Try to restart
                            if restart_attempts < max_restart_attempts:
                                restart_attempts += 1

                                logger.info(f"ðŸ”„ Attempting to restart sync agent (attempt {restart_attempts})...")

                                # Close old log file
                                if sync_log_file:
                                    try:
                                        sync_log_file.close()
                                    except:
                                        pass

                                # Remove from processes list
                                self.processes = [(n, p, l) for n, p, l in self.processes if n != 'sync_agent']

                                # Wait a moment
                                time.sleep(5)

                                # Restart
                                if self.start_sync_agent():
                                    logger.info("âœ… Sync agent restarted successfully")
                                    restart_attempts = 0  # Reset counter on success
                                else:
                                    logger.error("âŒ Sync agent restart failed")
                            else:
                                logger.error("âŒ Max restart attempts reached")
                                logger.error("   Manual intervention required")
                                logger.error("   Run cleanup_cirqen.py and restart application")
                                break
                        else:
                            # Process is healthy
                            if restart_attempts > 0:
                                logger.info("âœ… Sync agent health restored")
                                restart_attempts = 0

                            logger.debug(f"âœ“ Sync agent healthy (PID: {sync_process.pid})")

                except Exception as e:
                    logger.error(f"Error in sync agent monitor: {e}")

                # Sleep in small increments to allow clean shutdown
                for _ in range(check_interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            logger.info("ðŸ” Sync agent health monitor stopped")

        def start_postgresql(self):
            """
            Start the local embedded PostgreSQL instance on port 2215.

            Strategy (in order):
              1. systemd service active and accepting connections -> reuse as-is.
              2. systemd service installed but inactive           -> start it, wait.
              3. postgres already listening on the port          -> reuse as-is.
              4. Nothing running                                  -> direct process start.

            Systemd check is performed FIRST so a system-managed PostgreSQL
            on the correct port is always reused even when the embedded
            binary path differs.

            After confirming postgres is up we ensure the app user and database
            exist, then return True.  Any failure returns False.
            """
            import time as _time
            import psycopg2
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

            pg_dir  = self.runtime_dir / 'postgresql'
            pg_data = DATA_PATH / 'postgres'
            pg_log  = DATA_PATH / 'logs' / 'postgres.log'
            port    = self.port_manager.get_port('postgresql_local')

            logger.info(f"[PG] Starting PostgreSQL local on port {port}...")

            # ------------------------------------------------------------------
            # PRIORITY CHECK: systemd/existing instance (before binary check)
            # This ensures a system-managed postgres on port 2215 is always
            # detected and reused, even if the embedded binary path differs.
            # ------------------------------------------------------------------
            if self._pg_systemd.ensure(timeout=90):
                logger.info("[PG] Reusing existing PostgreSQL instance.")
                result = self._ensure_pg_user_and_db_safe(port, pg_dir)
                if result:
                    logger.info(f"[PG] ✅ PostgreSQL Local ready (systemd-managed, port {port}).")
                else:
                    logger.error("[PG] Systemd instance running but user/db setup failed.")
                return result

            # ------------------------------------------------------------------
            # Fallback: need to start the embedded binary directly
            # ------------------------------------------------------------------
            pg_bin = pg_dir / 'bin' / ('postgres.exe' if sys.platform == 'win32' else 'postgres')
            if not pg_bin.exists():
                logger.error(f"[PG] Binary not found: {pg_bin}")
                logger.error("[PG] Cannot start PostgreSQL — no systemd service and no embedded binary.")
                return False

            # ------------------------------------------------------------------
            # Direct start — clean up any stale postmaster.pid first
            # ------------------------------------------------------------------
            postmaster_pid_file = pg_data / 'postmaster.pid'
            if postmaster_pid_file.exists():
                logger.warning("[PG] Found stale postmaster.pid — cleaning up...")
                try:
                    old_pid = int(postmaster_pid_file.read_text().splitlines()[0])
                    if psutil.pid_exists(old_pid):
                        proc = psutil.Process(old_pid)
                        if 'postgres' in proc.name().lower():
                            proc.terminate()
                            try:
                                proc.wait(timeout=8)
                            except psutil.TimeoutExpired:
                                proc.kill()
                                proc.wait(timeout=3)
                            _time.sleep(1)
                except Exception as e:
                    logger.debug(f"[PG] postmaster.pid cleanup: {e}")
                # Remove stale state files regardless
                for fname in ('postmaster.pid', 'postmaster.opts',
                              f'.s.PGSQL.{port}', f'.s.PGSQL.{port}.lock'):
                    try:
                        (pg_data / fname).unlink(missing_ok=True)
                    except Exception:
                        pass

            # Build environment with correct library path
            env = os.environ.copy()
            pg_lib = pg_dir / 'lib'
            if pg_lib.exists() and sys.platform != 'win32':
                ld = env.get('LD_LIBRARY_PATH', '')
                env['LD_LIBRARY_PATH'] = f"{pg_lib}:{ld}" if ld else str(pg_lib)

            # Log rotation handled automatically by RotatingFileHandler

            log_fh = open(pg_log, 'a')
            process = subprocess.Popen(
                [str(pg_bin), '-D', str(pg_data)],
                stdout=log_fh, stderr=log_fh, env=env
            )
            self.processes.append(('postgres_local', process, log_fh))
            logger.info(f"[PG] Process started (PID {process.pid}), waiting for ready...")

            # ------------------------------------------------------------------
            # Wait up to 45 s for postgres to accept connections
            # ------------------------------------------------------------------
            postgres_ready   = False
            superuser_name   = None
            candidate_users  = ['postgres', self.db_config['user']]
            try:
                candidate_users.insert(1, os.getlogin())
            except OSError:
                pass

            for attempt in range(240):          # 240 x 0.5 s = 120 s max
                if process.poll() is not None:
                    logger.error(f"[PG] Process died (exit {process.poll()}). Check {pg_log}")
                    return False

                for uname in candidate_users:
                    try:
                        conn = psycopg2.connect(
                            host='127.0.0.1', port=port,
                            database='postgres', user=uname,
                            connect_timeout=3
                        )
                        conn.close()
                        superuser_name = uname
                        postgres_ready = True
                        break
                    except psycopg2.OperationalError as exc:
                        err = str(exc)
                        if 'does not exist' in err or 'authentication failed' in err:
                            # Server is up, just wrong user — still counts
                            superuser_name = uname
                            postgres_ready = True
                            break
                        # else: still starting up
                if postgres_ready:
                    break
                _time.sleep(0.5)

            if not postgres_ready:
                logger.error(f"[PG] Timed out waiting for PostgreSQL on port {port}. Check {pg_log}")
                return False

            logger.info(f"[PG] Ready on port {port} (connected as '{superuser_name}')")
            return self._ensure_pg_user_and_db_safe(port, pg_dir)

        def _ensure_pg_user_and_db_safe(self, port: int, pg_dir):
            """
            Idempotently create the app user and database if they don't exist.

            FIX: success log/return is now INSIDE the try block so a
            psycopg2 error in the finally (cur/conn close) can never
            swallow a True result.  cur is initialised to None before the
            try so the finally never hits NameError.
            """
            import psycopg2
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

            candidate_superusers = ['postgres', self.db_config['user']]
            try:
                candidate_superusers.insert(1, os.getlogin())
            except OSError:
                pass

            # --- connect as any superuser ---
            conn = None
            for uname in candidate_superusers:
                try:
                    conn = psycopg2.connect(
                        host='127.0.0.1', port=port,
                        database='postgres', user=uname,
                        connect_timeout=5
                    )
                    logger.info(f"[PG] Superuser connection established as '{uname}'.")
                    break
                except psycopg2.OperationalError as exc:
                    logger.debug(f"[PG] Could not connect as '{uname}': {exc}")
                    continue

            if conn is None:
                logger.error("[PG] Could not connect as any superuser to verify user/db.")
                return False

            # --- cur is None-initialised so the finally never hits NameError ---
            cur = None
            success = False
            try:
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                cur = conn.cursor()

                # Ensure app user exists
                cur.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s",
                    (self.db_config['user'],)
                )
                if not cur.fetchone():
                    logger.info(f"[PG] Creating user '{self.db_config['user']}'...")
                    cur.execute(
                        f"CREATE ROLE {self.db_config['user']} "
                        f"WITH LOGIN PASSWORD %s CREATEDB CREATEROLE SUPERUSER",
                        (self.db_config['password'],)
                    )
                    logger.info(f"[PG] User '{self.db_config['user']}' created.")
                else:
                    logger.info(f"[PG] User '{self.db_config['user']}' exists.")

                # Ensure app database exists
                cur.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    (self.db_config['database'],)
                )
                if not cur.fetchone():
                    logger.info(f"[PG] Creating database '{self.db_config['database']}'...")
                    cur.execute(
                        f"CREATE DATABASE {self.db_config['database']} "
                        f"ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
                    )
                    logger.info(f"[PG] Database '{self.db_config['database']}' created.")
                else:
                    logger.info(f"[PG] Database '{self.db_config['database']}' exists.")

                # Mark success BEFORE the finally block closes resources
                logger.info(f"[PG] PostgreSQL local is ready on port {port}.")
                success = True

            except Exception as exc:
                logger.error(f"[PG] Error during user/db verification: {exc}")
                success = False

            finally:
                # Safely close cursor and connection — never let these raise
                if cur is not None:
                    try:
                        cur.close()
                    except Exception:
                        pass
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

            return success

        def start_postgresql_hq(self):
            """
            Start PostgreSQL HQ (optional second database) with auto-creation
            FIXED: Now creates HQ database if needed
            """
            try:
                pg_dir = self.runtime_dir / 'postgresql'
                pg_data_hq = DATA_PATH / 'postgres_hq'
                pg_log = DATA_PATH / 'logs' / 'postgres_hq.log'
                port = self.port_manager.get_port('postgresql_hq')

                # Create HQ data directory if not exists
                pg_data_hq.mkdir(exist_ok=True)

                if sys.platform == 'win32':
                    pg_bin = pg_dir / 'bin' / 'postgres.exe'
                else:
                    pg_bin = pg_dir / 'bin' / 'postgres'

                # Check if HQ database cluster is initialized
                if not (pg_data_hq / 'PG_VERSION').exists():
                    logger.info("PostgreSQL HQ data directory not initialized - skipping")
                    return False

                logger.info("="*70)
                logger.info("STARTING POSTGRESQL HQ")
                logger.info("="*70)
                logger.info(f"Port: {port}")
                logger.info(f"Database: {self.hq_db_config['database']}")
                logger.info("="*70)

                # ---- HQ port reuse check ----
                # If postgres is already listening on the HQ port (e.g. a
                # previous run or an external instance) skip launching a new one.
                import socket as _sock
                with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as _s:
                    _s.settimeout(1)
                    _hq_port_busy = _s.connect_ex(('127.0.0.1', port)) == 0

                if _hq_port_busy:
                    # Check if postgres owns the HQ port directly
                    # (not via _pg_systemd which checks local port)
                    hq_port_is_pg = False
                    try:
                        for conn in psutil.net_connections(kind='inet'):
                            if conn.laddr.port == port and conn.status == 'LISTEN' and conn.pid:
                                proc = psutil.Process(conn.pid)
                                if 'postgres' in proc.name().lower():
                                    hq_port_is_pg = True
                                    break
                    except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
                        hq_port_is_pg = True  # Optimistic fallback
                    if hq_port_is_pg:
                        logger.info(
                            f"[PG-HQ] Port {port} already owned by postgres — reusing."
                        )
                        # Skip to the user/db setup below
                    else:
                        logger.warning(
                            f"[PG-HQ] Port {port} occupied by non-postgres process — "
                            "skipping HQ database start."
                        )
                        return False
                else:
                    # Port is free — start the bundled binary
                    log_file = open(pg_log, 'a')
                    process = subprocess.Popen(
                        [str(pg_bin), '-D', str(pg_data_hq), '-p', str(port)],
                        stdout=log_file,
                        stderr=log_file
                    )
                    self.processes.append(('postgres_hq', process, log_file))

                logger.info("Waiting for PostgreSQL HQ to be ready...")

                # Wait for PostgreSQL HQ to accept connections
                postgres_hq_ready = False

                for i in range(20):
                    try:
                        import psycopg2
                        conn = psycopg2.connect(
                            host=self.hq_db_config['host'],
                            port=port,
                            database='postgres',  # FIXED: Use 'postgres' database
                            user=self.hq_db_config['user'],
                            password=self.hq_db_config['password'],
                            connect_timeout=3
                        )
                        conn.close()
                        logger.info("âœ… PostgreSQL HQ is ready")
                        postgres_hq_ready = True
                        break
                    except:
                        time.sleep(0.5)

                if not postgres_hq_ready:
                    logger.warning("âš ï¸  PostgreSQL HQ timeout (non-critical)")
                    return False

                # Create HQ database if needed (same logic as local)
                try:
                    import psycopg2
                    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

                    conn = psycopg2.connect(
                        host=self.hq_db_config['host'],
                        port=port,
                        database='postgres',
                        user=self.hq_db_config['user'],
                        password=self.hq_db_config['password']
                    )
                    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                    cursor = conn.cursor()

                    cursor.execute(
                        "SELECT 1 FROM pg_database WHERE datname = %s",
                        (self.hq_db_config['database'],)
                    )
                    db_exists = cursor.fetchone() is not None

                    if not db_exists:
                        logger.info(f"Creating HQ database: {self.hq_db_config['database']}")
                        cursor.execute(f"CREATE DATABASE {self.hq_db_config['database']}")
                        logger.info(f"âœ… HQ database created: {self.hq_db_config['database']}")

                    cursor.close()
                    conn.close()

                    logger.info(f"âœ… PostgreSQL HQ ready on port {port}")
                    return True

                except Exception as e:
                    logger.warning(f"âš ï¸  PostgreSQL HQ database setup failed: {e}")
                    return False

            except Exception as e:
                logger.warning(f"âš ï¸  PostgreSQL HQ error (non-critical): {e}")
                return False

        def start_redis(self):
            """Start Redis"""
            try:
                redis_dir = self.runtime_dir / 'redis'
                redis_data = DATA_PATH / 'redis'
                redis_log = DATA_PATH / 'logs' / 'redis.log'
                port = self.port_manager.get_port('redis')

                if sys.platform == 'win32':
                    redis_bin = redis_dir / 'redis-server.exe'
                else:
                    redis_bin = redis_dir / 'redis-server'

                if not redis_bin.exists():
                    logger.error(f"Redis binary not found: {redis_bin}")
                    return False

                redis_conf = redis_data / 'redis.conf'
                redis_conf.write_text(f"""
    dir {redis_data}
    port {port}
    bind 127.0.0.1
    logfile {redis_log}
    daemonize no
    """)

                logger.info(f"Starting Redis on port {port}")

                log_file = open(redis_log, 'a')
                process = subprocess.Popen(
                    [str(redis_bin), str(redis_conf)],
                    stdout=log_file,
                    stderr=log_file
                )
                self.processes.append(('redis', process, log_file))

                time.sleep(1)
                logger.info(f"âœ… Redis started on port {port}")
                return True

            except Exception as e:
                logger.error(f"Redis error: {e}")
                return False

        def start_django(self):
            """
            Start Django web server using multiprocessing.
            ENHANCED: Better health checks, migrations, and detailed logging.

            NOTE: The target function run_django_server is defined at MODULE LEVEL
            (not nested here).  This is required so that multiprocessing 'spawn'
            can pickle it by name.  Nested/closure functions are not picklable and
            would cause a silent crash before django.log is ever created.
            """
            import time
            import multiprocessing
            from multiprocessing import Process

            try:
                django_log = DATA_PATH / 'logs' / 'django.log'
                port = self.port_manager.get_port('django')

                logger.info("="*70)
                logger.info("STARTING DJANGO WEB SERVER (MULTIPROCESSING)")
                logger.info("="*70)
                logger.info(f"Port: {port}")
                logger.info(f"URL:  http://127.0.0.1:{port}/")
                logger.info("="*70)

                # run_django_server is defined at module level — picklable by spawn.
                logger.info("🚀 Launching Django multiprocessing process...")

                django_process = Process(
                    target=run_django_server,
                    args=(
                        port,
                        self.db_config.copy(),
                        self.port_manager.get_port('redis'),
                        str(django_log),
                        APPLICATION_PATH
                    ),
                    daemon=False  # Don't make it daemon so it can outlive parent
                )

                django_process.start()

                logger.info(f"âœ… Django process started (PID: {django_process.pid})")
                logger.info(f"â³ Waiting for Django to be ready...")

                # Store process reference
                self.processes.append(('django', django_process, None))

                # Emit progress
                try:
                    self.progress_update.emit(f"Starting Django (PID: {django_process.pid})...", 65)
                except:
                    pass

                # ====================================================================
                # ENHANCED HEALTH CHECK WITH DETAILED LOGGING
                # ====================================================================
                django_ready = False
                max_wait = 180  # 3 minutes for first run (migrations, font cache, etc.)

                import urllib.request
                import urllib.error
                import socket

                logger.info("=" * 70)
                logger.info("DJANGO HEALTH CHECK - ENHANCED")
                logger.info("=" * 70)
                logger.info("â³ Waiting for Django server to be ready...")
                logger.info("   This may take 1-3 minutes on first run:")
                logger.info("   â€¢ Matplotlib font cache (~30-45s)")
                logger.info("   â€¢ Django app loading (~20-30s)")
                logger.info("   â€¢ Database migrations (if pending)")
                logger.info("   â€¢ Database connections (~10s)")
                logger.info("=" * 70)

                start_time = time.time()
                last_log_time = start_time
                attempts = 0
                port_open_time = None
                last_status = None

                for i in range(max_wait * 2):  # Check every 0.5 seconds
                    try:
                        attempts += 1
                        current_time = time.time()
                        elapsed = current_time - start_time

                        # ============================================================
                        # CHECK 1: Is process still alive?
                        # ============================================================
                        if not django_process.is_alive():
                            exit_code = django_process.exitcode
                            logger.error("=" * 70)
                            logger.error(f"âŒ DJANGO PROCESS DIED (EXIT CODE: {exit_code})")
                            logger.error("=" * 70)

                            # Show log
                            try:
                                with open(django_log, 'r') as f:
                                    lines = f.readlines()
                                    if lines:
                                        logger.error("ðŸ“„ LAST 50 LINES OF DJANGO LOG:")
                                        logger.error("-" * 70)
                                        for line in lines[-50:]:
                                            logger.error(f"  {line.rstrip()}")
                                        logger.error("=" * 70)
                            except Exception as log_err:
                                logger.error(f"Could not read log: {log_err}")

                            return False

                        # ============================================================
                        # CHECK 2: Is port listening? (Socket check - FAST)
                        # ============================================================
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(1)
                        port_result = sock.connect_ex(('127.0.0.1', port))
                        sock.close()

                        port_open = (port_result == 0)

                        if not port_open:
                            # Port not open yet - Django still starting
                            current_status = f"PORT_CLOSED"

                            if current_status != last_status:
                                logger.info(f"   ðŸ” Status: Django starting, port not open yet...")
                                last_status = current_status

                            if current_time - last_log_time > 10:  # Log every 10 seconds
                                logger.info(f"   â³ Still waiting... {int(elapsed)}s elapsed")
                                logger.info(f"      - Process alive: âœ…")
                                logger.info(f"      - Port {port} open: âŒ (not yet)")
                                last_log_time = current_time

                            time.sleep(0.5)
                            continue

                        # Port is NOW open!
                        if port_open_time is None:
                            port_open_time = current_time
                            logger.info(f"   âœ… Port {port} is now LISTENING (after {int(elapsed)}s)")
                            logger.info(f"   ðŸ” Attempting HTTP connection...")

                        # ============================================================
                        # CHECK 3: Can we get HTTP response? (CONFIRMS Django ready)
                        # ============================================================
                        try:
                            request = urllib.request.Request(
                                f'http://127.0.0.1:{port}/',
                                headers={'User-Agent': 'Cirqen-HealthCheck/1.0'}
                            )

                            response = urllib.request.urlopen(request, timeout=3)

                            logger.info("=" * 70)
                            logger.info(f"âœ… DJANGO READY ON PORT {port}")
                            logger.info("=" * 70)
                            logger.info(f"   Total startup time: {int(elapsed)}s")
                            logger.info(f"   HTTP response code: {response.code}")
                            logger.info(f"   Health check attempts: {attempts}")
                            logger.info("=" * 70)

                            django_ready = True
                            break

                        except urllib.error.HTTPError as e:
                            # Django is responding with HTTP error - but it's READY!
                            if e.code in [200, 301, 302, 404, 500, 403]:
                                logger.info("=" * 70)
                                logger.info(f"âœ… DJANGO READY ON PORT {port} (HTTP {e.code})")
                                logger.info("=" * 70)
                                logger.info(f"   Total startup time: {int(elapsed)}s")
                                logger.info(f"   Response code: {e.code} (Django responding)")
                                logger.info(f"   Health check attempts: {attempts}")
                                logger.info("=" * 70)

                                django_ready = True
                                break
                            else:
                                # Unexpected HTTP error - log but continue trying
                                current_status = f"HTTP_ERROR_{e.code}"
                                if current_status != last_status:
                                    logger.warning(f"   âš ï¸  HTTP {e.code} error, retrying...")
                                    last_status = current_status

                                if current_time - last_log_time > 10:
                                    logger.info(f"   â³ HTTP errors, still trying... {int(elapsed)}s elapsed")
                                    last_log_time = current_time

                        except (urllib.error.URLError, socket.error, ConnectionRefusedError) as e:
                            # Port open but HTTP not ready - Django still loading
                            current_status = "PORT_OPEN_HTTP_NOT_READY"

                            if current_status != last_status:
                                logger.info(f"   ðŸ” Port open, waiting for HTTP response...")
                                last_status = current_status

                            if current_time - last_log_time > 10:
                                time_since_port_open = current_time - port_open_time
                                logger.info(f"   â³ Django loading... {int(elapsed)}s total, {int(time_since_port_open)}s since port opened")
                                logger.info(f"      - Process alive: âœ…")
                                logger.info(f"      - Port {port} listening: âœ…")
                                logger.info(f"      - HTTP responding: â³ (loading apps...)")
                                last_log_time = current_time

                    except Exception as e:
                        # Unexpected error - log and continue
                        error_type = type(e).__name__
                        current_status = f"EXCEPTION_{error_type}"

                        if current_status != last_status:
                            logger.warning(f"   âš ï¸  Unexpected error: {error_type}: {str(e)[:100]}")
                            last_status = current_status

                        if current_time - last_log_time > 10:
                            logger.debug(f"   ðŸ” Still checking... {int(elapsed)}s elapsed ({error_type})")
                            last_log_time = current_time

                    time.sleep(0.5)

                # ============================================================
                # TIMEOUT CHECK
                # ============================================================
                if not django_ready:
                    logger.error("=" * 70)
                    logger.error(f"âŒ DJANGO STARTUP TIMEOUT (after {max_wait}s)")
                    logger.error("=" * 70)
                    logger.error(f"   Total attempts: {attempts}")
                    logger.error(f"   Process alive: {'âœ… Yes' if django_process.is_alive() else 'âŒ No'}")

                    if port_open_time:
                        logger.error(f"   Port opened: âœ… Yes (but HTTP never responded)")
                        logger.error(f"   Time since port opened: {int(time.time() - port_open_time)}s")
                    else:
                        logger.error(f"   Port opened: âŒ No (Django never started listening)")

                    logger.error("")
                    logger.error("ðŸ“„ DJANGO LOG FILE:")
                    logger.error(f"   Location: {django_log}")

                    # Show last part of log
                    try:
                        with open(django_log, 'r') as f:
                            lines = f.readlines()
                            if lines:
                                logger.error("")
                                logger.error("   LAST 30 LINES:")
                                logger.error("   " + "-" * 66)
                                for line in lines[-30:]:
                                    logger.error(f"   {line.rstrip()}")
                                logger.error("   " + "-" * 66)
                            else:
                                logger.error("   (Log file is empty)")
                    except Exception as log_err:
                        logger.error(f"   (Could not read log: {log_err})")

                    logger.error("")
                    logger.error("ðŸ’¡ TROUBLESHOOTING:")
                    logger.error("   1. Check if port is already in use")
                    logger.error("   2. Check Django settings for errors")
                    logger.error("   3. Try running migrations manually")
                    logger.error("   4. Check for missing dependencies")
                    logger.error("=" * 70)

                    return False

                # ====================================================================
                # SUCCESS
                # ====================================================================
                logger.info("="*70)
                logger.info("âœ… DJANGO WEB SERVER READY")
                logger.info("="*70)
                logger.info(f"Status:       Running")
                logger.info(f"PID:          {django_process.pid}")
                logger.info(f"Port:         {port}")
                logger.info(f"URL:          http://127.0.0.1:{port}/")
                logger.info(f"Admin:        http://127.0.0.1:{port}/admin/")
                logger.info(f"Log:          {django_log}")
                logger.info("="*70)

                # Emit progress

                try:
                    self.progress_update.emit("Django ready! ðŸš€", 70)
                except:
                    pass

                # ====================================================================
                # ADDITIONAL WARMUP - Wait for Django to be fully ready
                # ====================================================================

                logger.info("âœ… Django HTTP responding - waiting for full warmup...")
                logger.info("   (This ensures first page load works correctly)")

                # Give Django 3-5 more seconds to fully initialize all apps
                time.sleep(5)

                # Try one more request to ensure it's truly ready
                try:
                    request = urllib.request.Request(
                        f'http://127.0.0.1:{port}/',
                        headers={'User-Agent': 'Cirqen-Warmup/1.0'}
                    )
                    urllib.request.urlopen(request, timeout=5)
                    logger.info("ðŸ”¥ Django fully warmed up and ready")
                except:
                    logger.warning("âš ï¸  Warmup request failed, but continuing...")

                return True


            except Exception as e:
                logger.error(f"âŒ Django startup error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False
        def start_celery(self):
            """
            Start Celery worker using system Python from virtual environment
            FIXED: Correctly finds Python even when frozen with PyInstaller
            """
            try:
                celery_log = DATA_PATH / 'logs' / 'celery.log'
                redis_port = self.port_manager.get_port('redis')

                logger.info("=" * 70)
                logger.info("STARTING CELERY WORKER (SUBPROCESS MODE)")
                logger.info("=" * 70)
                logger.info(f"Redis Port: {redis_port}")
                logger.info(f"Broker: redis://127.0.0.1:{redis_port}/2")
                logger.info(f"Log File: {celery_log}")
                logger.info("=" * 70)

                # Log rotation handled automatically by RotatingFileHandler

                # Set environment variables
                env = os.environ.copy()
                env["CELERY_BROKER_URL"] = f"redis://127.0.0.1:{redis_port}/2"
                env["CELERY_RESULT_BACKEND"] = f"redis://127.0.0.1:{redis_port}/2"
                env["DJANGO_SETTINGS_MODULE"] = "Equiper.settings"
                env["PYTHONUNBUFFERED"] = "1"

                # CRITICAL: Skip instance lock for Celery subprocess
                env["CIRQEN_SKIP_INSTANCE_LOCK"] = "1"

                # Database config
                env['POSTGRES_LOCAL_HOST'] = str(self.db_config['host'])
                env['POSTGRES_LOCAL_PORT'] = str(self.db_config['port'])
                env['POSTGRES_LOCAL_DATABASE'] = str(self.db_config['database'])
                env['POSTGRES_LOCAL_USER'] = str(self.db_config['user'])
                env['POSTGRES_LOCAL_PASSWORD'] = str(self.db_config['password'])

                logger.info(f"✅ Environment variables set")

                # Open log file
                log_file = open(celery_log, 'a')
                log_file.write(f"\n{'='*70}\n")
                log_file.write(f"Celery worker startup at {datetime.now().isoformat()}\n")
                log_file.write(f"Broker: redis://127.0.0.1:{redis_port}/2\n")
                log_file.write(f"{'='*70}\n\n")
                log_file.flush()

                # Find Python executable
                python_exe = self._find_python_executable()
                logger.info(f"Using Python: {python_exe}")

                # Celery worker command
                celery_cmd = [
                    python_exe,
                    '-m', 'celery',
                    '-A', 'Equiper.celery:app',
                    'worker',
                    '--loglevel=INFO',
                    '--pool=solo',
                    '--concurrency=1',
                    '--logfile', str(celery_log),
                    '--max-tasks-per-child=1000'
                ]

                logger.info(f"Command: {' '.join(celery_cmd)}")
                logger.info(f"Working directory: {APPLICATION_PATH}")

                # Start Celery process
                process = subprocess.Popen(
                    celery_cmd,
                    stdout=log_file,
                    stderr=log_file,
                    cwd=str(APPLICATION_PATH),
                    env=env
                )

                self.processes.append(('celery', process, log_file))

                logger.info(f"✅ Celery worker process started (PID: {process.pid})")
                logger.info(f"   📋 Logs: {celery_log}")

                # Wait a moment and check if process is still running
                time.sleep(3)

                if process.poll() is None:
                    logger.info("✅ Celery worker process verified running")
                    logger.info("")
                    logger.info("🔧 CELERY WORKER FEATURES:")
                    logger.info("   • ⚡ Background task processing")
                    logger.info("   • 📅 Scheduled periodic tasks")
                    logger.info("   • 🔄 Async job queue")
                    logger.info("   • 📊 Task result storage")
                    logger.info("   • 🔍 Task monitoring")
                    logger.info("=" * 70)
                    return True
                else:
                    exit_code = process.poll()
                    logger.error(f"❌ Celery worker process died immediately (exit code: {exit_code})")
                    logger.error("   Check log file for details")

                    # Show last lines of log
                    try:
                        with open(celery_log, 'r') as f:
                            lines = f.readlines()
                            if lines:
                                logger.error("")
                                logger.error("   Last 30 lines of log:")
                                for line in lines[-30:]:
                                    logger.error(f"      {line.rstrip()}")
                    except:
                        pass

                    return False

            except Exception as e:
                logger.error(f"❌ Celery worker startup error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False


        def start_celery_beat(self):
            """
            Start Celery Beat scheduler for periodic tasks
            """
            try:
                celery_beat_log = DATA_PATH / 'logs' / 'celery_beat.log'
                redis_port = self.port_manager.get_port('redis')

                logger.info("=" * 70)
                logger.info("STARTING CELERY BEAT SCHEDULER")
                logger.info("=" * 70)
                logger.info(f"Redis Port: {redis_port}")
                logger.info(f"Log File: {celery_beat_log}")
                logger.info("=" * 70)

                # Log rotation handled automatically by RotatingFileHandler

                # Set environment variables
                env = os.environ.copy()
                env["CELERY_BROKER_URL"] = f"redis://127.0.0.1:{redis_port}/2"
                env["CELERY_RESULT_BACKEND"] = f"redis://127.0.0.1:{redis_port}/2"
                env["DJANGO_SETTINGS_MODULE"] = "Equiper.settings"
                env["PYTHONUNBUFFERED"] = "1"

                # CRITICAL: Skip instance lock for Celery subprocess
                env["CIRQEN_SKIP_INSTANCE_LOCK"] = "1"

                # Database config
                env['POSTGRES_LOCAL_HOST'] = str(self.db_config['host'])
                env['POSTGRES_LOCAL_PORT'] = str(self.db_config['port'])
                env['POSTGRES_LOCAL_DATABASE'] = str(self.db_config['database'])
                env['POSTGRES_LOCAL_USER'] = str(self.db_config['user'])
                env['POSTGRES_LOCAL_PASSWORD'] = str(self.db_config['password'])

                logger.info(f"✅ Environment variables set")

                # Open log file
                log_file = open(celery_beat_log, 'a')
                log_file.write(f"\n{'='*70}\n")
                log_file.write(f"Celery Beat scheduler startup at {datetime.now().isoformat()}\n")
                log_file.write(f"{'='*70}\n\n")
                log_file.flush()

                # Find Python executable
                python_exe = self._find_python_executable()
                logger.info(f"Using Python: {python_exe}")

                # Celery Beat command
                celery_beat_cmd = [
                    python_exe,
                    '-m', 'celery',
                    '-A', 'Equiper.celery:app',
                    'beat',
                    '--loglevel=INFO',
                    '--logfile', str(celery_beat_log),
                    '--pidfile', str(DATA_PATH / 'celery_beat.pid'),
                    '--schedule', str(DATA_PATH / 'celerybeat-schedule.db')
                ]

                logger.info(f"Command: {' '.join(celery_beat_cmd)}")
                logger.info(f"Working directory: {APPLICATION_PATH}")

                # Start Celery Beat process
                process = subprocess.Popen(
                    celery_beat_cmd,
                    stdout=log_file,
                    stderr=log_file,
                    cwd=str(APPLICATION_PATH),
                    env=env
                )

                self.processes.append(('celery_beat', process, log_file))

                logger.info(f"✅ Celery Beat scheduler started (PID: {process.pid})")
                logger.info(f"   📋 Logs: {celery_beat_log}")

                # Wait a moment and check if process is still running
                time.sleep(2)

                if process.poll() is None:
                    logger.info("✅ Celery Beat scheduler verified running")
                    logger.info("")
                    logger.info("📅 CELERY BEAT FEATURES:")
                    logger.info("   • ⏰ Automated task scheduling")
                    logger.info("   • 📧 Periodic email notifications")
                    logger.info("   • 📊 Automated reports")
                    logger.info("   • 🧹 Database maintenance")
                    logger.info("   • 🔔 Calibration reminders")
                    logger.info("=" * 70)
                    return True
                else:
                    exit_code = process.poll()
                    logger.error(f"❌ Celery Beat died immediately (exit code: {exit_code})")
                    logger.error("   Check log file for details")

                    try:
                        with open(celery_beat_log, 'r') as f:
                            lines = f.readlines()
                            if lines:
                                logger.error("")
                                logger.error("   Last 30 lines of log:")
                                for line in lines[-30:]:
                                    logger.error(f"      {line.rstrip()}")
                    except:
                        pass

                    return False

            except Exception as e:
                logger.error(f"❌ Celery Beat startup error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False


        def _find_python_executable(self):
            """
            Find the correct Python executable
            Returns path to Python that can run Celery
            """
            # Check if we're in a virtual environment
            venv_python = os.environ.get('VIRTUAL_ENV')
            if venv_python:
                if sys.platform == 'win32':
                    python_exe = os.path.join(venv_python, 'Scripts', 'python.exe')
                else:
                    python_exe = os.path.join(venv_python, 'bin', 'python')

                if os.path.exists(python_exe):
                    logger.info(f"Found virtualenv Python: {python_exe}")
                    return python_exe

            # Try to find system Python
            import shutil

            # Try python3 first, then python
            for python_name in ['python3', 'python']:
                python_exe = shutil.which(python_name)
                if python_exe:
                    logger.info(f"Found system Python: {python_exe}")
                    return python_exe

            # Fallback to sys.executable (works when not frozen)
            logger.warning(f"Using sys.executable as fallback: {sys.executable}")
            return sys.executable

        def start_sync_agent(self):
            """
            Start Sync Agent as a background thread.

            KEY FIX: Before calling sync_agent.main() we forcibly route every
            logger that the sync subsystem uses (sync_agent, sync_agent_optimized,
            mirror_sync, data_checker_client, RedisQueueSync, ...) to the same
            log file at DEBUG level.  Previously only the SyncAgentThread
            wrapper logger wrote to the file, so everything inside main() was
            silently dropped (the module-level LOG was set to WARNING and had
            no file handler).
            """
            try:
                sync_log = DATA_PATH / 'logs' / 'sync_agent.log'
                data_dir = DATA_PATH

                logger.info("=" * 70)
                logger.info("STARTING SYNC AGENT (THREAD MODE)")
                logger.info("=" * 70)
                logger.info(f"Data Directory: {data_dir}")
                logger.info(f"Log File: {sync_log}")
                logger.info("=" * 70)

                # Log rotation handled automatically by RotatingFileHandler

                # -- Import sync agent module -----------------------------------------
                try:
                    sys.path.insert(0, str(APPLICATION_PATH))
                    sync_agent_module = None

                    try:
                        import sync_agent as sync_agent_module
                        logger.info("Imported sync_agent from root")
                    except ImportError:
                        try:
                            from sync import sync_agent as sync_agent_module
                            logger.info("Imported from sync.sync_agent")
                        except ImportError:
                            sys.path.insert(0, str(APPLICATION_PATH / '_internal'))
                            try:
                                import sync_agent as sync_agent_module
                                logger.info("Imported from _internal/sync_agent")
                            except ImportError:
                                from sync import sync_agent as sync_agent_module
                                logger.info("Imported from _internal/sync/sync_agent")

                    if not sync_agent_module:
                        logger.error("Could not import sync_agent module")
                        return False

                except Exception as e:
                    logger.error(f"Failed to import sync agent: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    return False

                # -- Thread target ----------------------------------------------------
                def run_sync_agent():
                    """
                    Run sync_agent.main() inside a background thread.

                    Steps
                    -----
                    1. Create one shared FileHandler pointing at sync_agent.log.
                    2. Apply it to EVERY logger the sync subsystem uses so all
                       messages (DEBUG and above) land in the file.
                    3. Call sync_agent.main() with --data-dir and --log-level DEBUG.
                    4. Restore sys.argv and flush/close the handler on exit.
                    """
                    import logging as _logging

                    # 1. Shared file handler -----------------------------------------
                    sync_log.parent.mkdir(parents=True, exist_ok=True)
                    _fmt = _logging.Formatter(
                        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                    )
                    from logging.handlers import RotatingFileHandler as _RotFH
                    _file_handler = _RotFH(
                        str(sync_log), maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
                    )
                    _file_handler.setLevel(_logging.WARNING)
                    _file_handler.setFormatter(_fmt)

                    # 2. Apply to every sync-subsystem logger — file only, no terminal
                    _sync_logger_names = [
                        'SyncAgentThread',
                        'sync_agent',
                        'sync_agent_optimized',
                        'sync.sync_agent',
                        'mirror_sync',
                        'sync.mirror',
                        'data_checker_client',
                        'data_checker',
                        'RedisQueueSync',
                        '',                   # root logger catches everything else
                    ]

                    for _name in _sync_logger_names:
                        _lg = _logging.getLogger(_name)
                        _lg.setLevel(_logging.WARNING)
                        # Remove ALL handlers (including any StreamHandlers)
                        # then attach only the file handler — nothing goes to terminal
                        _lg.handlers = [
                            h for h in _lg.handlers
                            if isinstance(h, _logging.FileHandler)
                        ]
                        _lg.addHandler(_file_handler)
                        _lg.propagate = False

                    # Silence noisy third-party libs
                    for _noisy in ('urllib3', 'requests', 'psycopg2'):
                        _logging.getLogger(_noisy).setLevel(_logging.WARNING)

                    _tl = _logging.getLogger('SyncAgentThread')

                    _tl.info("=" * 70)
                    _tl.info("SYNC AGENT THREAD STARTED")
                    _tl.info("=" * 70)
                    _tl.info(f"Thread ID:      {threading.current_thread().ident}")
                    _tl.info(f"Data Directory: {data_dir}")
                    _tl.info(f"Log file:       {sync_log}")
                    _tl.info("Log level:      DEBUG — all messages enabled")
                    _tl.info("=" * 70)

                    # 3. Call main() -------------------------------------------------
                    original_argv = sys.argv.copy()
                    sys.argv = [
                        'sync_agent.py',
                        '--data-dir', str(data_dir),
                        '--log-level', 'WARNING',
                    ]
                    _tl.info(f"Calling sync_agent.main() with args: {sys.argv}")

                    try:
                        sync_agent_module.main()
                    except Exception as exc:
                        import traceback as _tb
                        _tl.error(f"Sync agent error: {exc}")
                        _tl.error(_tb.format_exc())
                    finally:
                        # 4. Restore and flush --------------------------------------
                        sys.argv = original_argv
                        _tl.info("Sync agent thread exiting")
                        try:
                            _file_handler.flush()
                            _file_handler.close()
                        except Exception:
                            pass

                # -- Spawn thread -----------------------------------------------------
                sync_thread = threading.Thread(
                    target=run_sync_agent,
                    name='SyncAgentThread',
                    daemon=True,
                )
                sync_thread.start()

                self.processes.append(('sync_agent', sync_thread, None))
                logger.info(f"Sync agent thread started (ID: {sync_thread.ident})")
                logger.info(f"   Logs: {sync_log}")

                time.sleep(2)

                if sync_thread.is_alive():
                    logger.info("Sync agent thread verified running")
                    logger.info("")
                    logger.info("SYNC AGENT FEATURES:")
                    logger.info("   Instant uploads (1-second change detection)")
                    logger.info("   Periodic downloads from HQ (every 15s)")
                    logger.info("   Certificate synchronization")
                    logger.info("   Smart delete with cascade support")
                    logger.info("   Automatic offline/online handling")
                    logger.info("   Triple redundancy state management")
                    logger.info("   Full DEBUG logging -> tail sync_agent.log to see everything")
                    logger.info("=" * 70)
                    return True
                else:
                    logger.error("Sync agent thread died immediately")
                    logger.error("   Check log file for details")
                    try:
                        with open(sync_log, 'r') as _f:
                            _lines = _f.readlines()
                            if _lines:
                                logger.error("   Last 30 lines of sync_agent.log:")
                                for _line in _lines[-30:]:
                                    logger.error(f"      {_line.rstrip()}")
                    except Exception:
                        pass
                    return False

            except Exception as e:
                logger.error(f"Sync agent startup error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False

        def start_update_manager(self):
            """
            Start Update Manager as a background thread
            Checks for application updates non-blockingly with automatic reconnection
            """
            try:
                update_log = DATA_PATH / 'logs' / 'update_manager.log'
                data_dir = DATA_PATH

                logger.info("STARTING UPDATE MANAGER (THREAD MODE)")
                logger.info(f"Data Directory: {data_dir}")
                logger.info(f"Log File: {update_log}")

                # Log rotation handled automatically by RotatingFileHandler

                # Import update manager module (kept for compatibility only)
                try:
                    from sync.update_manager import UpdateManager
                    logger.info("✅ UpdateManager class imported (legacy)")
                except ImportError:
                    pass  # Not needed — direct API mode handles everything

                # Determine app root
                if getattr(sys, 'frozen', False):
                    app_root = Path(sys.executable).parent / "_internal"
                else:
                    app_root = APPLICATION_PATH

                # Create wrapper function for background thread
                def run_update_manager():
                    """
                    Self-contained update checker.
                    Talks directly to the HQ FastAPI server — no update_client
                    dependency required.  Uses the same endpoints as the server:
                      GET /health/
                      GET /api/updates/latest/?current_version=X&machine_id=Y
                      GET /api/updates/download/{version}/
                    Update packages are .zip files with:
                      manifest.json
                      files/<relative-path-in-app-root>
                    """
                    import hashlib
                    import json as _json
                    import logging
                    import shutil
                    import time as _time
                    import urllib.error as _uerr
                    import urllib.request as _req
                    import zipfile

                    # ── Logging ────────────────────────────────────────────────
                    update_logger = logging.getLogger('UpdateManagerThread')
                    update_logger.setLevel(logging.WARNING)
                    update_logger.propagate = False
                    update_logger.handlers.clear()
                    from logging.handlers import RotatingFileHandler as _RotFH
                    _fh = _RotFH(str(update_log), maxBytes=5*1024*1024, backupCount=3)
                    _fh.setFormatter(logging.Formatter(
                        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                    ))
                    update_logger.addHandler(_fh)

                    update_logger.info("=" * 70)
                    update_logger.info("UPDATE MANAGER THREAD STARTED (direct API mode)")
                    update_logger.info("=" * 70)

                    # ── Config ─────────────────────────────────────────────────
                    _server_url  = "https://cirqen-hq.onrender.com"
                    _api_key     = "58f8605e1966ce148990c477dcb99d02"
                    _interval_h  = 24

                    # Read version from version.txt written after each update is applied.
                    # Falls back to the hardcoded baseline so the app always works even on
                    # a fresh install that has never been updated.
                    _version_file = app_root / 'version.txt'
                    try:
                        _current_ver = _version_file.read_text().strip()
                    except Exception:
                        _current_ver = "1.0.0"

                    try:
                        from sync.config import CirqenConfig
                        _cfg        = CirqenConfig(data_dir)
                        _server_url = _cfg.get('update.server_url', _server_url)
                        _api_key    = _cfg.get('update.api_key',    _api_key)
                        _interval_h = int(_cfg.get('update.check_interval_hours', _interval_h))
                    except Exception as _ce:
                        update_logger.warning(f"Config load failed, using defaults: {_ce}")

                    _interval_s = max(3600, _interval_h * 3600)   # minimum 1 h

                    update_logger.info(f"  Server  : {_server_url}")
                    update_logger.info(f"  Version : {_current_ver}")
                    update_logger.info(f"  Interval: every {_interval_h}h")

                    # ── Staging dirs ───────────────────────────────────────────
                    _staging_dir = data_dir / 'update_staging'
                    _backup_dir  = data_dir / 'update_backups'
                    _staging_dir.mkdir(parents=True, exist_ok=True)
                    _backup_dir.mkdir(parents=True, exist_ok=True)

                    # ── Status file (UI reads this) ────────────────────────────
                    _status_file = data_dir / 'sync_state' / 'update_status.json'

                    def _write_status(**kwargs):
                        try:
                            existing = {}
                            if _status_file.exists():
                                try:
                                    existing = _json.loads(_status_file.read_text())
                                except Exception:
                                    pass
                            existing.update(kwargs)
                            existing['updated_at'] = datetime.now().isoformat()
                            _status_file.write_text(_json.dumps(existing, indent=2))
                        except Exception as _we:
                            update_logger.debug(f"write_status failed: {_we}")

                    # ── HTTP helpers ───────────────────────────────────────────
                    def _get_json(path, timeout=8):
                        """GET JSON from server with API key header."""
                        url = _server_url.rstrip('/') + path
                        rq  = _req.Request(url, headers={'X-Api-Key': _api_key})
                        with _req.urlopen(rq, timeout=timeout) as resp:
                            return _json.loads(resp.read().decode())

                    def _server_alive(timeout=5):
                        """Ping /health/ — any HTTP reply means the server is up."""
                        try:
                            _req.urlopen(
                                _server_url.rstrip('/') + '/health/',
                                timeout=timeout
                            )
                            return True
                        except _uerr.HTTPError:
                            return True   # even 4xx = server is up
                        except Exception:
                            return False

                    def _sha256(path):
                        h = hashlib.sha256()
                        with open(path, 'rb') as f:
                            for chunk in iter(lambda: f.read(65536), b''):
                                h.update(chunk)
                        return h.hexdigest()

                    # ── Machine ID ─────────────────────────────────────────────
                    try:
                        from sync.device_id_generator import get_or_create_client_id
                        _machine_id = get_or_create_client_id()
                    except Exception:
                        import uuid
                        _machine_id = str(uuid.getnode())

                    # ── Download & apply ───────────────────────────────────────
                    def _download_and_apply(version, download_url, expected_checksum):
                        """
                        Download the .zip, verify checksum, extract files/ → app_root.
                        Backs up every file that will be overwritten.
                        Returns True on success.
                        """
                        zip_path = _staging_dir / f"cirqen_update_v{version}.zip"
                        update_logger.info(f"⬇️  Downloading v{version} from {download_url}")
                        _write_status(downloading=True, download_progress=0,
                                      new_version=version, server_available=True)

                        try:
                            # Download with progress
                            rq = _req.Request(
                                download_url,
                                headers={'X-Api-Key': _api_key}
                            )
                            with _req.urlopen(rq, timeout=120) as resp:
                                total = int(resp.headers.get('Content-Length', 0))
                                downloaded = 0
                                with open(zip_path, 'wb') as out:
                                    while True:
                                        chunk = resp.read(65536)
                                        if not chunk:
                                            break
                                        out.write(chunk)
                                        downloaded += len(chunk)
                                        if total:
                                            pct = int(downloaded * 100 / total)
                                            _write_status(
                                                downloading=True,
                                                download_progress=pct
                                            )

                            update_logger.info(f"   Downloaded {downloaded // 1024} KB")

                            # Verify checksum
                            if expected_checksum:
                                actual = _sha256(zip_path)
                                if actual != expected_checksum:
                                    update_logger.error(
                                        f"❌ Checksum mismatch! "
                                        f"expected={expected_checksum[:16]}… "
                                        f"got={actual[:16]}…"
                                    )
                                    zip_path.unlink(missing_ok=True)
                                    return False
                                update_logger.info("   ✅ Checksum verified")

                            # Extract & apply
                            _write_status(downloading=True, download_progress=99)
                            backup_ts  = datetime.now().strftime('%Y%m%d_%H%M%S')
                            backup_dir = _backup_dir / f"backup_{backup_ts}"
                            backup_dir.mkdir(parents=True, exist_ok=True)
                            applied = 0

                            with zipfile.ZipFile(zip_path, 'r') as zf:
                                names = zf.namelist()
                                for name in names:
                                    if not name.startswith('files/') or name.endswith('/'):
                                        continue
                                    rel_path = name[len('files/'):]
                                    dest = app_root / rel_path
                                    # Back up existing file
                                    if dest.exists():
                                        bak = backup_dir / rel_path
                                        bak.parent.mkdir(parents=True, exist_ok=True)
                                        shutil.copy2(dest, bak)
                                    # Write new file
                                    dest.parent.mkdir(parents=True, exist_ok=True)
                                    dest.write_bytes(zf.read(name))
                                    applied += 1

                            update_logger.info(f"   ✅ Applied {applied} files")

                            # Write restart sentinel
                            sentinel = app_root / '.restart_required'
                            sentinel.write_text(
                                _json.dumps({
                                    'version': version,
                                    'applied_at': datetime.now().isoformat()
                                })
                            )

                            # Write version.txt so the next boot reads the correct
                            # version and does NOT see another update available.
                            try:
                                (app_root / 'version.txt').write_text(version)
                                update_logger.info(f"   ✅ version.txt updated to {version}")
                            except Exception as _ve:
                                update_logger.warning(f"   ⚠️  Could not write version.txt: {_ve}")

                            zip_path.unlink(missing_ok=True)
                            _write_status(
                                downloading=False,
                                download_progress=100,
                                update_ready=True,
                                update_available=False,
                                new_version=version,
                                server_available=True,
                                last_check=datetime.now().isoformat()
                            )
                            update_logger.info(
                                f"✅ Update v{version} applied — restart required"
                            )
                            return True

                        except Exception as e:
                            update_logger.error(f"❌ Download/apply failed: {e}")
                            import traceback
                            update_logger.error(traceback.format_exc())
                            try:
                                zip_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            _write_status(
                                downloading=False,
                                server_available=True,
                                error=str(e)
                            )
                            return False

                    # ── Single check cycle ─────────────────────────────────────
                    def _do_check():
                        """
                        One check→download→apply cycle.
                        Returns (server_ok, update_available, new_version).
                        """
                        # 1) Check if server alive
                        if not _server_alive():
                            update_logger.warning("⚠️  Update server unreachable")
                            _write_status(
                                server_available=False,
                                checking=False,
                                last_check=datetime.now().isoformat()
                            )
                            return False, False, None

                        # 2) Query latest version
                        try:
                            info = _get_json(
                                f'/api/updates/latest/'
                                f'?current_version={_current_ver}'
                                f'&machine_id={_machine_id}'
                            )
                            update_logger.info(
                                f"Server response: update_available="
                                f"{info.get('update_available')}, "
                                f"latest={info.get('latest_version') or info.get('version')}"
                            )
                        except Exception as e:
                            update_logger.warning(f"Version check failed: {e}")
                            # Server reachable (alive) but API call failed
                            _write_status(
                                server_available=True,
                                checking=False,
                                last_check=datetime.now().isoformat()
                            )
                            return True, False, None

                        _write_status(
                            server_available=True,
                            checking=False,
                            last_check=datetime.now().isoformat()
                        )

                        if not info.get('update_available'):
                            _write_status(
                                update_available=False,
                                new_version=None
                            )
                            update_logger.info(
                                f"✅ Up to date (v{_current_ver} is current)"
                            )
                            return True, False, None

                        # 3) Update available — download automatically
                        new_ver      = info['version']
                        download_url = info.get('download_url', '')
                        checksum     = info.get('checksum', '')

                        _write_status(
                            update_available=True,
                            new_version=new_ver
                        )
                        update_logger.info(f"🆕 Update available: v{new_ver}")

                        if download_url:
                            if _download_and_apply(new_ver, download_url, checksum):
                                _current_ver = new_ver

                        return True, True, new_ver

                    # ── Main loop ──────────────────────────────────────────────
                    # Write initial status so UI doesn't show stale "Offline"
                    _write_status(
                        server_available=False,
                        checking=True,
                        update_available=False,
                        downloading=False,
                        new_version=None,
                        last_check=None,
                        current_version=_current_ver,
                        server_url=_server_url
                    )

                    update_logger.info("🔍 Running startup update check...")
                    try:
                        _do_check()
                    except Exception as e:
                        update_logger.error(f"Startup check error: {e}")
                        _write_status(server_available=False, checking=False)

                    update_logger.info(
                        f"✅ Startup check complete — next check in {_interval_h}h"
                    )

                    # Background loop
                    while True:
                        try:
                            _time.sleep(_interval_s)
                            update_logger.info("🔄 Scheduled update check...")
                            _do_check()
                        except Exception as e:
                            update_logger.error(f"Scheduled check error: {e}")
                            _write_status(server_available=False, checking=False)

                # Start update manager in background thread
                import threading
                update_thread = threading.Thread(
                    target=run_update_manager,
                    name='UpdateManagerThread',
                    daemon=True
                )
                update_thread.start()

                # Store thread reference
                self.processes.append(('update_manager', update_thread, None))

                logger.info(f"✅ Update manager thread started (ID: {update_thread.ident})")
                logger.info(f"   📋 Logs: {update_log}")

                # Wait a moment to check if thread is alive
                time.sleep(2)

                if update_thread.is_alive():
                    logger.info("✅ Update manager thread verified running")
                    return True
                else:
                    logger.error("❌ Update manager thread died immediately")
                    return False

            except ImportError as e:
                logger.warning(f"⚠️  UpdateManager module not available: {e}")
                logger.info("   Update functionality will not be available")
                return False

            except Exception as e:
                logger.error(f"❌ Failed to start update manager: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False

        def stop_services(self):
            """Stop all services - UPDATED for thread-based Celery and sync agent"""
            logger.info("Shutting down services...")

            for name, process, log_file in reversed(self.processes):
                try:
                    logger.info(f"Stopping {name}...")

                    # Leave PostgreSQL running if managed by systemd
                    if name == 'postgres_local':
                        self._pg_systemd.stop()  # logs intent, actual no-op
                        if self._pg_systemd.is_installed:
                            logger.info("   postgres_local left running (systemd)")
                            if log_file:
                                try: log_file.close()
                                except Exception: pass
                            continue


                    # Special handling for sync agent (thread-based)
                    if name == 'sync_agent':
                        logger.info(f"   Sending graceful shutdown signal to sync agent...")

                        # For threads, we can't terminate them directly
                        if hasattr(process, 'is_alive'):
                            if process.is_alive():
                                logger.info(f"   Waiting for sync agent thread to finish...")

                                # Give thread time to finish current work
                                import threading

                                # Wait up to 15 seconds for graceful shutdown
                                process.join(timeout=15)

                                if process.is_alive():
                                    logger.warning(f"   ⚠️  Sync agent thread did not stop within timeout")
                                    logger.warning(f"   Thread will be abandoned (daemon mode)")
                                else:
                                    logger.info(f"   ✅ Sync agent stopped gracefully")
                            else:
                                logger.info(f"   ✅ Sync agent already stopped")

                    # Special handling for Celery (subprocess-based)
                    elif name == 'celery':
                        if hasattr(process, 'terminate'):
                            process.terminate()

                            try:
                                logger.info(f"   Waiting for Celery to stop...")
                                process.wait(timeout=10)
                                logger.info(f"   ✅ Celery stopped gracefully")
                            except:
                                logger.warning(f"   ⚠️  Celery did not stop, forcing...")
                                if hasattr(process, 'kill'):
                                    process.kill()
                                    process.wait()
                                logger.info(f"   ✅ Celery force killed")

                    # Special handling for Django (multiprocessing)
                    elif name == 'django':
                        if hasattr(process, 'terminate'):
                            process.terminate()

                            try:
                                logger.info(f"   Waiting for Django to stop...")
                                process.join(timeout=10)
                                logger.info(f"   ✅ Django stopped gracefully")
                            except:
                                logger.warning(f"   ⚠️  Django did not stop, forcing...")
                                if hasattr(process, 'kill'):
                                    process.kill()
                                    process.join()
                                logger.info(f"   ✅ Django force killed")

                    # Normal handling for subprocess-based services (PostgreSQL, Redis)
                    else:
                        if hasattr(process, 'terminate'):
                            process.terminate()

                            try:
                                if hasattr(process, 'wait'):
                                    process.wait(timeout=10)
                                elif hasattr(process, 'join'):
                                    process.join(timeout=10)

                                logger.info(f"   ✅ {name} stopped gracefully")

                            except:
                                logger.warning(f"   ⚠️  {name} did not stop, forcing...")
                                if hasattr(process, 'kill'):
                                    process.kill()
                                    if hasattr(process, 'wait'):
                                        process.wait()
                                    elif hasattr(process, 'join'):
                                        process.join()
                                logger.info(f"   ✅ {name} force killed")

                    # Close log file if exists
                    if log_file:
                        try:
                            log_file.close()
                        except:
                            pass

                except Exception as e:
                    logger.error(f"Error stopping {name}: {e}")

            self.processes.clear()
            logger.info("All services stopped")

    class ServiceThread(QThread):
        """Thread for service management"""

        def __init__(self, service_manager):
            super().__init__()
            self.service_manager = service_manager

        def run(self):
            try:
                self.service_manager.start_services()
            except Exception as e:
                logger.error(f"❌ ServiceThread crashed: {e}")
                import traceback
                logger.error(traceback.format_exc())
                try:
                    self.service_manager.service_error.emit(str(e))
                except Exception:
                    pass


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
                    except:
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
            self.sync_online_label = QLabel("⚫ Sync")
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
            self.sync_online_label.setToolTip("Sync agent: checking connection to HQ server...")
            bottom_layout.addWidget(self.sync_online_label)

            bottom_layout.addStretch()

            # Update Status + Restart button (no version badge shown to users)
            self.update_status_label = QLabel("⚙️ Checking...")
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
                downloading       = status.get('downloading', False)
                update_available  = status.get('update_available', False)
                update_ready      = status.get('update_ready', False)
                new_version       = status.get('new_version') or ''
                last_check        = _fmt_time(status.get('last_check', ''))
                progress          = status.get('download_progress', 0)
                last_check_text   = f"@ {last_check}" if last_check else ''

                if update_ready:
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
                        bg     = 'rgba(34,197,94,0.10)'
                        border = 'rgba(34,197,94,0.25)'
                        label_text = f"{dot} Online"
                        if pending:
                            label_text = f"{dot} Syncing ({pending})"
                        tip = (
                            f"HQ server: connected\n"
                            f"Pending changes: {pending}\n"
                            f"Last sync: {last_sync_text or 'unknown'}"
                        )
                    else:
                        dot    = '🔴'
                        color  = '#ef4444'
                        bg     = 'rgba(239,68,68,0.10)'
                        border = 'rgba(239,68,68,0.25)'
                        label_text = f"{dot} Offline"
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


    def main():
        """
        ENHANCED: Main entry point with automatic cleanup, dynamic port allocation,
        and update checking
        """

        # ====================================================================
        # CRITICAL: Set multiprocessing start method BEFORE QApplication.
        # Qt (PySide6) is NOT fork-safe.  Using 'fork' (the old Linux default)
        # after a QApplication exists causes the Django child process to crash
        # immediately — before it can open its log file — producing a silent
        # failure.  'spawn' starts a clean Python interpreter with no inherited
        # Qt state.  Must be called before any Process() is created AND before
        # QApplication is instantiated (Python 3.14 changed the Linux default
        # from 'fork' to 'forkserver', but 'spawn' is the only safe choice
        # when Qt is in the parent).
        # ====================================================================
        import multiprocessing as _mp
        try:
            _mp.set_start_method('spawn', force=True)
            logger.info("multiprocessing start method set to 'spawn'")
        except RuntimeError:
            # Already set (e.g. called twice in tests) — not a problem
            pass

        # Create QApplication FIRST
        app = QApplication(sys.argv)
        app.setApplicationName("Cirqen")
        app.setOrganizationName("B12 Technologies")

        # Set application icon if available
        icon_path = APPLICATION_PATH / "assets" / "icons" / "app_icon.ico"
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))

        logger.info("="*70)
        logger.info("🚀 CIRQEN APPLICATION - ENHANCED EDITION")
        logger.info("="*70)
        logger.info(f"Platform: {sys.platform}")
        logger.info(f"Python: {sys.version.split()[0]}")
        logger.info(f"PID: {os.getpid()}")
        logger.info(f"Data Path: {DATA_PATH}")
        logger.info("="*70)

        # ============================
        # STEP 1: STARTUP CLEANUP
        # ============================
        logger.info("STEP 1: Running startup cleanup...")
        perform_startup_cleanup()

        # ============================
        # STEP 2: PORT ALLOCATION
        # ============================
        logger.info("STEP 2: Allocating ports...")
        try:
            port_manager.allocate_ports()
        except RuntimeError as e:
            logger.error(f"❌ Port allocation failed: {e}")
            return 1

        # ============================
        # STEP 2b: INSTANCE LOCK
        # ============================
        instance_lock = None
        if not should_skip_instance_lock():
            LOCK_FILE = DATA_PATH / 'cirqen.lock'
            instance_lock = SingleInstanceLock(LOCK_FILE, port_manager)
            lock_success, lock_message = instance_lock.acquire()
            if not lock_success:
                logger.error(f"❌ Instance lock failed: {lock_message}")
                return 1
            logger.info("✅ Instance lock acquired")
        else:
            logger.info("⚠️  Skipping instance lock (subprocess/migration mode)")

        # ====================================================================
        # CLEANUP FUNCTION
        # ====================================================================
        def cleanup_on_exit():
            """Cleanup function called on exit"""
            logger.info("="*70)
            logger.info("CLEANUP ON EXIT")
            logger.info("="*70)

            if instance_lock:
                instance_lock.release()
            port_manager.cleanup_session()
            logger.info("✅ Cleanup complete")

        atexit.register(cleanup_on_exit)

        # Signal handlers
        # NOTE: Signal handlers must only use async-signal-safe operations.
        # Calling logger inside a signal handler causes RuntimeError (reentrant
        # write into BufferedWriter) if the signal fires mid-log-write.
        # Fix: use os.write(2, ...) for the signal context, then raise SystemExit
        # so that cleanup_on_exit() runs in normal thread context via atexit.
        def signal_handler(sig, frame):
            # os.write is async-signal-safe; logging/print to file is NOT
            msg = f"\nReceived signal {sig} — shutting down...\n".encode()
            os.write(2, msg)  # 2 = stderr fd, no buffering, no locks
            # Raising SystemExit here unwinds normally and triggers atexit,
            # which calls cleanup_on_exit() safely outside the signal context.
            raise SystemExit(0)

        signal.signal(signal.SIGINT, signal_handler)
        if sys.platform != 'win32':
            signal.signal(signal.SIGTERM, signal_handler)

        # ============================
        # STEP 3: APPLICATION STARTUP
        # ============================
        try:
            # Show splash screen
            splash = CustomSplashScreen(port_manager)
            splash.show()
            app.processEvents()

            # ====================================================================
            # STARTUP WARMUP & STATE CLEARING (with splash progress)
            # ====================================================================
            try:
                logger.info("🚀 Initiating startup warmup sequence...")

                # Create startup state manager
                startup_manager = StartupStateManager(DATA_PATH, logger)

                # Get last startup info
                last_startup = startup_manager.get_last_startup_info()
                if last_startup:
                    last_time = last_startup.get('last_startup', 'Unknown')
                    last_cleared = last_startup.get('cleared_items', 0)
                    logger.info(f"📅 Last startup: {last_time}")
                    logger.info(f"📊 Last session cleared: {last_cleared} items")

                # Clear stale states with splash feedback
                splash.update_progress("Getting things ready…", 5)
                app.processEvents()
                cleared_count = startup_manager.clear_stale_states()

                # Define progress callback for warmup
                def warmup_progress_callback(step_name, progress):
                    # Map progress to 10-40% range on splash
                    splash_progress = 10 + int((progress / 100) * 30)
                    splash.update_progress("Optimising your experience…", splash_progress)
                    app.processEvents()

                # Warmup application with progress feedback
                startup_manager.warmup_application(progress_callback=warmup_progress_callback)

                # Save current startup state
                startup_manager.save_startup_state()

                splash.update_progress("Almost there…", 45)
                app.processEvents()

                logger.info("="*70)
                logger.info(f"✅ STARTUP WARMUP COMPLETE")
                logger.info(f"   • Cleared: {cleared_count} stale items")
                logger.info(f"   • Warnings: {len(startup_manager.warnings)}")
                logger.info("="*70)

            except Exception as e:
                logger.warning(f"⚠️  Startup warmup encountered an issue: {e}")
                logger.info("Continuing with normal startup...")
                import traceback
                logger.debug(traceback.format_exc())

            # Check for first run
            first_run_setup = FirstRunSetup(port_manager)

            if first_run_setup.is_first_run():
                logger.info("STEP 3: First run detected - showing setup dialog")

                # Show setup dialog
                setup_dialog = SetupDialog()
                setup_thread = SetupThread(first_run_setup)

                # Connect signals
                first_run_setup.progress_update.connect(setup_dialog.update_progress)
                first_run_setup.log_message.connect(setup_dialog.add_log)

                setup_complete = [False, None]

                def on_setup_done(success, message):
                    setup_complete[0] = success
                    setup_complete[1] = message
                    setup_dialog.accept()

                first_run_setup.setup_complete.connect(on_setup_done)

                # Start setup
                setup_thread.start()
                setup_dialog.exec()
                setup_thread.wait()

                # Check result
                if not setup_complete[0]:
                    splash.close()
                    _themed_dialog("error",
                        "Setup didn’t complete",
                        "Something went wrong during the initial setup.",
                        f"Details: {setup_complete[1]}"
                    )
                    logger.error("❌ First-run setup failed")
                    cleanup_on_exit()
                    return 1
                else:
                    # Show success message
                    splash.close()
                    _themed_dialog("info",
                        "You’re all set!",
                        "Cirqen is ready to use. Your workspace has been created.",
                        setup_complete[1]
                    )
                    logger.info("✅ First-run setup completed")

                    # Show splash again
                    splash = CustomSplashScreen(port_manager)
                    splash.show()
                    app.processEvents()

            # ============================
            # STEP 4: START SERVICES
            # ============================
            logger.info("STEP 4: Starting services...")

            # Create service manager
            service_manager = ServiceManager(port_manager)
            service_manager.progress_update.connect(splash.update_progress)

            # Start services in thread
            service_thread = ServiceThread(service_manager)

            # Create main window
            main_window = MainWindow(port_manager, service_manager)

            def on_ready():
                """Called when ALL services are ready"""
                logger.info("=" * 70)
                logger.info("🎉 ALL SERVICES READY - WARMING UP DJANGO")
                logger.info("=" * 70)

                # DON'T show window yet - keep splash screen
                # Just update splash message
                splash.update_progress("You’re almost in…", 95)

                # Wait for Django to fully warm up
                def show_window_when_ready():
                    logger.info("🔔 Django warmed up - showing main window...")

                    # Detect screen and fit window before showing
                    screen = QApplication.primaryScreen()
                    screen_geometry = screen.availableGeometry()
                    main_window.setGeometry(screen_geometry)

                    # Close splash and show window maximized to fill full screen
                    splash.finish(main_window)
                    main_window.showMaximized()

                    # Load the URL immediately
                    django_url = f"http://127.0.0.1:{main_window.django_port}/"
                    logger.info(f"Loading URL: {django_url}")
                    main_window.web_view.setUrl(QUrl(django_url))

                    # Enable buttons
                    main_window.refresh_btn.setEnabled(True)
                    main_window.status_label.setText("🟢 System Online")

                    # Start status timers
                    main_window.update_status_timer.start(30000)
                    QTimer.singleShot(5000, main_window.update_update_status)
                    main_window.sync_status_timer.start(5000)
                    QTimer.singleShot(2000, main_window.update_sync_online_indicator)

                    logger.info("✅ Application ready and displayed")

                # Wait 5 seconds for Django to fully warm up
                QTimer.singleShot(5000, show_window_when_ready)

            def on_error(error):
                splash.close()
                logger.error(f"❌ Service error: {error}")
                _themed_dialog("error",
                    "Couldn’t start Cirqen",
                    "One of the background services failed to start.",
                    f"Details: {error}"
                )
                cleanup_on_exit()
                app.quit()

            service_manager.service_ready.connect(on_ready)
            service_manager.service_error.connect(on_error)

            # Start services
            service_thread.start()

            # ============================
            # STEP 5: RUN APPLICATION
            # ============================
            logger.info("STEP 5: Starting Qt event loop...")
            exit_code = app.exec()

            # Cleanup
            logger.info("Application exiting normally")
            service_manager.stop_services()
            service_thread.wait()

            return exit_code

        except Exception as e:
            logger.error(f"❌ Fatal error: {e}")
            import traceback
            logger.error(traceback.format_exc())

            _themed_dialog("error",
                "Something went wrong",
                "Cirqen encountered an unexpected problem and needs to close.",
                f"Details: {str(e)}"
            )
            return 1

        finally:
            # Ensure cleanup happens
            cleanup_on_exit()


# ============================
# ENTRY POINT
# ============================
# CRITICAL: multiprocessing 'spawn' re-imports this module as '__main__'
# in the child process so it can locate the target function by name.
# We must NOT run main() in that child — only the true launcher should.
#
# The reliable guard: Python's multiprocessing sets the current process
# name to something other than 'MainProcess' in every spawned worker
# (e.g. 'Process-1').  Checking this is the only approach that works
# across Python versions without private API access.
#
# We also guard with __name__ == '__main__' so the module is safe to
# import from other scripts without side effects.

if __name__ == '__main__':
    import multiprocessing as _mp_entry

    # freeze_support() is required for PyInstaller + spawn.
    # It exits immediately in the spawn-child bootstrap context,
    # so nothing below runs in that case.
    _mp_entry.freeze_support()

    # After freeze_support(), check the process name.
    # Spawned workers are named 'Process-N'; only the true launcher
    # process is named 'MainProcess'.
    _proc_name = _mp_entry.current_process().name

    if _proc_name == 'MainProcess' and not SKIP_GUI_IMPORTS:
        # True application entry — run the Qt GUI
        sys.exit(main())
    elif SKIP_GUI_IMPORTS:
        # Legacy Django manage.py subprocess mode via env vars
        logger.info("Subprocess mode - skipping main() execution")
    else:
        # Spawned multiprocessing worker (e.g. Django process).
        # Module was imported so the worker can find run_django_server.
        # Do nothing — the multiprocessing framework calls the target.
        pass
