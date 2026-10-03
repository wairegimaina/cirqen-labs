# First run on a PC: make Cirqen's own database and apply the migrations.
# Nothing else: user accounts come down from HQ through sync
# (`manage.py create_hod` exists for a PC that has to start without HQ).
#
# The database is embedded (embedded_pg.py): Cirqen makes it in the user's
# folder and runs it itself. Nothing is copied from older setups; a new PC,
# or one moving to this version, fills its database from HQ.
from PySide6.QtCore import QObject, Signal

from .embedded_pg import DatabaseError
from .runtime import *


class FirstRunSetup(QObject):
    """Creates and migrates the local database. Every step can be repeated, so
    a setup that was interrupted simply runs again on the next start. The
    server is left running for ServiceManager, which finds and uses it."""

    progress_update = Signal(str, int)
    setup_complete = Signal(bool, str)
    log_message = Signal(str)

    def __init__(self, port_manager: PortManager):
        super().__init__()
        self.port_manager = port_manager
        self.db = local_database()
        self.pg_logs = DATA_PATH / 'logs'

    def is_first_run(self):
        is_first = not self.db.is_ready()
        logger.info(f"First run check: {is_first}")
        return is_first

    def _step(self, text, percent):
        logger.info(f"[setup] {text}")
        self.progress_update.emit(text, percent)
        self.log_message.emit(text)

    def run_setup(self):
        if not self.is_first_run():
            self.setup_complete.emit(True, "Already configured")
            return
        try:
            if not self.db.exists():
                self._step("Creating your database...", 15)
                self.db.initialize()

            self._step("Starting the database...", 40)
            port = self.db.start(
                self.port_manager.get_port('postgresql_local'),
                on_wait=lambda s: s and s % 10 == 0 and self._step(
                    f"Starting the database (the disk is slow, {s}s)...", 45))
            self.port_manager.ports['postgresql_local'] = port
            db_config = setup_environment(self.port_manager)
            self.db.ensure_database(port)

            self._step("Applying database migrations (this can take a few minutes)...", 60)
            error = self._run_migrations(db_config)
            if error:
                return self._fail(error)

            self.db.mark_ready(self._app_version())
            self._step("Setup complete", 100)
            self.setup_complete.emit(True, (
                "The local database is ready.\n\n"
                "Sign in with the account your hospital gave you; accounts come from HQ."))
        except DatabaseError as e:
            self._fail(str(e))
        except Exception as e:
            import traceback
            logger.error(f"Setup error: {traceback.format_exc()}")
            self._fail(f"{type(e).__name__}: {e}")

    def _fail(self, message):
        logger.error(f"[setup] failed: {message}")
        self.db.stop()
        self.setup_complete.emit(False, f"{message}\n\nLogs: {self.pg_logs}")

    def _log_tail(self, name, chars=1500):
        try:
            return (self.pg_logs / name).read_text(encoding='utf-8', errors='replace')[-chars:]
        except OSError:
            return ''

    @staticmethod
    def _app_version():
        for version_file in (APPLICATION_PATH / '_internal' / 'version.txt', APPLICATION_PATH / 'version.txt'):
            if version_file.is_file():
                return version_file.read_text().strip()
        return ''

    def _run_migrations(self, db_config):
        """None when the local database is migrated, else why not. HQ applies
        its own migrations. Output goes to logs/migrations.log, not pipes:
        anything the child starts would inherit pipes and keep them open."""
        manage_py = APPLICATION_PATH / 'manage.py'
        if not manage_py.exists():
            return f"manage.py not found at {manage_py}"

        env = os.environ.copy()
        env['CIRQEN_SKIP_INSTANCE_LOCK'] = '1'
        env['CIRQEN_MIGRATION_MODE'] = '1'
        for key in ('host', 'port', 'database', 'user', 'password'):
            env[f'POSTGRES_LOCAL_{key.upper()}'] = str(db_config[key])

        log_path = self.pg_logs / 'migrations.log'
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
        try:
            with open(log_path, 'w', encoding='utf-8') as out:
                result = subprocess.run(
                    [sys.executable, str(manage_py), 'migrate', '--noinput'],
                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                    cwd=str(APPLICATION_PATH), env=env, timeout=1200, **kwargs)
        except subprocess.TimeoutExpired:
            return "Migrations did not finish in 20 minutes.\n" + self._log_tail('migrations.log')
        if result.returncode != 0:
            return (f"Database migrations failed (exit {result.returncode}):\n"
                    + self._log_tail('migrations.log'))
        logger.info("[setup] Migrations applied")
        return None
