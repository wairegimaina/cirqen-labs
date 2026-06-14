# Auto-generated refactor of the original Cirqen main.py first-run database setup layer.
from PySide6.QtCore import QObject, Signal

from .runtime import *

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

