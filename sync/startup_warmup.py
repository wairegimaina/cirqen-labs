"""
Startup Warmup Module for Cirqen Application

This module handles:
1. Clearing stale states from previous runs
2. Warming up the application progressively
3. Validating system resources
4. Preparing the environment for clean startup
"""

import os
import time
import json
import socket
import logging
import psutil
from pathlib import Path
from datetime import datetime
import shutil


class StartupStateManager:
    """
    Manages application startup state, clearing stale data and warming up
    """

    def __init__(self, data_path: Path, logger=None):
        self.data_path = Path(data_path)
        self.logger = logger or logging.getLogger('StartupManager')

        # Define paths
        self.state_file = self.data_path / 'startup_state.json'
        self.lock_dir = self.data_path / 'locks'
        self.temp_dir = self.data_path / 'temp'
        self.cache_dir = self.data_path / 'cache'
        self.logs_dir = self.data_path / 'logs'
        self.backups_dir = self.data_path / 'backups'

        # Statistics
        self.cleared_items = []
        self.warnings = []

    def clear_stale_states(self):
        """
        Clear all stale states from previous runs
        Returns: Number of items cleared
        """
        self.logger.info("="*70)
        self.logger.info("🧹 CLEARING STALE STATES")
        self.logger.info("="*70)

        self.cleared_items = []

        # 1. Clear old lock files (except instance.lock)
        self._clear_stale_locks()

        # 2. Clear temporary files
        self._clear_temp_files()

        # 3. Clear old cache entries
        self._clear_old_cache()

        # 4. Clear orphaned PID files
        self._clear_orphaned_pids()

        # 5. Clear stale session data
        self._clear_stale_sessions()

        # 6. Clear old log files (keep last 7 days)
        self._cleanup_old_logs()

        # 7. Verify and fix directory permissions
        self._verify_permissions()

        # Summary
        self.logger.info("="*70)
        if self.cleared_items:
            self.logger.info(f"✅ Cleared {len(self.cleared_items)} stale state items:")
            for item in self.cleared_items:
                self.logger.info(f"   • {item}")
        else:
            self.logger.info("✅ No stale states found - system clean")

        if self.warnings:
            self.logger.warning(f"⚠️  {len(self.warnings)} warnings during cleanup:")
            for warning in self.warnings:
                self.logger.warning(f"   • {warning}")

        self.logger.info("="*70)

        return len(self.cleared_items)

    def _clear_stale_locks(self):
        """Clear stale lock files"""
        if not self.lock_dir.exists():
            return

        for lock_file in self.lock_dir.glob('*.lock'):
            try:
                # Skip instance.lock - it's managed separately
                if lock_file.name == 'instance.lock':
                    continue

                # Check if lock has associated PID
                lock_valid = False
                try:
                    lock_data = json.loads(lock_file.read_text())
                    pid = lock_data.get('pid')
                    if pid and psutil.pid_exists(pid):
                        lock_valid = True
                except:
                    pass

                if not lock_valid:
                    lock_file.unlink()
                    self.cleared_items.append(f"Stale lock: {lock_file.name}")
                    self.logger.info(f"  🗑️  Removed stale lock: {lock_file.name}")

            except Exception as e:
                self.warnings.append(f"Could not remove lock {lock_file.name}: {e}")

    def _clear_temp_files(self):
        """Clear all temporary files"""
        if not self.temp_dir.exists():
            return

        temp_count = 0
        for temp_item in self.temp_dir.glob('*'):
            try:
                if temp_item.is_file():
                    temp_item.unlink()
                    temp_count += 1
                elif temp_item.is_dir():
                    shutil.rmtree(temp_item)
                    temp_count += 1
            except Exception as e:
                self.warnings.append(f"Could not remove temp item {temp_item.name}: {e}")

        if temp_count > 0:
            self.cleared_items.append(f"{temp_count} temporary files/folders")
            self.logger.info(f"  🗑️  Cleared {temp_count} temporary items")

    def _clear_old_cache(self, max_age_hours=24):
        """Clear cache entries older than max_age_hours"""
        if not self.cache_dir.exists():
            return

        cleared = 0
        cutoff_time = time.time() - (max_age_hours * 3600)

        for cache_file in self.cache_dir.glob('**/*'):
            if cache_file.is_file():
                try:
                    if cache_file.stat().st_mtime < cutoff_time:
                        cache_file.unlink()
                        cleared += 1
                except Exception as e:
                    self.warnings.append(f"Could not remove cache file {cache_file.name}: {e}")

        if cleared > 0:
            self.cleared_items.append(f"{cleared} stale cache entries (>{max_age_hours}h old)")
            self.logger.info(f"  🗑️  Cleared {cleared} stale cache entries")

    def _clear_orphaned_pids(self):
        """Clear orphaned PID files"""
        pid_files = list(self.data_path.glob('*.pid'))

        for pid_file in pid_files:
            try:
                pid = int(pid_file.read_text().strip())
                if not psutil.pid_exists(pid):
                    pid_file.unlink()
                    self.cleared_items.append(f"Orphaned PID file: {pid_file.name} (PID: {pid})")
                    self.logger.info(f"  🗑️  Removed orphaned PID file: {pid_file.name}")
            except Exception as e:
                self.warnings.append(f"Error checking PID file {pid_file.name}: {e}")

    def _clear_stale_sessions(self):
        """Clear stale session data"""
        session_file = self.data_path / 'port_session.json'

        if session_file.exists():
            try:
                session_data = json.loads(session_file.read_text())
                pid = session_data.get('pid')

                if pid and not psutil.pid_exists(pid):
                    session_file.unlink()
                    self.cleared_items.append(f"Stale session (PID {pid} not found)")
                    self.logger.info(f"  🗑️  Removed stale session data (PID {pid})")
            except Exception as e:
                self.warnings.append(f"Error checking session data: {e}")

    def _cleanup_old_logs(self, keep_days=7):
        """Remove log files older than keep_days"""
        if not self.logs_dir.exists():
            return

        cutoff_time = time.time() - (keep_days * 24 * 3600)
        removed = 0

        for log_file in self.logs_dir.glob('*.log'):
            try:
                if log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
                    removed += 1
            except Exception as e:
                self.warnings.append(f"Could not remove old log {log_file.name}: {e}")

        if removed > 0:
            self.cleared_items.append(f"{removed} old log files (>{keep_days} days)")
            self.logger.info(f"  🗑️  Removed {removed} old log files")

    def _verify_permissions(self):
        """Verify and fix directory permissions"""
        required_dirs = [
            self.data_path,
            self.lock_dir,
            self.temp_dir,
            self.cache_dir,
            self.logs_dir,
            self.backups_dir,
        ]

        for dir_path in required_dirs:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                # Try to write a test file
                test_file = dir_path / '.write_test'
                test_file.write_text('test')
                test_file.unlink()
            except Exception as e:
                self.warnings.append(f"Permission issue with {dir_path.name}: {e}")

    def warmup_application(self, progress_callback=None):
        """
        Warm up the application by pre-initializing components
        progress_callback: Optional callback function(step_name, progress_percent)
        """
        self.logger.info("="*70)
        self.logger.info("🔥 WARMING UP APPLICATION")
        self.logger.info("="*70)

        warmup_steps = [
            ("Validating data directories", self._validate_directories),
            ("Checking system resources", self._check_resources),
            ("Verifying network connectivity", self._check_network),
            ("Loading configuration", self._load_config),
            ("Preparing cache system", self._prepare_cache),
            ("Checking database files", self._prepare_database),
            ("Initializing security", self._init_security),
            ("Validating dependencies", self._check_dependencies),
        ]

        total_steps = len(warmup_steps)

        for idx, (step_name, step_func) in enumerate(warmup_steps, 1):
            progress = int((idx / total_steps) * 100)
            self.logger.info(f"[{idx}/{total_steps}] {step_name}...")

            if progress_callback:
                try:
                    progress_callback(step_name, progress)
                except Exception as e:
                    self.logger.warning(f"Progress callback error: {e}")

            try:
                step_func()
                self.logger.info(f"  ✅ {step_name} - Complete")
            except Exception as e:
                self.logger.warning(f"  ⚠️  {step_name} - Warning: {e}")

            # Brief pause for visual feedback
            time.sleep(0.15)

        self.logger.info("="*70)
        self.logger.info("✅ WARMUP COMPLETE - Application ready")
        self.logger.info("="*70)

    def _validate_directories(self):
        """Ensure all required directories exist with proper structure"""
        required_dirs = [
            self.data_path,
            self.lock_dir,
            self.temp_dir,
            self.cache_dir,
            self.logs_dir,
            self.backups_dir,
            self.data_path / 'cirqen_local',
            self.data_path / 'cirqen_hq',
        ]

        for dir_path in required_dirs:
            dir_path.mkdir(parents=True, exist_ok=True)

        self.logger.debug(f"  📁 Validated {len(required_dirs)} directories")

    def _check_resources(self):
        """Check system resources and log warnings if needed"""
        # Check memory
        memory = psutil.virtual_memory()
        self.logger.debug(f"  💾 Memory: {memory.percent}% used ({memory.available / (1024**3):.1f} GB available)")

        if memory.percent > 90:
            self.logger.warning(f"  ⚠️  High memory usage: {memory.percent}%")

        # Check disk space
        disk = psutil.disk_usage(str(self.data_path))
        self.logger.debug(f"  💿 Disk: {disk.percent}% used ({disk.free / (1024**3):.1f} GB free)")

        if disk.percent > 90:
            self.logger.warning(f"  ⚠️  Low disk space: {disk.percent}% used")

        # Check CPU
        cpu_percent = psutil.cpu_percent(interval=0.1)
        self.logger.debug(f"  🖥️  CPU: {cpu_percent}% usage")

    def _check_network(self):
        """Verify network connectivity"""
        try:
            # Try to connect to Google DNS
            socket.create_connection(("8.8.8.8", 53), timeout=2)
            self.logger.debug("  🌐 Network connectivity: OK")
        except OSError:
            self.logger.warning("  ⚠️  Network connectivity check failed")

    def _load_config(self):
        """Load and validate configuration files"""
        config_file = self.data_path / 'config.json'

        if config_file.exists():
            try:
                config = json.loads(config_file.read_text())
                self.logger.debug(f"  ⚙️  Configuration loaded: {len(config)} settings")
            except json.JSONDecodeError as e:
                self.logger.warning(f"  ⚠️  Config file invalid: {e}")
        else:
            self.logger.debug("  ⚙️  No config file found (will use defaults)")

    def _prepare_cache(self):
        """Prepare cache system"""
        if self.cache_dir.exists():
            cache_size = sum(f.stat().st_size for f in self.cache_dir.glob('**/*') if f.is_file())
            self.logger.debug(f"  🗄️  Cache: {cache_size / 1024:.1f} KB")

    def _prepare_database(self):
        """Check database files and log status"""
        db_local = self.data_path / 'cirqen_local' / 'db.sqlite3'
        db_hq = self.data_path / 'cirqen_hq' / 'db.sqlite3'

        for db_file in [db_local, db_hq]:
            if db_file.exists():
                size_kb = db_file.stat().st_size / 1024
                self.logger.debug(f"  📊 Database found: {db_file.name} ({size_kb:.1f} KB)")

    def _init_security(self):
        """Initialize security checks"""
        # Verify file permissions
        sensitive_files = [
            self.data_path / 'config.json',
            self.data_path / '.env',
        ]

        for file_path in sensitive_files:
            if file_path.exists():
                # On Unix systems, check if file is readable by others/group and
                # tighten it — these files hold DB/API credentials in plaintext.
                if hasattr(os, 'stat') and hasattr(os, 'chmod'):
                    mode = file_path.stat().st_mode
                    if mode & 0o077:  # group- or world-accessible
                        try:
                            file_path.chmod(0o600)
                            self.logger.info(
                                f"  🔒 Tightened permissions on {file_path.name} (was group/world-readable)"
                            )
                        except OSError as e:
                            self.logger.warning(f"  ⚠️  {file_path.name} is group/world-readable "
                                                 f"and could not be fixed: {e}")

    def _check_dependencies(self):
        """Verify critical dependencies are available"""
        critical_modules = [
            'django',
            'psycopg2',
            'redis',
            'PySide6',
        ]

        missing = []
        for module in critical_modules:
            try:
                __import__(module)
            except ImportError:
                missing.append(module)

        if missing:
            self.logger.warning(f"  ⚠️  Missing modules: {', '.join(missing)}")
        else:
            self.logger.debug(f"  📦 All critical dependencies present")

    def save_startup_state(self):
        """Save current startup state for next run"""
        state = {
            'last_startup': datetime.now().isoformat(),
            'pid': os.getpid(),
            'platform': os.sys.platform,
            'python_version': os.sys.version.split()[0],
            'cleared_items': len(self.cleared_items),
            'warnings': len(self.warnings),
        }

        try:
            self.state_file.write_text(json.dumps(state, indent=2))
            self.logger.info(f"💾 Startup state saved")
        except Exception as e:
            self.logger.warning(f"Could not save startup state: {e}")

    def get_last_startup_info(self):
        """Get information about last startup"""
        if not self.state_file.exists():
            return None

        try:
            return json.loads(self.state_file.read_text())
        except Exception:
            return None


# Convenience function for quick integration
def perform_startup_sequence(data_path, logger=None, progress_callback=None):
    """
    Perform complete startup sequence

    Args:
        data_path: Path to application data directory
        logger: Optional logger instance
        progress_callback: Optional callback for progress updates

    Returns:
        StartupStateManager instance
    """
    manager = StartupStateManager(data_path, logger)

    # Clear stale states
    cleared_count = manager.clear_stale_states()

    # Warmup application
    manager.warmup_application(progress_callback)

    # Save startup state
    manager.save_startup_state()

    return manager
