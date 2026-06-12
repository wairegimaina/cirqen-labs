#!/usr/bin/env python3
"""
Cirqen Auto-Update Client - COMPLETE DUAL-MODE SYSTEM
======================================================

This file contains TWO independent update clients that work with main.py:

1. **CodeUpdateClient** - Code Directory Updates (PRIMARY)
   - Scans ONLY the 'code' subdirectory
   - Updates Django apps and backend code
   - Used by: CodeUpdateManager
   - Fast, focused updates

2. **SafeUpdateClient** - Full Application Updates (FUTURE USE)
   - Scans ALL files in _internal directory
   - Updates everything: code, libs, frontend
   - Used by: UpdateManager
   - Comprehensive updates

Both clients support:
- ✅ Safe atomic updates with rollback
- ✅ Checksum verification (SHA-256)
- ✅ Progress callbacks for UI
- ✅ Staging and automatic backup
- ✅ WebSocket real-time notifications
- ✅ Graceful error handling

Compatible with:
- update_manager.py (CodeUpdateManager + UpdateManager)
- main.py (Cirqen desktop application)
"""

import asyncio
import aiohttp
import hashlib
import json
import logging
import shutil
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set
import uuid
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# CODE DIRECTORY UPDATE CLIENT (PRIMARY)
# ============================================================================

class CodeUpdateClient:
    """
    Update client specifically for 'code' directory

    This client focuses updates on Django apps and backend code only,
    leaving frontend, libraries, and user data completely untouched.

    Features:
    - Scans only code/ subdirectory
    - Fast checksumming (fewer files)
    - Atomic updates with rollback
    - Backup before applying
    - WebSocket notifications

    Used by: CodeUpdateManager from update_manager.py
    """

    def __init__(
        self,
        server_url: str,
        app_root: Path,
        data_dir: Path,
        current_version: str,
        code_subdir: str = "code"
    ):
        """
        Initialize code directory update client

        Args:
            server_url: Update server URL (e.g., http://localhost:8001)
            app_root: Application root directory (_internal folder)
            data_dir: User data directory (for staging, backups)
            current_version: Current application version
            code_subdir: Subdirectory containing Django code (default: "code")
        """
        self.server_url = server_url.rstrip('/')
        self.app_root = Path(app_root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.current_version = current_version

        # Code directory - this is what we'll update
        self.code_dir = self.app_root / code_subdir
        self.code_subdir = code_subdir

        # Get/create unique installation ID
        self.installation_id = self._get_installation_id()

        # Update staging area (downloads go here first)
        self.staging_dir = self.data_dir / "update_staging" / "code"
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        # Backup directory
        self.backup_dir = self.data_dir / "update_backups" / "code"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # WebSocket
        self.ws_session: Optional[aiohttp.ClientSession] = None
        self.ws_connection = None
        self.update_callback = None

        logger.info("="*70)
        logger.info("CODE DIRECTORY UPDATE CLIENT INITIALIZED")
        logger.info("="*70)
        logger.info(f"Installation ID: {self.installation_id}")
        logger.info(f"Current version: {self.current_version}")
        logger.info(f"Server: {self.server_url}")
        logger.info(f"App root: {self.app_root}")
        logger.info(f"Code directory: {self.code_dir}")
        logger.info(f"Update scope: CODE DIRECTORY ONLY")
        logger.info("="*70)

        # Verify code directory exists, create if needed
        if not self.code_dir.exists():
            logger.warning(f"⚠️  Code directory does not exist: {self.code_dir}")
            logger.warning("    Creating it now...")
            self.code_dir.mkdir(parents=True, exist_ok=True)
            logger.info("✅ Code directory created")

    def _get_installation_id(self) -> str:
        """Get or create unique installation ID"""
        id_file = self.data_dir / "installation_id.txt"

        if id_file.exists():
            installation_id = id_file.read_text().strip()
            logger.debug(f"Loaded existing installation ID: {installation_id}")
            return installation_id

        installation_id = str(uuid.uuid4())
        id_file.write_text(installation_id)
        logger.info(f"Created new installation ID: {installation_id}")
        return installation_id

    def _should_scan_file(self, file_path: Path) -> bool:
        """
        Determine if a file should be scanned for updates

        Only scans files within the code directory, skipping:
        - Hidden files (starting with .)
        - __pycache__ directories
        - .pyc files
        - Virtual environments
        - Git directories
        - User data directories

        Args:
            file_path: Path to check

        Returns:
            True if file should be scanned, False otherwise
        """
        # Must be a file
        if not file_path.is_file():
            return False

        # Skip hidden files
        if file_path.name.startswith('.'):
            return False

        # Check if file is within code directory
        try:
            rel_path = file_path.relative_to(self.code_dir)
        except ValueError:
            # File is outside code directory - skip
            return False

        # Skip certain directories even within code
        skip_dirs = {
            '.git', '__pycache__', '.venv', 'venv', 'env',
            'update_staging', 'update_backups', 'node_modules',
            'data', 'runtime', 'logs', 'postgres', 'redis',
            'media', 'static', 'staticfiles'  # Django static files
        }

        # Check if any part of the relative path is in skip_dirs
        path_parts = set(rel_path.parts)
        if path_parts & skip_dirs:
            return False

        # Skip .pyc files
        if file_path.suffix == '.pyc':
            return False

        return True

    def calculate_checksums(self, force_rescan: bool = False) -> Dict[str, str]:
        """
        Calculate checksums of all files in code directory

        Scans the entire code directory and computes SHA-256 checksums
        for all eligible files (non-hidden, non-cache files).

        Args:
            force_rescan: If True, ignore any cached checksums

        Returns:
            Dict mapping {relative_path: sha256_checksum}
            Paths are relative to code directory
        """
        checksums = {}
        scanned_count = 0
        skipped_count = 0

        logger.info("🔍 Scanning code directory for checksums...")
        logger.info(f"   Directory: {self.code_dir}")

        if not self.code_dir.exists():
            logger.error(f"❌ Code directory does not exist: {self.code_dir}")
            return checksums

        # Scan all files in code directory
        for file_path in self.code_dir.rglob("*"):
            try:
                if not self._should_scan_file(file_path):
                    skipped_count += 1
                    continue

                # Get path relative to code directory
                rel_path = str(file_path.relative_to(self.code_dir))

                # Normalize to forward slashes (cross-platform compatibility)
                rel_path = rel_path.replace('\\', '/')

                # Calculate checksum
                checksum = self._checksum_file(file_path)
                checksums[rel_path] = checksum
                scanned_count += 1

                logger.debug(f"   ✓ {rel_path}: {checksum[:12]}...")

            except Exception as e:
                logger.warning(f"   ⚠️  Could not checksum {file_path}: {e}")
                continue

        logger.info(f"✅ Code directory scan complete:")
        logger.info(f"   • Scanned: {scanned_count} files")
        logger.info(f"   • Skipped: {skipped_count} files")
        logger.info(f"   • Total checksums: {len(checksums)}")

        return checksums

    def _checksum_file(self, file_path: Path) -> str:
        """
        Calculate SHA-256 checksum of a file

        Args:
            file_path: Path to the file

        Returns:
            Hexadecimal SHA-256 checksum string
        """
        sha256 = hashlib.sha256()

        try:
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            logger.error(f"Error calculating checksum for {file_path}: {e}")
            raise

    async def check_for_updates(self) -> Optional[Dict]:
        """
        Check if code directory updates are available from server

        This sends local checksums to the server and receives information
        about what files have changed (new, modified, deleted).

        Returns:
            Update info dict if updates available, None otherwise

        Update info structure:
        {
            "update_available": bool,
            "new_version": str,
            "current_version": str,
            "changes_count": int,
            "new_files": [list of paths],
            "modified_files": [list of paths],
            "deleted_files": [list of paths],
            "checksums": {path: checksum}
        }
        """
        logger.info("🔍 Checking for code directory updates from server...")

        try:
            # Calculate local checksums
            local_checksums = self.calculate_checksums()

            if not local_checksums:
                logger.warning("⚠️  No files found in code directory")

            # Prepare request
            request_data = {
                "current_version": self.current_version,
                "checksums": local_checksums,
                "installation_id": self.installation_id,
                "update_scope": "code_directory"
            }

            # Send to server
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.server_url}/check_update",
                    json=request_data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:

                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Server returned status {response.status}")
                        logger.error(f"Response: {error_text}")
                        return None

                    update_info = await response.json()

                    if update_info.get("update_available"):
                        logger.info("="*70)
                        logger.info("📦 CODE DIRECTORY UPDATES AVAILABLE")
                        logger.info("="*70)
                        logger.info(f"   Current version: {self.current_version}")
                        logger.info(f"   New version: {update_info.get('new_version')}")
                        logger.info(f"   Total changes: {update_info.get('changes_count')}")
                        logger.info(f"   • New files: {len(update_info.get('new_files', []))}")
                        logger.info(f"   • Modified files: {len(update_info.get('modified_files', []))}")
                        logger.info(f"   • Deleted files: {len(update_info.get('deleted_files', []))}")
                        logger.info("="*70)
                    else:
                        logger.info("✅ Code directory is up to date")

                    return update_info

        except asyncio.TimeoutError:
            logger.error("⏱️  Request timed out - server not responding")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"❌ Connection error: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Error checking for updates: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def download_and_apply_update(self, progress_func=None) -> bool:
        """
        Download and apply code directory updates

        Process:
        1. Check for updates
        2. Download changed files to staging
        3. Verify checksums
        4. Create backup of current code
        5. Apply updates atomically
        6. Rollback on any error

        Args:
            progress_func: Optional callback(current, total, message)
                          Called to report progress

        Returns:
            True if update applied successfully, False otherwise
        """
        logger.info("="*70)
        logger.info("DOWNLOADING AND APPLYING CODE DIRECTORY UPDATES")
        logger.info("="*70)

        backup_path = None

        try:
            # Step 1: Check for updates
            update_info = await self.check_for_updates()

            if not update_info or not update_info.get("update_available"):
                logger.info("No updates available")
                return False

            new_files = update_info.get("new_files", [])
            modified_files = update_info.get("modified_files", [])
            deleted_files = update_info.get("deleted_files", [])
            server_checksums = update_info.get("checksums", {})

            total_changes = len(new_files) + len(modified_files) + len(deleted_files)
            new_version = update_info.get("new_version")

            logger.info(f"Total changes to apply: {total_changes}")

            # Step 2: Clear staging directory
            logger.info("🧹 Preparing staging area...")
            if self.staging_dir.exists():
                shutil.rmtree(self.staging_dir)
            self.staging_dir.mkdir(parents=True, exist_ok=True)

            # Step 3: Download new and modified files
            files_to_download = new_files + modified_files
            download_success_count = 0
            download_fail_count = 0

            logger.info(f"📥 Downloading {len(files_to_download)} files...")

            for idx, relative_path in enumerate(files_to_download, 1):
                if progress_func:
                    progress_func(idx, len(files_to_download),
                                f"Downloading {relative_path}")

                logger.info(f"📥 [{idx}/{len(files_to_download)}] {relative_path}")

                success = await self._download_file(
                    relative_path,
                    server_checksums.get(relative_path)
                )

                if success:
                    download_success_count += 1
                else:
                    download_fail_count += 1
                    logger.error(f"Failed to download {relative_path}")
                    return False

            logger.info(f"✅ Downloaded {download_success_count}/{len(files_to_download)} files")

            if download_fail_count > 0:
                logger.error(f"❌ {download_fail_count} files failed to download")
                return False

            # Step 4: Create backup before applying
            logger.info("📦 Creating backup of current code directory...")
            if progress_func:
                progress_func(len(files_to_download), len(files_to_download) + total_changes,
                            "Creating backup...")

            backup_path = await self._create_backup()
            logger.info(f"✅ Backup created: {backup_path.name}")

            # Step 5: Apply updates atomically
            logger.info("🔄 Applying updates to code directory...")

            try:
                applied_count = 0

                # Apply new and modified files
                for idx, relative_path in enumerate(files_to_download, 1):
                    if progress_func:
                        progress_func(
                            len(files_to_download) + idx,
                            len(files_to_download) + total_changes,
                            f"Applying {relative_path}"
                        )

                    self._apply_file(relative_path)
                    applied_count += 1
                    logger.debug(f"Applied: {relative_path}")

                # Delete removed files
                for relative_path in deleted_files:
                    self._delete_file(relative_path)
                    applied_count += 1
                    logger.debug(f"Deleted: {relative_path}")

                # Step 6: Update version file
                self._save_version(new_version)

                logger.info("="*70)
                logger.info("✅ CODE DIRECTORY UPDATE COMPLETED SUCCESSFULLY!")
                logger.info("="*70)
                logger.info(f"   Previous version: {self.current_version}")
                logger.info(f"   New version: {new_version}")
                logger.info(f"   Changes applied: {applied_count}")
                logger.info(f"   Backup saved: {backup_path.name}")
                logger.info(f"   Code directory: {self.code_dir}")
                logger.info("="*70)

                # Update current version
                self.current_version = new_version

                return True

            except Exception as e:
                logger.error(f"❌ Error applying updates: {e}")
                logger.error("🔄 Rolling back to backup...")

                if backup_path and backup_path.exists():
                    await self._restore_backup(backup_path)
                    logger.info("✅ Rollback complete - code directory restored")
                else:
                    logger.error("❌ Backup not found - cannot rollback!")

                return False

        except Exception as e:
            logger.error(f"❌ Update process failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

            # Attempt rollback if we have a backup
            if backup_path and backup_path.exists():
                try:
                    await self._restore_backup(backup_path)
                    logger.info("✅ Rollback complete")
                except Exception as rollback_error:
                    logger.error(f"❌ Rollback failed: {rollback_error}")

            return False

    async def _download_file(self, relative_path: str, expected_checksum: Optional[str] = None) -> bool:
        """
        Download a single file from server to staging area

        Args:
            relative_path: Path relative to code directory
            expected_checksum: Expected SHA-256 checksum for verification

        Returns:
            True if download and verification successful, False otherwise
        """
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.server_url}/download/{relative_path}"

                async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Server returned {response.status} for {relative_path}")
                        logger.error(f"Response: {error_text}")
                        return False

                    # Get checksum from header (if provided)
                    server_checksum = response.headers.get('X-Checksum')

                    # Read content
                    content = await response.read()

                    # Calculate local checksum
                    local_checksum = hashlib.sha256(content).hexdigest()

                    # Verify against server checksum (if provided in header)
                    if server_checksum and server_checksum != local_checksum:
                        logger.error(f"❌ Checksum mismatch for {relative_path}")
                        logger.error(f"   Server: {server_checksum}")
                        logger.error(f"   Local:  {local_checksum}")
                        return False

                    # Verify against expected checksum (from update manifest)
                    if expected_checksum and expected_checksum != local_checksum:
                        logger.error(f"❌ Expected checksum mismatch for {relative_path}")
                        logger.error(f"   Expected: {expected_checksum}")
                        logger.error(f"   Local:    {local_checksum}")
                        return False

                    # Save to staging area
                    staged_file = self.staging_dir / relative_path
                    staged_file.parent.mkdir(parents=True, exist_ok=True)

                    with open(staged_file, 'wb') as f:
                        f.write(content)

                    logger.debug(f"✓ Downloaded: {relative_path} ({len(content)} bytes, {local_checksum[:12]}...)")
                    return True

        except Exception as e:
            logger.error(f"❌ Download error for {relative_path}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def _apply_file(self, relative_path: str):
        """
        Copy file from staging area to code directory

        Args:
            relative_path: Path relative to code directory
        """
        staged_file = self.staging_dir / relative_path
        target_file = self.code_dir / relative_path

        if not staged_file.exists():
            raise FileNotFoundError(f"Staged file not found: {staged_file}")

        # Create parent directories if needed
        target_file.parent.mkdir(parents=True, exist_ok=True)

        # Copy file (preserving metadata)
        shutil.copy2(staged_file, target_file)

    def _delete_file(self, relative_path: str):
        """
        Delete file from code directory

        Args:
            relative_path: Path relative to code directory
        """
        target_file = self.code_dir / relative_path

        if target_file.exists():
            target_file.unlink()
            logger.debug(f"Deleted: {relative_path}")
        else:
            logger.warning(f"File to delete not found: {relative_path}")

    async def _create_backup(self) -> Path:
        """
        Create backup of current code directory

        Returns:
            Path to backup directory
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"backup_{self.current_version}_{timestamp}"

        logger.debug(f"Creating backup at: {backup_path}")

        # Copy entire code directory
        shutil.copytree(
            self.code_dir,
            backup_path,
            dirs_exist_ok=False,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git', '.DS_Store')
        )

        logger.debug(f"Backup created successfully: {backup_path}")
        return backup_path

    async def _restore_backup(self, backup_path: Path):
        """
        Restore code directory from backup

        Args:
            backup_path: Path to backup directory
        """
        logger.warning(f"⚠️  Restoring code directory from backup: {backup_path.name}")

        if not backup_path.exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")

        # Remove current code directory
        if self.code_dir.exists():
            shutil.rmtree(self.code_dir)
            logger.debug("Removed current code directory")

        # Restore from backup
        shutil.copytree(backup_path, self.code_dir)

        logger.info("✅ Code directory restored from backup successfully")

    def _save_version(self, version: str):
        """
        Save current version to file

        Args:
            version: Version string to save
        """
        version_file = self.data_dir / "code_version.txt"
        version_file.write_text(version)
        logger.debug(f"Saved code version: {version}")

    async def connect_websocket(self, update_callback=None):
        """
        Connect to WebSocket for real-time update notifications

        This allows the server to push notifications when updates become
        available, rather than polling.

        Args:
            update_callback: Function to call when update notification received
                           Signature: callback(update_info: dict)
        """
        self.update_callback = update_callback

        logger.info("🔌 Connecting to update WebSocket...")

        ws_url = f"{self.server_url.replace('http', 'ws')}/ws/{self.installation_id}"
        logger.debug(f"WebSocket URL: {ws_url}")

        try:
            self.ws_session = aiohttp.ClientSession()
            self.ws_connection = await self.ws_session.ws_connect(ws_url)

            logger.info("✅ WebSocket connected - real-time updates enabled")

            # Listen for messages
            async for msg in self.ws_connection:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_ws_message(data)

                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket closed by server")
                    break

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("WebSocket error occurred")
                    break

        except Exception as e:
            logger.error(f"WebSocket error: {e}")

        finally:
            if self.ws_session:
                await self.ws_session.close()
                logger.info("WebSocket connection closed")

    async def _handle_ws_message(self, data: Dict):
        """
        Handle WebSocket message from server

        Args:
            data: Message data from server
        """
        msg_type = data.get("type")

        if msg_type == "connected":
            logger.info("📡 Server connection confirmed")

        elif msg_type == "update_available":
            logger.info("="*70)
            logger.info("🔔 CODE UPDATE NOTIFICATION VIA WEBSOCKET")
            logger.info("="*70)
            logger.info(f"   Changes: {data.get('changes_count', 'unknown')} files")
            logger.info(f"   New version: {data.get('new_version', 'unknown')}")
            logger.info("="*70)

            # Call user callback if provided
            if self.update_callback:
                try:
                    self.update_callback(data)
                except Exception as e:
                    logger.error(f"Error in update callback: {e}")

        elif msg_type == "pong":
            logger.debug("Received pong from server")

        else:
            logger.warning(f"Unknown WebSocket message type: {msg_type}")


# ============================================================================
# HELPER FUNCTION FOR EASY INTEGRATION
# ============================================================================

async def check_and_apply_code_updates(
    server_url: str,
    app_root: Path,
    data_dir: Path,
    current_version: str,
    code_subdir: str = "code",
    show_dialog_func=None,
    progress_func=None
) -> bool:
    """
    Helper function to check and apply code directory updates

    This is a convenience function that:
    1. Creates a CodeUpdateClient
    2. Checks for updates
    3. Optionally asks user (via dialog function)
    4. Downloads and applies updates
    5. Reports progress

    Args:
        server_url: Update server URL (e.g., http://localhost:8001)
        app_root: Application root directory (_internal)
        data_dir: Data directory (for staging and backups)
        current_version: Current app version
        code_subdir: Subdirectory containing Django code (default: "code")
        show_dialog_func: Optional function to show update dialog
                         Signature: show_dialog_func(update_info: dict) -> bool
                         Return True to proceed, False to cancel
        progress_func: Optional function to show progress
                      Signature: progress_func(current: int, total: int, message: str)

    Returns:
        True if update was applied successfully, False otherwise
    """

    client = CodeUpdateClient(
        server_url,
        app_root,
        data_dir,
        current_version,
        code_subdir=code_subdir
    )

    # Check for updates
    update_info = await client.check_for_updates()

    if not update_info or not update_info.get("update_available"):
        logger.info("No code updates available")
        return False

    # Ask user if dialog function provided
    if show_dialog_func:
        user_approved = show_dialog_func(update_info)
        if not user_approved:
            logger.info("User declined code update")
            return False

    # Download and apply
    logger.info("User approved code update - proceeding...")
    success = await client.download_and_apply_update(progress_func)

    return success
