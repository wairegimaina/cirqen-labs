# app_update_streaming.py
"""
Application Update Streaming System
Streams application updates (HTML, static files, Django apps) to offline-first desktop apps
Integrates with your existing sync_agent.py and update_client.py
"""

import os
import sys
import json
import time
import hashlib
import zipfile
import shutil
import tempfile
import threading
import queue
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import logging
import requests
from flask import Response, stream_with_context, request, jsonify, send_file

LOG = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

class UpdateConfig:
    """Configuration for update streaming"""

    # Update intervals
    CHECK_INTERVAL_HOURS = float(os.getenv('UPDATE_CHECK_INTERVAL_HOURS', '24'))

    # Update server (HQ)
    HQ_UPDATE_URL = os.getenv('HQ_UPDATE_URL', 'https://hq-server-dgs6.onrender.com/api/updates')

    # Local paths
    APP_DIR = Path(os.getenv('APP_DIR', Path.cwd()))
    DATA_DIR = Path(os.getenv('DATA_DIR', Path.home() / '.cmms'))

    # Update storage
    UPDATE_CACHE_DIR = DATA_DIR / 'update_cache'
    BACKUP_DIR = DATA_DIR / 'backups'

    # Components to update
    UPDATE_COMPONENTS = {
        'templates': True,      # HTML templates
        'static': True,         # CSS, JS, images
        'django_apps': True,    # Python Django apps
        'python_code': True,    # Main Python files
        'migrations': True      # Database migrations
    }

    # Safety settings
    AUTO_APPLY_UPDATES = os.getenv('AUTO_APPLY_UPDATES', 'false').lower() == 'true'
    REQUIRE_CONFIRMATION = not AUTO_APPLY_UPDATES

    # Streaming settings
    CHUNK_SIZE = 8192  # 8KB chunks for streaming
    MAX_DOWNLOAD_SIZE = 500 * 1024 * 1024  # 500MB max


# ============================================================
# UPDATE MANIFEST STRUCTURE
# ============================================================

class UpdateManifest:
    """
    Represents an application update package

    Structure:
    {
        "version": "1.2.0",
        "created_at": "2025-01-01T10:00:00Z",
        "min_version": "1.0.0",
        "changes": "Bug fixes and new features",
        "critical": false,
        "size_bytes": 12345678,
        "checksum": "sha256:abcdef...",
        "components": {
            "templates": {"files": 45, "size": 123456},
            "static": {"files": 120, "size": 5678901},
            "django_apps": {"files": 89, "size": 2345678},
            "python_code": {"files": 12, "size": 345678}
        },
        "files": [
            {
                "path": "templates/base.html",
                "size": 1234,
                "checksum": "abc123..."
            }
        ],
        "migrations": [
            {
                "app": "equipment",
                "migration": "0015_add_status_fields"
            }
        ]
    }
    """

    def __init__(self, data: Dict):
        self.version = data['version']
        self.created_at = data['created_at']
        self.min_version = data.get('min_version', '0.0.0')
        self.changes = data.get('changes', '')
        self.critical = data.get('critical', False)
        self.size_bytes = data.get('size_bytes', 0)
        self.checksum = data.get('checksum', '')
        self.components = data.get('components', {})
        self.files = data.get('files', [])
        self.migrations = data.get('migrations', [])

    @property
    def size_mb(self) -> float:
        """Size in megabytes"""
        return self.size_bytes / (1024 * 1024)

    def to_dict(self) -> Dict:
        return {
            'version': self.version,
            'created_at': self.created_at,
            'min_version': self.min_version,
            'changes': self.changes,
            'critical': self.critical,
            'size_bytes': self.size_bytes,
            'size_mb': round(self.size_mb, 2),
            'checksum': self.checksum,
            'components': self.components,
            'files': self.files,
            'migrations': self.migrations
        }


# ============================================================
# UPDATE STREAM MANAGER
# ============================================================

class UpdateStreamManager:
    """
    Manages update streaming to multiple clients
    Similar to SSE but optimized for large file transfers
    """

    def __init__(self):
        self.active_streams: Dict[str, Dict] = {}
        self.lock = threading.Lock()

        # Update availability cache
        self.available_updates: Dict[str, UpdateManifest] = {}
        self.last_check = None

        LOG.info("✅ Update Stream Manager initialized")

    def register_stream(self, client_id: str, current_version: str) -> Dict:
        """Register client for update stream"""
        with self.lock:
            self.active_streams[client_id] = {
                'current_version': current_version,
                'connected_at': datetime.now(timezone.utc).isoformat(),
                'status': 'connected'
            }

            LOG.info(f"📱 Client {client_id} registered for updates (v{current_version})")
            return self.active_streams[client_id]

    def unregister_stream(self, client_id: str):
        """Remove client from active streams"""
        with self.lock:
            if client_id in self.active_streams:
                del self.active_streams[client_id]
                LOG.info(f"👋 Client {client_id} unregistered from updates")

    def check_available_updates(self, version: str) -> Optional[UpdateManifest]:
        """Check if updates are available for version"""
        # Check cache first
        if version in self.available_updates:
            return self.available_updates[version]

        # Query HQ for updates
        try:
            response = requests.get(
                f"{UpdateConfig.HQ_UPDATE_URL}/check",
                params={'current_version': version},
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()

                if data.get('update_available'):
                    manifest = UpdateManifest(data)
                    self.available_updates[version] = manifest
                    return manifest

        except Exception as e:
            LOG.error(f"❌ Failed to check for updates: {e}")

        return None


# Global manager instance
update_manager = UpdateStreamManager()


# ============================================================
# UPDATE DOWNLOADER WITH PROGRESS STREAMING
# ============================================================

class StreamingUpdateDownloader:
    """
    Downloads updates with real-time progress streaming
    Optimized for large files with resume capability
    """

    def __init__(self, manifest: UpdateManifest, client_id: str):
        self.manifest = manifest
        self.client_id = client_id
        self.progress_queue = queue.Queue()
        self.cancel_event = threading.Event()

    def download_update(self, dest_path: Path) -> bool:
        """
        Download update package with progress tracking

        Progress events sent to queue:
        {
            'event': 'progress',
            'downloaded': 12345,
            'total': 100000,
            'percent': 12.3,
            'speed_mbps': 5.2
        }
        """
        try:
            url = f"{UpdateConfig.HQ_UPDATE_URL}/download/{self.manifest.version}"

            LOG.info(f"📥 Starting download: {self.manifest.version}")
            LOG.info(f"   Size: {self.manifest.size_mb:.2f} MB")
            LOG.info(f"   Destination: {dest_path}")

            # Create temporary file for atomic download
            temp_file = dest_path.with_suffix('.tmp')

            # Stream download with progress
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            start_time = time.time()
            last_progress_time = start_time

            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=UpdateConfig.CHUNK_SIZE):
                    if self.cancel_event.is_set():
                        LOG.info("❌ Download cancelled by user")
                        temp_file.unlink(missing_ok=True)
                        return False

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        # Send progress update (every 100ms or 1MB)
                        current_time = time.time()
                        if (current_time - last_progress_time > 0.1 or
                            downloaded % (1024 * 1024) < UpdateConfig.CHUNK_SIZE):

                            elapsed = current_time - start_time
                            speed_mbps = (downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0

                            progress = {
                                'event': 'download_progress',
                                'downloaded': downloaded,
                                'total': total_size,
                                'percent': (downloaded / total_size * 100) if total_size > 0 else 0,
                                'speed_mbps': round(speed_mbps, 2),
                                'elapsed_seconds': round(elapsed, 1)
                            }

                            self.progress_queue.put(progress)
                            last_progress_time = current_time

            # Verify download
            if total_size > 0 and downloaded != total_size:
                LOG.error(f"❌ Download incomplete: {downloaded}/{total_size}")
                temp_file.unlink(missing_ok=True)
                return False

            # Verify checksum if provided
            if self.manifest.checksum:
                LOG.info("🔍 Verifying checksum...")
                if not self._verify_checksum(temp_file):
                    LOG.error("❌ Checksum verification failed")
                    temp_file.unlink(missing_ok=True)
                    return False

            # Move to final destination (atomic)
            shutil.move(str(temp_file), str(dest_path))

            elapsed = time.time() - start_time
            avg_speed = (downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0

            LOG.info(f"✅ Download complete!")
            LOG.info(f"   Time: {elapsed:.1f}s")
            LOG.info(f"   Avg speed: {avg_speed:.2f} MB/s")

            self.progress_queue.put({
                'event': 'download_complete',
                'path': str(dest_path),
                'size': downloaded,
                'elapsed_seconds': round(elapsed, 1)
            })

            return True

        except Exception as e:
            LOG.error(f"❌ Download failed: {e}")
            self.progress_queue.put({
                'event': 'download_error',
                'error': str(e)
            })
            return False

    def _verify_checksum(self, file_path: Path) -> bool:
        """Verify file checksum"""
        try:
            # Parse checksum format: "sha256:abc123..."
            algo, expected = self.manifest.checksum.split(':', 1)

            hasher = hashlib.new(algo)
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hasher.update(chunk)

            actual = hasher.hexdigest()
            return actual == expected

        except Exception as e:
            LOG.error(f"Checksum verification error: {e}")
            return False

    def cancel(self):
        """Cancel ongoing download"""
        self.cancel_event.set()


# ============================================================
# UPDATE APPLIER WITH BACKUP
# ============================================================

class UpdateApplier:
    """
    Applies updates to the application with backup and rollback support
    """

    def __init__(self, manifest: UpdateManifest, package_path: Path):
        self.manifest = manifest
        self.package_path = package_path
        self.backup_path = None

    def apply_update(self) -> Tuple[bool, str]:
        """
        Apply update with full backup and rollback capability

        Returns:
            (success, message)
        """
        try:
            LOG.info("=" * 80)
            LOG.info(f"🔄 APPLYING UPDATE: {self.manifest.version}")
            LOG.info("=" * 80)

            # Step 1: Create backup
            LOG.info("💾 Step 1/5: Creating backup...")
            self.backup_path = self._create_backup()
            if not self.backup_path:
                return False, "Backup creation failed"
            LOG.info(f"   ✅ Backup created: {self.backup_path}")

            # Step 2: Extract update package
            LOG.info("📦 Step 2/5: Extracting update package...")
            extract_dir = self._extract_package()
            if not extract_dir:
                return False, "Package extraction failed"
            LOG.info(f"   ✅ Extracted to: {extract_dir}")

            # Step 3: Verify files
            LOG.info("🔍 Step 3/5: Verifying files...")
            if not self._verify_extracted_files(extract_dir):
                return False, "File verification failed"
            LOG.info("   ✅ All files verified")

            # Step 4: Copy files to application directory
            LOG.info("📋 Step 4/5: Updating application files...")
            if not self._copy_files(extract_dir):
                LOG.error("   ❌ File copy failed, rolling back...")
                self._rollback()
                return False, "File copy failed, rolled back"
            LOG.info("   ✅ Files updated successfully")

            # Step 5: Run migrations (if any)
            if self.manifest.migrations:
                LOG.info(f"🔄 Step 5/5: Running {len(self.manifest.migrations)} migrations...")
                if not self._run_migrations():
                    LOG.warning("   ⚠️ Migrations failed, but update applied")
            else:
                LOG.info("✓ Step 5/5: No migrations to run")

            # Update version file
            self._update_version_file()

            # Cleanup
            shutil.rmtree(extract_dir, ignore_errors=True)

            LOG.info("=" * 80)
            LOG.info("🎉 UPDATE APPLIED SUCCESSFULLY!")
            LOG.info(f"   New version: {self.manifest.version}")
            LOG.info(f"   Backup saved: {self.backup_path}")
            LOG.info("=" * 80)

            return True, f"Update to {self.manifest.version} applied successfully"

        except Exception as e:
            LOG.error(f"❌ Update failed: {e}")
            if self.backup_path:
                LOG.info("🔄 Attempting rollback...")
                self._rollback()
            return False, str(e)

    def _create_backup(self) -> Optional[Path]:
        """Create backup of current application"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_name = f"backup-v{self.manifest.version}-{timestamp}"
            backup_path = UpdateConfig.BACKUP_DIR / backup_name
            backup_path.mkdir(parents=True, exist_ok=True)

            # Backup directories based on enabled components
            components_to_backup = {
                'templates': UpdateConfig.UPDATE_COMPONENTS.get('templates', True),
                'static': UpdateConfig.UPDATE_COMPONENTS.get('static', True),
                'staticfiles': UpdateConfig.UPDATE_COMPONENTS.get('static', True)
            }

            for component, should_backup in components_to_backup.items():
                if should_backup:
                    src = UpdateConfig.APP_DIR / component
                    if src.exists():
                        dst = backup_path / component
                        shutil.copytree(src, dst)
                        LOG.debug(f"   ✓ Backed up: {component}")

            # Backup Django apps
            if UpdateConfig.UPDATE_COMPONENTS.get('django_apps', True):
                for app_dir in UpdateConfig.APP_DIR.iterdir():
                    if app_dir.is_dir() and (app_dir / 'apps.py').exists():
                        dst = backup_path / app_dir.name
                        shutil.copytree(app_dir, dst)
                        LOG.debug(f"   ✓ Backed up app: {app_dir.name}")

            # Backup main Python files
            if UpdateConfig.UPDATE_COMPONENTS.get('python_code', True):
                for py_file in ['main.py', 'config.py', 'sync_agent.py', 'manage.py']:
                    src = UpdateConfig.APP_DIR / py_file
                    if src.exists():
                        dst = backup_path / py_file
                        shutil.copy2(src, dst)
                        LOG.debug(f"   ✓ Backed up: {py_file}")

            # Save backup metadata
            metadata = {
                'version': self.manifest.version,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'files_backed_up': sum(1 for _ in backup_path.rglob('*') if _.is_file())
            }

            with open(backup_path / 'backup_metadata.json', 'w') as f:
                json.dump(metadata, f, indent=2)

            return backup_path

        except Exception as e:
            LOG.error(f"Backup creation failed: {e}")
            return None

    def _extract_package(self) -> Optional[Path]:
        """Extract update package"""
        try:
            extract_dir = UpdateConfig.UPDATE_CACHE_DIR / f"extract_{self.manifest.version}"
            extract_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(self.package_path, 'r') as zf:
                zf.extractall(extract_dir)

            return extract_dir

        except Exception as e:
            LOG.error(f"Extraction failed: {e}")
            return None

    def _verify_extracted_files(self, extract_dir: Path) -> bool:
        """Verify all files match manifest"""
        try:
            for file_info in self.manifest.files:
                file_path = extract_dir / file_info['path']

                if not file_path.exists():
                    LOG.error(f"Missing file: {file_info['path']}")
                    return False

                # Verify size
                if file_path.stat().st_size != file_info['size']:
                    LOG.error(f"Size mismatch: {file_info['path']}")
                    return False

                # Verify checksum
                with open(file_path, 'rb') as f:
                    hasher = hashlib.sha256()
                    hasher.update(f.read())
                    actual = hasher.hexdigest()

                if actual != file_info['checksum']:
                    LOG.error(f"Checksum mismatch: {file_info['path']}")
                    return False

            return True

        except Exception as e:
            LOG.error(f"Verification failed: {e}")
            return False

    def _copy_files(self, extract_dir: Path) -> bool:
        """Copy files to application directory"""
        try:
            for file_info in self.manifest.files:
                src = extract_dir / file_info['path']
                dst = UpdateConfig.APP_DIR / file_info['path']

                # Create parent directories
                dst.parent.mkdir(parents=True, exist_ok=True)

                # Copy file
                shutil.copy2(src, dst)

            LOG.info(f"   Copied {len(self.manifest.files)} files")
            return True

        except Exception as e:
            LOG.error(f"File copy failed: {e}")
            return False

    def _run_migrations(self) -> bool:
        """Run Django migrations"""
        try:
            import subprocess

            manage_py = UpdateConfig.APP_DIR / 'manage.py'

            if not manage_py.exists():
                LOG.warning("manage.py not found, skipping migrations")
                return True

            env = os.environ.copy()
            env['CIRQEN_SKIP_INSTANCE_LOCK'] = '1'
            env['CIRQEN_MIGRATION_MODE'] = '1'

            result = subprocess.run(
                [sys.executable, str(manage_py), 'migrate', '--noinput'],
                cwd=str(UpdateConfig.APP_DIR),
                env=env,
                capture_output=True,
                text=True,
                timeout=300
            )

            if result.returncode != 0:
                LOG.error(f"Migrations failed: {result.stderr}")
                return False

            LOG.info("   ✅ Migrations completed")
            return True

        except Exception as e:
            LOG.error(f"Migration error: {e}")
            return False

    def _update_version_file(self):
        """Update version file"""
        version_file = UpdateConfig.APP_DIR / 'VERSION'
        version_file.write_text(self.manifest.version)

    def _rollback(self):
        """Rollback to backup"""
        if not self.backup_path or not self.backup_path.exists():
            LOG.error("❌ No backup available for rollback")
            return

        try:
            LOG.info("🔄 Rolling back from backup...")

            # Restore each backed up component
            for item in self.backup_path.iterdir():
                if item.name == 'backup_metadata.json':
                    continue

                src = item
                dst = UpdateConfig.APP_DIR / item.name

                # Remove current version
                if dst.exists():
                    if dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()

                # Restore backup
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

            LOG.info("✅ Rollback complete")

        except Exception as e:
            LOG.error(f"❌ Rollback failed: {e}")


# ============================================================
# INTEGRATION WITH SYNC AGENT
# ============================================================

def add_update_check_to_sync_agent(agent):
    """
    Add update checking to SyncAgent

    Usage in sync_agent.py __init__:
        from app_update_streaming import add_update_check_to_sync_agent
        add_update_check_to_sync_agent(self)
    """

    def update_check_loop():
        """Background thread to check for updates"""
        interval = int(UpdateConfig.CHECK_INTERVAL_HOURS * 3600)

        LOG.info(f"🔄 Update check loop started (every {UpdateConfig.CHECK_INTERVAL_HOURS}h)")

        while not agent.stop_event.is_set():
            try:
                current_version = agent.version

                # Check for updates
                manifest = update_manager.check_available_updates(current_version)

                if manifest:
                    LOG.info("=" * 80)
                    LOG.info("🆕 UPDATE AVAILABLE!")
                    LOG.info("=" * 80)
                    LOG.info(f"   Current: {current_version}")
                    LOG.info(f"   Available: {manifest.version}")
                    LOG.info(f"   Size: {manifest.size_mb:.2f} MB")
                    LOG.info(f"   Changes: {manifest.changes}")
                    LOG.info(f"   Critical: {manifest.critical}")
                    LOG.info("=" * 80)

                    # Auto-apply if enabled or if critical
                    if UpdateConfig.AUTO_APPLY_UPDATES or manifest.critical:
                        LOG.info("🚀 Auto-applying update...")

                        # Download
                        cache_dir = UpdateConfig.UPDATE_CACHE_DIR
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        package_path = cache_dir / f"update-{manifest.version}.zip"

                        downloader = StreamingUpdateDownloader(manifest, agent.client_id)
                        if downloader.download_update(package_path):
                            # Apply
                            applier = UpdateApplier(manifest, package_path)
                            success, message = applier.apply_update()

                            if success:
                                LOG.info("🎉 UPDATE APPLIED! Restart required.")
                                # Notify user to restart
                                agent.state.set('update_pending_restart', 'true')
                                agent.state.set('new_version', manifest.version)
                            else:
                                LOG.error(f"❌ Update failed: {message}")
                    else:
                        LOG.info("📌 Update available - user confirmation required")
                        agent.state.set('update_available', manifest.version)
                        agent.state.set('update_manifest', json.dumps(manifest.to_dict()))

            except Exception as e:
                LOG.error(f"❌ Update check failed: {e}")

            # Wait for next check
            for _ in range(interval):
                if agent.stop_event.is_set():
                    break
                time.sleep(1)

        LOG.info("🔄 Update check loop exiting")

    # Start update check thread
    update_thread = threading.Thread(target=update_check_loop, name="UpdateCheckThread", daemon=True)
    update_thread.start()
    agent.threads.append(update_thread)

    LOG.info("✅ Update checking enabled")


# ============================================================
# FLASK ROUTES FOR UPDATE STREAMING
# ============================================================

def setup_update_routes(app):
    """
    Add update routes to Flask app

    Usage in server_sync_api.py:
        from app_update_streaming import setup_update_routes
        setup_update_routes(app)
    """

    @app.route('/api/updates/check')
    def check_updates():
        """
        Check if updates are available

        Query params:
            - current_version: Client's current version

        Response:
            {
                "update_available": true,
                "version": "1.2.0",
                "size_mb": 25.5,
                "changes": "Bug fixes...",
                "critical": false,
                "components": {...}
            }
        """
        current_version = request.args.get('current_version', '0.0.0')

        manifest = update_manager.check_available_updates(current_version)

        if manifest:
            return jsonify({
                'update_available': True,
                **manifest.to_dict()
            })
        else:
            return jsonify({
                'update_available': False,
                'current_version': current_version,
                'message': 'You are up to date'
            })


    @app.route('/api/updates/download/<version>')
    def download_update(version):
        """
        Download update package

        Streams the update package with progress
        """
        # Find update package
        package_path = UpdateConfig.UPDATE_CACHE_DIR / f"cirqen-update-{version}.zip"

        if not package_path.exists():
            return jsonify({'error': 'Update not found'}), 404

        return send_file(
            package_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'cirqen-update-{version}.zip'
        )


    @app.route('/api/updates/manifest/<version>')
    def get_update_manifest(version):
        """Get update manifest for verification"""
        manifest_path = UpdateConfig.UPDATE_CACHE_DIR / version / 'manifest.json'

        if not manifest_path.exists():
            return jsonify({'error': 'Manifest not found'}), 404

        with open(manifest_path, 'r') as f:
            manifest = json.load(f)

        return jsonify(manifest)


    @app.route('/api/updates/status')
    def update_status():
        """Get update system status"""
        return jsonify({
            'active_clients': len(update_manager.active_streams),
            'available_updates': len(update_manager.available_updates),
            'config': {
                'auto_apply': UpdateConfig.AUTO_APPLY_UPDATES,
                'check_interval_hours': UpdateConfig.CHECK_INTERVAL_HOURS
            }
        })

    LOG.info("✅ Update routes registered")


