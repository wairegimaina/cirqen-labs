# First run on a PC: create the local PostgreSQL cluster and database, and
# apply the migrations. Nothing else: user accounts come down from HQ through
# sync (`manage.py create_hod` exists for a PC that has to start without HQ).
import shutil

from PySide6.QtCore import QObject, Signal

from .pg_process import fix_data_dir_mode, pg_env, server_options, start as start_pg_server, tool
from .runtime import *


class FirstRunSetup(QObject):
    """Creates the local database the first time Cirqen starts on a PC.

    The cluster's superuser is the app's own role (config.json local_db.user,
    normally cirqen1) with config.json's password, set by initdb itself. So
    there is no second role to create, no trust stage and no pg_hba.conf
    rewriting, and the OS login name (spaces, capitals, no login terminal)
    never matters.
    """

    progress_update = Signal(str, int)
    setup_complete = Signal(bool, str)
    log_message = Signal(str)

    def __init__(self, port_manager: PortManager):
        super().__init__()
        self.port_manager = port_manager
        self.pg_data = DATA_PATH / 'postgres'
        self.pg_logs = DATA_PATH / 'logs'
        self.pg_dir = RUNTIME_DIR / 'postgresql'
        self.db_config = setup_environment(port_manager)

    def is_first_run(self):
        is_first = not (self.pg_data / 'PG_VERSION').exists()
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
        server = log_handle = None
        try:
            self._step("Checking the installation...", 5)
            if not self.db_config.get('password'):
                return self._fail(f"config.json in {DATA_PATH} has no local database password "
                                  "(local_db.password). Delete config.json and start Cirqen again.")
            missing = [tool(self.pg_dir, n) for n in ('initdb', 'postgres', 'pg_ctl')
                       if not tool(self.pg_dir, n).exists()]
            if missing:
                return self._fail("PostgreSQL is missing from this installation:\n"
                                  + "\n".join(f"  {p}" for p in missing)
                                  + "\nReinstall Cirqen.")

            self._step("Creating the database cluster...", 20)
            error = self._initdb()
            if error:
                return self._fail(error)

            self._step("Starting PostgreSQL...", 45)
            server, log_handle = self._start()
            if not self._wait_ready(server):
                return self._fail("PostgreSQL did not start.\n" + self._log_tail('postgres_setup.log'))

            self._step("Creating the Cirqen database...", 60)
            self._create_database()

            self._step("Applying database migrations (this can take a few minutes)...", 70)
            error = self._run_migrations()
            if error:
                return self._fail(error)

            self._step("Setup complete", 100)
            self.setup_complete.emit(True, (
                "The local database is ready.\n\n"
                "Sign in with the account your hospital gave you; accounts come from HQ.\n\n"
                f"PostgreSQL: {self.db_config['port']}   "
                f"Redis: {self.port_manager.get_port('redis')}   "
                f"Django: {self.port_manager.get_port('django')}"))
        except Exception as e:
            import traceback
            logger.error(f"Setup error: {traceback.format_exc()}")
            self._fail(f"{type(e).__name__}: {e}")
        finally:
            if server is not None:
                self._stop(server)
            if log_handle:
                log_handle.close()

    def _fail(self, message):
        logger.error(f"[setup] failed: {message}")
        self.setup_complete.emit(False, f"{message}\n\nLogs: {DATA_PATH / 'logs'}")

    def _log_tail(self, name, chars=1500):
        try:
            return (self.pg_logs / name).read_text(encoding='utf-8', errors='replace')[-chars:]
        except OSError:
            return ''

    def _initdb(self):
        """None when the cluster was made, else why not. A failed or
        interrupted earlier attempt leaves files without PG_VERSION, and
        initdb refuses a non-empty folder, so those are moved aside first."""
        if self.pg_data.exists() and any(self.pg_data.iterdir()):
            aside = self.pg_data.with_name(f"postgres.unfinished-{time.strftime('%Y%m%d-%H%M%S')}")
            logger.warning(f"[setup] Moving an unfinished database folder aside: {aside}")
            self.pg_data.rename(aside)
        self.pg_data.mkdir(parents=True, exist_ok=True)
        fix_data_dir_mode(self.pg_data)

        pwfile = DATA_PATH / 'temp' / 'initdb.pw'
        pwfile.parent.mkdir(exist_ok=True)
        try:
            pwfile.write_text(self.db_config['password'] + '\n', encoding='utf-8')
            try:
                pwfile.chmod(0o600)
            except OSError:
                pass
            # On Windows initdb drops admin rights itself, like pg_ctl does.
            result = subprocess.run(
                [str(tool(self.pg_dir, 'initdb')), '-D', str(self.pg_data),
                 '-U', self.db_config['user'], f'--pwfile={pwfile}',
                 '--auth=scram-sha-256', '--encoding=UTF8', '--locale=C'],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=300, env=pg_env(self.pg_dir))
        except subprocess.TimeoutExpired:
            return "initdb did not finish in 5 minutes."
        finally:
            pwfile.unlink(missing_ok=True)

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()[-1500:]
            logger.error(f"[setup] initdb failed ({result.returncode}): {detail}")
            shutil.rmtree(self.pg_data, ignore_errors=True)
            self.pg_data.mkdir(exist_ok=True)
            return f"Creating the database failed:\n{detail}"
        logger.info(f"[setup] Cluster created in {self.pg_data} (superuser {self.db_config['user']})")
        return None

    def _start(self):
        (self.pg_logs / 'postgres_setup.log').write_text('')
        return start_pg_server(tool(self.pg_dir, 'postgres'), self.pg_data,
                               server_options(self.db_config['port']),
                               self.pg_logs / 'postgres_setup.log', pg_env(self.pg_dir))

    def _connect(self, database='postgres'):
        import psycopg2
        return psycopg2.connect(host='127.0.0.1', port=self.db_config['port'], dbname=database,
                                user=self.db_config['user'], password=self.db_config['password'],
                                connect_timeout=3)

    def _wait_ready(self, server, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if server.poll() is not None:
                return False
            try:
                self._connect().close()
                return True
            except Exception:
                time.sleep(0.5)
        return False

    def _create_database(self):
        conn = self._connect()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.db_config['database'],))
                if not cur.fetchone():
                    from psycopg2 import sql
                    cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0")
                                .format(sql.Identifier(self.db_config['database'])))
                    logger.info(f"[setup] Created database {self.db_config['database']}")
        finally:
            conn.close()

    def _stop(self, server):
        try:
            server.terminate()
            server.wait(timeout=30)
        except Exception:
            try:
                server.kill()
                server.wait(timeout=10)
            except Exception as e:
                logger.warning(f"[setup] Could not stop PostgreSQL: {e}")

    def _run_migrations(self):
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
            env[f'POSTGRES_LOCAL_{key.upper()}'] = str(self.db_config[key])

        log_path = self.pg_logs / 'migrations.log'
        try:
            with open(log_path, 'w', encoding='utf-8') as out:
                result = subprocess.run(
                    [sys.executable, str(manage_py), 'migrate', '--noinput'],
                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                    cwd=str(APPLICATION_PATH), env=env, timeout=600)
        except subprocess.TimeoutExpired:
            return "Migrations did not finish in 10 minutes.\n" + self._log_tail('migrations.log')
        if result.returncode != 0:
            return (f"Database migrations failed (exit {result.returncode}):\n"
                    + self._log_tail('migrations.log'))
        logger.info("[setup] Migrations applied")
        return None
