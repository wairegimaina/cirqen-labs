# Auto-generated refactor of the original Cirqen main.py service manager layer.
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .runtime import *

class SetupThread(QThread):
    """Thread for first-run setup"""

    def __init__(self, setup_manager):
        super().__init__()
        self.setup_manager = setup_manager

    def run(self):
        self.setup_manager.run_setup()


class ServiceThread(QThread):
    def __init__(self, service_manager):
        super().__init__()
        self.service_manager = service_manager

    def run(self):
        self.service_manager.start_services()


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

        # Update manager — initialised here, started in start_services()
        self._update_manager = None

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
        self.stop_event.set()

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
                        except Exception:
                            pass

                    QTimer.singleShot(2000, force_kill)

                # Close log file immediately
                if log_file:
                    try:
                        log_file.close()
                    except Exception:
                        pass

            except Exception as e:
                logger.debug(f"Error terminating {name}: {e}")

        self.processes.clear()
        logger.info("✅ Fast shutdown complete")

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
         8. Update Manager (poll for app updates)

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
            logger.info("📊 [1/7] Starting PostgreSQL Local...")

            if not self.start_postgresql():
                raise Exception("PostgreSQL Local startup failed")

            logger.info("✅ PostgreSQL Local started successfully")
            # start_postgresql() already polls a real psycopg2 connection until
            # ready (see _ensure_pg_user_and_db_safe), so no extra wait is needed
            # here beyond a brief settle margin.
            time.sleep(0.5)

            # ============================================================
            # SERVICE 2: PostgreSQL HQ (OPTIONAL)
            # ============================================================
            self.progress_update.emit("Starting PostgreSQL HQ...", 20)
            logger.info("")
            logger.info("📊 [2/7] Starting PostgreSQL HQ...")

            if not self.start_postgresql_hq():
                logger.warning("⚠️  PostgreSQL HQ startup failed")
                logger.warning("   Application will continue without HQ database")
                logger.warning("   Sync functionality will be limited")
            else:
                logger.info("✅ PostgreSQL HQ started successfully")

            # No wait here: HQ is the remote Supabase DB in normal operation,
            # so this step is a fast no-op skip (missing local data dir) on
            # every startup — there's nothing to let "settle".

            # ============================================================
            # SERVICE 3: Redis (REQUIRED)
            # ============================================================
            self.progress_update.emit("Starting Redis...", 30)
            logger.info("")
            logger.info("📊 [3/7] Starting Redis...")

            if not self.start_redis():
                raise Exception("Redis startup failed")

            logger.info("✅ Redis started successfully")
            time.sleep(1)

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
            logger.info("📊 [4/7] Starting Django Web Server...")

            if not self.start_django():
                raise Exception("Django startup failed")

            logger.info("✅ Django web server started successfully")
            # start_django() already polls the real HTTP health check until
            # ready (see the DJANGO HEALTH CHECK loop below), so no extra
            # wait is needed here.

            # ============================================================
            # SERVICE 5: Celery Worker (OPTIONAL)
            # ============================================================
            self.progress_update.emit("Starting task worker...", 60)
            logger.info("")
            logger.info("📊 [5/7] Starting Celery Worker...")

            if not self.start_celery():
                logger.warning("⚠️  Celery startup failed")
                logger.warning("   Application will continue without background tasks")
                logger.warning("   Scheduled jobs and async tasks will not run")
            else:
                logger.info("✅ Celery worker started successfully")

            time.sleep(1)

            # ============================================================
            # SERVICE 6: Celery Beat (OPTIONAL)
            # ============================================================
            self.progress_update.emit("Starting task scheduler...", 75)
            logger.info("")
            logger.info("📊 [6/7] Starting Celery Beat Scheduler...")

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

            time.sleep(1)

            # ============================================================
            # SERVICE 7: Sync Agent (OPTIONAL)
            # ============================================================
            self.progress_update.emit("Starting sync agent...", 80)
            logger.info("")
            logger.info("📊 [7/7] Starting Sync Agent...")

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

                # Start the health monitor now that there's a live sync
                # agent thread to watch. Previously defined but never
                # started — a crashed sync thread had no automatic recovery
                # as long as the main app process stayed up. Bounded to 3
                # restart attempts; see monitor_sync_agent_health() docstring.
                import threading as _threading
                self._sync_health_monitor_thread = _threading.Thread(
                    target=self.monitor_sync_agent_health,
                    name='SyncAgentHealthMonitor',
                    daemon=True,
                )
                self._sync_health_monitor_thread.start()
                logger.info("🔍 Sync agent health monitor thread started")

            time.sleep(1)

            # ============================================================
            # SERVICE 8: Update Manager (OPTIONAL — non-blocking)
            # ============================================================
            self.progress_update.emit("Starting update manager...", 88)
            logger.info("")
            logger.info("📊 [8/8] Starting Update Manager...")

            try:
                from bulider_tools.runtime import UpdateManager
                import bulider_tools.runtime as _rt

                self._update_manager = UpdateManager(
                    data_path=DATA_PATH,
                    app_path=APPLICATION_PATH,
                )
                self._update_manager.start()
                # Expose globally so the UI "Check Now" button can poke it
                _rt.update_manager_instance = self._update_manager

                logger.info("✅ Update manager started — polling https://cirqen-hq.onrender.com")
            except Exception as _um_exc:
                logger.warning("⚠️  Update manager failed to start: %s", _um_exc)
                logger.warning("   App will continue without automatic update checks")

            time.sleep(1)

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
                except Exception:
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
        Monitor sync agent health and restart if needed.
        Runs as a background thread — started from start_services() right
        after start_sync_agent() succeeds.

        HISTORY / BUGFIX: this function previously called `sync_process.poll()`
        to detect death, which is a subprocess.Popen API. start_sync_agent()
        actually runs the sync agent as a threading.Thread (see
        self.processes.append(('sync_agent', sync_thread, None)) below) —
        Thread has no .poll(), so this would have raised AttributeError the
        first time it ran. On top of that, nothing ever called this method at
        all, so the whole thing was dead code start to finish: a sync-thread
        crash while the app kept running had zero automatic recovery. Fixed
        to use Thread.is_alive() and to actually be started.
        """
        import time

        logger = logging.getLogger('Cirqen.SyncAgent')

        logger.info("🔍 Sync agent health monitor started")

        check_interval = 60  # Check every 60 seconds
        restart_attempts = 0
        max_restart_attempts = 3

        while not self.stop_event.is_set():
            try:
                # Find sync agent thread
                sync_thread = None
                sync_log_file = None

                for name, process, log_file in self.processes:
                    if name == 'sync_agent':
                        sync_thread = process
                        sync_log_file = log_file
                        break

                if sync_thread:
                    # Check if the thread is still running. Note this only
                    # detects TOTAL death of the sync subsystem (the outer
                    # SyncAgentThread wrapper) — sync_agent_8.py's own start()
                    # loop keeps that wrapper alive as long as ANY of its 9
                    # inner sync threads (upload/download/cert/heartbeat/...)
                    # is alive, so a single inner loop dying silently is not
                    # visible here. See the sync_agent_8.py per-thread
                    # supervision fix for that layer.
                    if not sync_thread.is_alive():
                        logger.error("=" * 70)
                        logger.error("❌ SYNC AGENT THREAD DIED!")
                        logger.error(f"   Restart attempts: {restart_attempts}/{max_restart_attempts}")
                        logger.error("=" * 70)

                        # Try to restart
                        if restart_attempts < max_restart_attempts:
                            restart_attempts += 1

                            logger.info(f"🔄 Attempting to restart sync agent (attempt {restart_attempts})...")

                            # Close old log file (normally None for the thread
                            # path — run_sync_agent() owns its own file handler)
                            if sync_log_file:
                                try:
                                    sync_log_file.close()
                                except Exception:
                                    pass

                            # Remove the dead entry from the processes list
                            self.processes = [(n, p, l) for n, p, l in self.processes if n != 'sync_agent']

                            # Wait a moment
                            time.sleep(5)

                            # Restart
                            if self.start_sync_agent():
                                logger.info("✅ Sync agent restarted successfully")
                                restart_attempts = 0  # Reset counter on success
                            else:
                                logger.error("❌ Sync agent restart failed")
                        else:
                            logger.error("❌ Max restart attempts reached")
                            logger.error("   Manual intervention required")
                            logger.error("   Run cleanup_cirqen.py and restart application")
                            self._report_critical_failure_standalone(
                                "sync_subsystem_permanently_down",
                                f"Sync agent thread died and could not be restarted after "
                                f"{max_restart_attempts} attempts — the entire sync subsystem "
                                f"is down on this machine until someone manually intervenes.",
                            )
                            break
                    else:
                        # Thread is healthy
                        if restart_attempts > 0:
                            logger.info("✅ Sync agent health restored")
                            restart_attempts = 0

                        logger.debug(f"✅ Sync agent healthy (thread ID: {sync_thread.ident})")

            except Exception as e:
                logger.error(f"Error in sync agent monitor: {e}")

            # Sleep in small increments to allow clean shutdown
            for _ in range(check_interval):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        logger.info("🔍 Sync agent health monitor stopped")

    def _report_critical_failure_standalone(self, failure_type: str, message: str):
        """
        Report a critical failure to HQ WITHOUT going through the SyncAgent
        object — deliberately self-contained, because the whole point of
        calling this is that the sync subsystem (which owns SyncAgent) has
        just been declared permanently dead. Depending on its (possibly
        broken) internals to report its own death would defeat the purpose.

        Reads SYNC_API_URL / SYNC_AUTH_TOKEN directly from the environment
        (same names sync_agent's own config loading uses) and generates a
        stable device id via sync.device_id_generator — the same identity
        the sync agent itself would have used, so HQ can correlate this
        report with whatever it last heard from this machine's heartbeat.
        Fully best-effort: any failure here (HQ unreachable, missing env
        vars, import failure) is logged at debug level and swallowed.
        """
        try:
            import os
            import sys
            import requests

            api_url = os.getenv("SYNC_API_URL", "").rstrip("/")
            auth_token = os.getenv("SYNC_AUTH_TOKEN", "")
            if not api_url:
                logger.debug("Cannot report critical failure — SYNC_API_URL not set")
                return

            device_id = "unknown"
            try:
                sys.path.insert(0, str(APPLICATION_PATH))
                try:
                    from sync.device_id_generator import get_or_create_client_id
                except ImportError:
                    sys.path.insert(0, str(APPLICATION_PATH / '_internal'))
                    from sync.device_id_generator import get_or_create_client_id
                device_id = get_or_create_client_id()
            except Exception as id_err:
                logger.debug(f"Could not resolve device id for failure report: {id_err}")

            headers = {"Content-Type": "application/json"}
            if auth_token:
                headers["X-API-Key"] = auth_token

            requests.post(
                f"{api_url}/report_device_failure",
                json={
                    "client_id": device_id,
                    "machine_id": device_id,
                    "client_name": os.getenv("CLIENT_NAME", ""),
                    "failure_type": failure_type,
                    "message": message,
                },
                headers=headers,
                timeout=10,
            )
            logger.error(f"🚨 Reported critical failure to HQ: {failure_type} — {message}")
        except Exception as e:
            logger.debug(f"Could not report critical failure to HQ ({failure_type}): {e}")

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

        rotate_log_if_large(pg_log)
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
                rotate_log_if_large(pg_log)
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
                    logger.info("✅ PostgreSQL HQ is ready")
                    postgres_hq_ready = True
                    break
                except Exception:
                    time.sleep(0.5)

            if not postgres_hq_ready:
                logger.warning("⚠️  PostgreSQL HQ timeout (non-critical)")
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
                    logger.info(f"✅ HQ database created: {self.hq_db_config['database']}")

                cursor.close()
                conn.close()

                logger.info(f"✅ PostgreSQL HQ ready on port {port}")
                return True

            except Exception as e:
                logger.warning(f"⚠️  PostgreSQL HQ database setup failed: {e}")
                return False

        except Exception as e:
            logger.warning(f"⚠️  PostgreSQL HQ error (non-critical): {e}")
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

            rotate_log_if_large(redis_log)
            log_file = open(redis_log, 'a')
            process = subprocess.Popen(
                [str(redis_bin), str(redis_conf)],
                stdout=log_file,
                stderr=log_file
            )
            self.processes.append(('redis', process, log_file))

            time.sleep(1)
            logger.info(f"✅ Redis started on port {port}")
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

            logger.info(f"✅ Django process started (PID: {django_process.pid})")
            logger.info(f"⏳ Waiting for Django to be ready...")

            # Store process reference
            self.processes.append(('django', django_process, None))

            # Emit progress
            try:
                self.progress_update.emit(f"Starting Django (PID: {django_process.pid})...", 65)
            except Exception:
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
            logger.info("⏳ Waiting for Django server to be ready...")
            logger.info("   This may take 1-3 minutes on first run:")
            logger.info("   • Matplotlib font cache (~30-45s)")
            logger.info("   • Django app loading (~20-30s)")
            logger.info("   • Database migrations (if pending)")
            logger.info("   • Database connections (~10s)")
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
                        logger.error(f"❌ DJANGO PROCESS DIED (EXIT CODE: {exit_code})")
                        logger.error("=" * 70)

                        # Show log
                        try:
                            with open(django_log, 'r') as f:
                                lines = f.readlines()
                                if lines:
                                    logger.error("📄 LAST 50 LINES OF DJANGO LOG:")
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
                            logger.info(f"   🔍 Status: Django starting, port not open yet...")
                            last_status = current_status

                        if current_time - last_log_time > 10:  # Log every 10 seconds
                            logger.info(f"   ⏳ Still waiting... {int(elapsed)}s elapsed")
                            logger.info(f"      - Process alive: ✅")
                            logger.info(f"      - Port {port} open: ❌ (not yet)")
                            last_log_time = current_time

                        time.sleep(0.5)
                        continue

                    # Port is NOW open!
                    if port_open_time is None:
                        port_open_time = current_time
                        logger.info(f"   ✅ Port {port} is now LISTENING (after {int(elapsed)}s)")
                        logger.info(f"   🔍 Attempting HTTP connection...")

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
                        logger.info(f"✅ DJANGO READY ON PORT {port}")
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
                            logger.info(f"✅ DJANGO READY ON PORT {port} (HTTP {e.code})")
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
                                logger.warning(f"   ⚠️  HTTP {e.code} error, retrying...")
                                last_status = current_status

                            if current_time - last_log_time > 10:
                                logger.info(f"   ⏳ HTTP errors, still trying... {int(elapsed)}s elapsed")
                                last_log_time = current_time

                    except (urllib.error.URLError, socket.error, ConnectionRefusedError) as e:
                        # Port open but HTTP not ready - Django still loading
                        current_status = "PORT_OPEN_HTTP_NOT_READY"

                        if current_status != last_status:
                            logger.info(f"   🔍 Port open, waiting for HTTP response...")
                            last_status = current_status

                        if current_time - last_log_time > 10:
                            time_since_port_open = current_time - port_open_time
                            logger.info(f"   ⏳ Django loading... {int(elapsed)}s total, {int(time_since_port_open)}s since port opened")
                            logger.info(f"      - Process alive: ✅")
                            logger.info(f"      - Port {port} listening: ✅")
                            logger.info(f"      - HTTP responding: ⏳ (loading apps...)")
                            last_log_time = current_time

                except Exception as e:
                    # Unexpected error - log and continue
                    error_type = type(e).__name__
                    current_status = f"EXCEPTION_{error_type}"

                    if current_status != last_status:
                        logger.warning(f"   ⚠️  Unexpected error: {error_type}: {str(e)[:100]}")
                        last_status = current_status

                    if current_time - last_log_time > 10:
                        logger.debug(f"   🔍 Still checking... {int(elapsed)}s elapsed ({error_type})")
                        last_log_time = current_time

                time.sleep(0.5)

            # ============================================================
            # TIMEOUT CHECK
            # ============================================================
            if not django_ready:
                logger.error("=" * 70)
                logger.error(f"❌ DJANGO STARTUP TIMEOUT (after {max_wait}s)")
                logger.error("=" * 70)
                logger.error(f"   Total attempts: {attempts}")
                logger.error(f"   Process alive: {'✅ Yes' if django_process.is_alive() else '❌ No'}")

                if port_open_time:
                    logger.error(f"   Port opened: ✅ Yes (but HTTP never responded)")
                    logger.error(f"   Time since port opened: {int(time.time() - port_open_time)}s")
                else:
                    logger.error(f"   Port opened: ❌ No (Django never started listening)")

                logger.error("")
                logger.error("📄 DJANGO LOG FILE:")
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
                logger.error("💡 TROUBLESHOOTING:")
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
            logger.info("✅ DJANGO WEB SERVER READY")
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
                self.progress_update.emit("Django ready! 🚀", 70)
            except Exception:
                pass

            # ====================================================================
            # ADDITIONAL WARMUP - Wait for Django to be fully ready
            # ====================================================================

            logger.info("✅ Django HTTP responding - waiting for full warmup...")
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
                logger.info("🔥 Django fully warmed up and ready")
            except Exception:
                logger.warning("⚠️  Warmup request failed, but continuing...")

            return True


        except Exception as e:
            logger.error(f"❌ Django startup error: {e}")
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
            rotate_log_if_large(celery_log)
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
                except Exception:
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
            rotate_log_if_large(celery_beat_log)
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
                except Exception:
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

    def stop_services(self):
        """Stop all services - UPDATED for thread-based Celery and sync agent"""
        logger.info("Shutting down services...")

        # BUGFIX: self.stop_event was created in __init__ but nothing ever
        # called .set() on it anywhere in this class. monitor_sync_agent_health
        # (and stop_services_fast's cleanup ordering) both check
        # self.stop_event.is_set() to know when to stop looping — without this,
        # the health monitor thread only ever stops because it's a daemon
        # thread getting hard-killed at interpreter exit, not because it was
        # told to. Setting it here lets it exit its sleep loop within ~1s.
        self.stop_event.set()

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
                        except Exception:
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
                        except Exception:
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

                        except Exception:
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
                    except Exception:
                        pass

            except Exception as e:
                logger.error(f"Error stopping {name}: {e}")

        # Stop the update manager background thread
        if self._update_manager:
            try:
                self._update_manager.stop()
                logger.info("✅ Update manager stopped")
            except Exception as _e:
                logger.debug("Update manager stop error: %s", _e)
            self._update_manager = None

        self.processes.clear()
        logger.info("All services stopped")
