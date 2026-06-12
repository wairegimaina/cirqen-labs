#!/usr/bin/env python3
"""
Cirqen Auto-Update Client - IMPROVED VERSION
✅ Better file scanning and change detection
✅ Safe automatic updates with rollback
✅ Enhanced checksum verification
✅ Atomic updates (all-or-nothing)
✅ WebSocket for instant notifications
✅ Better error handling and logging
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

# Configure logging with more detail
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# ENHANCED UPDATE CLIENT WITH IMPROVED SCANNING
# ============================================================================

class SafeUpdateClient:
    """
    Safe update client with improved:
    - File scanning and change detection
    - Atomic updates (all-or-nothing)
    - Checksum verification
    - Automatic rollback on failure
    - WebSocket notifications
    """

    def __init__(
        self,
        server_url: str,
        app_root: Path,
        data_dir: Path,
        current_version: str,
        scan_extensions: Optional[Set[str]] = None
    ):
        self.server_url = server_url.rstrip('/')
        self.app_root = Path(app_root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.current_version = current_version

        # File extensions to scan (None = scan all files)
        self.scan_extensions = scan_extensions

        # Get/create installation ID
        self.installation_id = self._get_installation_id()

        # Update staging area (downloads go here first)
        self.staging_dir = self.data_dir / "update_staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        # Backup directory
        self.backup_dir = self.data_dir / "update_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # WebSocket
        self.ws_session: Optional[aiohttp.ClientSession] = None
        self.ws_connection = None
        self.update_callback = None

        logger.info("="*70)
        logger.info("SAFE UPDATE CLIENT INITIALIZED")
        logger.info("="*70)
        logger.info(f"Installation ID: {self.installation_id}")
        logger.info(f"Current version: {self.current_version}")
        logger.info(f"Server: {self.server_url}")
        logger.info(f"App root: {self.app_root}")
        logger.info(f"Data dir: {self.data_dir}")
        logger.info(f"Scan extensions: {self.scan_extensions or 'ALL'}")
        logger.info("="*70)

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

        Returns:
            True if file should be scanned, False otherwise
        """
        # Check if it's a file
        if not file_path.is_file():
            return False

        # Skip hidden files
        if file_path.name.startswith('.'):
            return False

        # Check extension filter if specified
        if self.scan_extensions:
            if file_path.suffix.lower() not in self.scan_extensions:
                return False

        # Skip directories that should be ignored
        # NOTE: Removed 'dist', 'build', 'data', 'runtime' as these may be part of _internal
        skip_dirs = {'.git', '__pycache__', '.venv', 'venv',
                     'update_staging', 'update_backups', 'node_modules'}

        try:
            # Get path relative to app_root
            rel_path = file_path.relative_to(self.app_root)

            # Check if any part of the relative path is in skip_dirs
            path_parts = set(rel_path.parts)
            if path_parts & skip_dirs:
                return False

            # Skip user data directories only if they're at the top level
            # This prevents overwriting user data while allowing system files
            if len(rel_path.parts) >= 1:
                first_part = rel_path.parts[0]
                # These are user data directories that shouldn't be overwritten
                user_data_dirs = {'data', 'runtime', 'logs', 'postgres',
                                 'update_staging', 'update_backups', 'app_files',
                                 'Archives', 'audit_log', 'media', 'offline_cache',
                                 'qrcodes'}
                if first_part in user_data_dirs:
                    logger.debug(f"Skipping user data: {rel_path}")
                    return False
        except ValueError:
            # File is not relative to app_root
            return False

        return True

    def calculate_checksums(self, force_rescan: bool = False) -> Dict[str, str]:
        """
        Calculate checksums of all application files

        Args:
            force_rescan: If True, ignore cached checksums and rescan all files

        Returns:
            {relative_path: checksum}
        """
        checksums = {}
        scanned_count = 0
        skipped_count = 0

        logger.info("🔍 Scanning application files...")
        logger.info(f"   Scanning directory: {self.app_root}")

        if not self.app_root.exists():
            logger.error(f"❌ App root directory does not exist: {self.app_root}")
            return checksums

        # Scan all files
        for file_path in self.app_root.rglob("*"):
            try:
                if not self._should_scan_file(file_path):
                    skipped_count += 1
                    continue

                rel_path = str(file_path.relative_to(self.app_root))
                checksum = self._checksum_file(file_path)
                checksums[rel_path] = checksum
                scanned_count += 1

                logger.debug(f"   ✓ {rel_path}: {checksum[:12]}...")

            except Exception as e:
                logger.warning(f"   ⚠️  Could not checksum {file_path}: {e}")
                continue

        logger.info(f"✅ File scan complete:")
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
            Hexadecimal checksum string
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
        Check if updates are available from the server

        Returns:
            Update info dict if available, None otherwise
        """
        logger.info("🔍 Checking for updates from server...")

        try:
            # Calculate local checksums
            checksums = self.calculate_checksums()

            if not checksums:
                logger.warning("⚠️  No files found to check - this may be an issue")

            # Prepare request
            request_data = {
                "installation_id": self.installation_id,
                "current_version": self.current_version,
                "platform": sys.platform,
                "file_checksums": checksums
            }

            logger.debug(f"Sending request to: {self.server_url}/check_updates")
            logger.debug(f"Request data: installation_id={self.installation_id}, "
                        f"version={self.current_version}, files={len(checksums)}")

            # Make request to server
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.server_url}/check_updates",
                    json=request_data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:

                    logger.debug(f"Server response status: {response.status}")

                    if response.status == 200:
                        data = await response.json()
                        logger.debug(f"Server response: {json.dumps(data, indent=2)}")

                        if data.get("update_available"):
                            logger.info("="*70)
                            logger.info("✅ UPDATE AVAILABLE!")
                            logger.info("="*70)
                            logger.info(f"   New version: {data.get('new_version')}")
                            logger.info(f"   Current version: {self.current_version}")
                            logger.info(f"   Changes: {data.get('changes_count')} files")
                            logger.info(f"   Download size: {data.get('download_size', 0) / 1024:.1f} KB")
                            logger.info("="*70)
                            return data
                        else:
                            logger.info("✅ Already up to date - no changes detected")
                            return None
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Server error: HTTP {response.status}")
                        logger.error(f"   Response: {error_text}")
                        return None

        except aiohttp.ClientConnectorError as e:
            logger.error(f"❌ Cannot connect to update server: {e}")
            logger.error(f"   Server URL: {self.server_url}")
            logger.error(f"   Make sure the server is running and accessible")
            return None
        except asyncio.TimeoutError:
            logger.error(f"❌ Request timed out - server may be slow or unreachable")
            return None
        except Exception as e:
            logger.error(f"❌ Update check failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def _get_manifest(self) -> Optional[Dict]:
        """
        Get detailed update manifest from server

        Returns:
            Manifest dict with list of changes, or None on failure
        """
        logger.info("📋 Requesting update manifest from server...")

        try:
            # Calculate local checksums
            checksums = self.calculate_checksums()

            # Prepare request
            request_data = {
                "installation_id": self.installation_id,
                "current_version": self.current_version,
                "platform": sys.platform,
                "file_checksums": checksums
            }

            logger.debug(f"Requesting manifest from: {self.server_url}/get_manifest")

            # Make request
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.server_url}/get_manifest",
                    json=request_data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:

                    if response.status == 200:
                        manifest = await response.json()

                        changes = manifest.get("changes", [])
                        logger.info(f"✅ Received manifest with {len(changes)} changes")

                        # Log details about changes
                        if changes:
                            added = sum(1 for c in changes if c.get("change_type") == "added")
                            modified = sum(1 for c in changes if c.get("change_type") == "modified")
                            deleted = sum(1 for c in changes if c.get("change_type") == "deleted")

                            logger.info(f"   Change breakdown:")
                            logger.info(f"   • Added: {added}")
                            logger.info(f"   • Modified: {modified}")
                            logger.info(f"   • Deleted: {deleted}")

                        return manifest
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to get manifest: HTTP {response.status}")
                        logger.error(f"   Response: {error_text}")
                        return None

        except Exception as e:
            logger.error(f"❌ Failed to get manifest: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def install_offline_update(self, bundle_path: Path, progress_callback=None) -> bool:
        """
        Install an offline update from a local ZIP bundle

        Args:
            bundle_path: Path to the update ZIP file
            progress_callback: Optional callback(current, total, message)

        Returns:
            True if successful, False otherwise
        """
        logger.info("="*70)
        logger.info("STARTING OFFLINE UPDATE PROCESS")
        logger.info("="*70)
        logger.info(f"Bundle: {bundle_path}")

        try:
            if not bundle_path.exists():
                logger.error(f"❌ Update bundle not found: {bundle_path}")
                return False

            import zipfile

            # ================================================================
            # STEP 1: Extract bundle to staging
            # ================================================================
            if progress_callback:
                progress_callback(10, 100, "Extracting update bundle...")

            logger.info("📦 Extracting bundle to staging area...")

            # Clean staging first
            if self.staging_dir.exists():
                shutil.rmtree(self.staging_dir)
            self.staging_dir.mkdir(parents=True, exist_ok=True)

            try:
                with zipfile.ZipFile(bundle_path, 'r') as zip_ref:
                    zip_ref.extractall(self.staging_dir)
            except zipfile.BadZipFile:
                logger.error("❌ Invalid update bundle (not a valid ZIP file)")
                return False
            except Exception as e:
                logger.error(f"❌ Failed to extract bundle: {e}")
                return False

            # ================================================================
            # STEP 2: Read and validate manifest
            # ================================================================
            manifest_file = self.staging_dir / "manifest.json"
            if not manifest_file.exists():
                logger.error("❌ Invalid bundle: manifest.json missing")
                return False

            try:
                manifest = json.loads(manifest_file.read_text())
            except json.JSONDecodeError:
                logger.error("❌ Invalid bundle: manifest.json is corrupted")
                return False

            # Check compatibility
            new_version = manifest.get("new_version")
            if not new_version:
                logger.error("❌ Invalid manifest: Version info missing")
                return False

            # Move files from 'files' subdir to root of staging if needed
            # (Offline bundles usually have a 'files' folder)
            files_dir = self.staging_dir / "files"
            if files_dir.exists() and files_dir.is_dir():
                logger.info("   Found 'files' subdirectory, adjusting paths...")
                # We need to move content of 'files' to staging root, but preserving structure
                # Easier way: adjust self.staging_dir to point to 'files' for the apply process
                # But _apply_changes_from_staging expects staging_dir to be the root.
                # Let's move everything up.

                # Create a temporary dir to hold the files
                temp_files = self.data_dir / "temp_offline_files"
                if temp_files.exists():
                    shutil.rmtree(temp_files)

                shutil.move(str(files_dir), str(temp_files))

                # Move everything back to staging root
                for item in temp_files.iterdir():
                    shutil.move(str(item), str(self.staging_dir))

                shutil.rmtree(temp_files)
                # Remove the empty 'files' dir if it still exists (it shouldn't)

            # Now we can reuse the common logic
            return await self._apply_changes_from_staging(manifest, progress_callback)

        except Exception as e:
            logger.error(f"❌ Offline update failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def download_and_apply_update(self, progress_callback=None) -> bool:
        """
        Download and apply updates SAFELY with improved error handling

        Process:
        1. Get update manifest
        2. Download all files to staging
        3. Verify all checksums
        4. Create backup
        5. Apply changes atomically
        6. Rollback on any failure

        Args:
            progress_callback: Optional callback(current, total, message)

        Returns:
            True if successful, False otherwise
        """
        logger.info("="*70)
        logger.info("STARTING SAFE UPDATE PROCESS")
        logger.info("="*70)

        try:
            # ================================================================
            # STEP 1: Get update manifest
            # ================================================================
            if progress_callback:
                progress_callback(0, 100, "Getting update information...")

            manifest = await self._get_manifest()
            if not manifest:
                logger.error("❌ Failed to get update manifest")
                return False

            changes = manifest.get("changes", [])
            total_changes = len(changes)
            new_version = manifest.get("new_version", self.current_version)

            logger.info(f"📦 Update package:")
            logger.info(f"   • New version: {new_version}")
            logger.info(f"   • Current version: {self.current_version}")
            logger.info(f"   • Total changes: {total_changes} files")

            if total_changes == 0:
                logger.info("✅ No changes to apply")
                return True

            # ================================================================
            # STEP 2: Download all files to staging
            # ================================================================
            if progress_callback:
                progress_callback(10, 100, "Downloading files...")

            logger.info("⬇️  Downloading files to staging area...")

            # Clean staging
            if self.staging_dir.exists():
                shutil.rmtree(self.staging_dir)
            self.staging_dir.mkdir(parents=True, exist_ok=True)

            downloaded = []
            failed = []

            for i, change in enumerate(changes):
                path = change["path"]
                change_type = change["change_type"]

                progress = 10 + int((i / total_changes) * 40)
                if progress_callback:
                    progress_callback(progress, 100, f"Downloading {path}...")

                try:
                    if change_type in ["added", "modified"]:
                        # Download new/modified files
                        expected_checksum = change.get("new_checksum") or change.get("checksum")
                        success = await self._download_file(path, expected_checksum)

                        if success:
                            downloaded.append(path)
                            logger.info(f"   ✓ {path}")
                        else:
                            failed.append(path)
                            logger.error(f"   ✗ {path}")

                    elif change_type == "deleted":
                        # Mark for deletion
                        self._mark_for_deletion(path)
                        downloaded.append(path)
                        logger.info(f"   🗑️  Marked for deletion: {path}")

                except Exception as e:
                    logger.error(f"❌ Failed to process {path}: {e}")
                    failed.append(path)

            logger.info(f"📊 Download summary:")
            logger.info(f"   • Success: {len(downloaded)}/{total_changes}")
            logger.info(f"   • Failed: {len(failed)}/{total_changes}")

            if failed:
                logger.error(f"❌ Failed to download files:")
                for f in failed:
                    logger.error(f"   • {f}")
                return False

            # Continue with common application logic
            return await self._apply_changes_from_staging(manifest, progress_callback)

        except Exception as e:
            logger.error("="*70)
            logger.error("❌ UDPATE DOWNLOAD FAILED")
            logger.error("="*70)
            logger.error(f"Error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def _apply_changes_from_staging(self, manifest: Dict, progress_callback=None) -> bool:
        """
        Apply changes from staging directory to application

        This is shared between online and offline updates.
        Assumes staging directory is already populated with files.
        """
        backup_path = None
        changes = manifest.get("changes", [])
        total_changes = len(changes)
        requires_restart = manifest.get("requires_restart", False)
        new_version = manifest.get("new_version")

        try:
            # ================================================================
            # STEP 3: Verify all checksums (Staging Verification)
            # ================================================================
            if progress_callback:
                progress_callback(50, 100, "Verifying staged files...")

            logger.info("🔍 Verifying staged files...")

            verification_failed = []

            for change in changes:
                if change["change_type"] == "deleted":
                    continue

                path = change["path"]
                expected_checksum = change.get("new_checksum") or change.get("checksum")

                if not expected_checksum:
                    logger.warning(f"⚠️  No checksum provided for {path}, skipping verification")
                    continue

                staged_file = self.staging_dir / path
                if not staged_file.exists():
                    logger.error(f"❌ Staged file missing: {path}")
                    verification_failed.append(path)
                    continue

                try:
                    actual_checksum = self._checksum_file(staged_file)

                    if actual_checksum != expected_checksum:
                        logger.error(f"❌ Checksum mismatch: {path}")
                        logger.error(f"   Expected: {expected_checksum}")
                        logger.error(f"   Actual:   {actual_checksum}")
                        verification_failed.append(path)
                    else:
                        logger.debug(f"   ✓ Verified: {path}")

                except Exception as e:
                    logger.error(f"❌ Failed to verify {path}: {e}")
                    verification_failed.append(path)

            if verification_failed:
                logger.error(f"❌ Verification failed for {len(verification_failed)} files:")
                for f in verification_failed:
                    logger.error(f"   • {f}")
                return False

            logger.info("✅ All checksums verified successfully")

            # ================================================================
            # STEP 4: Create backup
            # ================================================================
            if progress_callback:
                progress_callback(60, 100, "Creating backup...")

            logger.info("💾 Creating backup of current state...")
            backup_path = await self._create_backup()
            logger.info(f"✅ Backup created: {backup_path.name}")

            # ================================================================
            # STEP 5: Apply changes ATOMICALLY
            # ================================================================
            if progress_callback:
                progress_callback(70, 100, "Applying updates...")

            logger.info("📦 Applying updates atomically...")

            applied_count = 0
            deleted_count = 0
            apply_errors = []

            for i, change in enumerate(changes):
                path = change["path"]
                change_type = change["change_type"]

                progress = 70 + int((i / total_changes) * 25)
                if progress_callback:
                    progress_callback(progress, 100, f"Applying {path}...")

                try:
                    if change_type in ["added", "modified"]:
                        self._apply_file(path)
                        applied_count += 1
                        logger.debug(f"   ✓ Applied: {path}")

                    elif change_type == "deleted":
                        self._delete_file(path)
                        deleted_count += 1
                        logger.debug(f"   🗑️  Deleted: {path}")

                except Exception as e:
                    logger.error(f"❌ Failed to apply {path}: {e}")
                    apply_errors.append((path, str(e)))

            if apply_errors:
                logger.error(f"❌ {len(apply_errors)} files failed to apply:")
                for path, error in apply_errors:
                    logger.error(f"   • {path}: {error}")

                # Rollback!
                logger.warning("⚠️  Rolling back changes...")
                await self._restore_backup(backup_path)
                return False

            logger.info(f"✅ Applied changes:")
            logger.info(f"   • Added/Modified: {applied_count}")
            logger.info(f"   • Deleted: {deleted_count}")

            # ================================================================
            # STEP 6: Final verification
            # ================================================================
            if progress_callback:
                progress_callback(95, 100, "Final verification...")

            logger.info("🔍 Performing final verification...")

            # Verify applied files exist and have correct checksums
            verification_errors = []
            for change in changes:
                if change["change_type"] == "deleted":
                    continue

                path = change["path"]
                expected_checksum = change.get("new_checksum") or change.get("checksum")
                target_file = self.app_root / path

                if not target_file.exists():
                    logger.error(f"❌ File missing after update: {path}")
                    verification_errors.append(path)
                    continue

                if expected_checksum:
                    try:
                        actual_checksum = self._checksum_file(target_file)
                        if actual_checksum != expected_checksum:
                            logger.error(f"❌ Checksum mismatch after update: {path}")
                            verification_errors.append(path)
                    except Exception as e:
                        logger.error(f"❌ Failed to verify {path}: {e}")
                        verification_errors.append(path)

            if verification_errors:
                logger.error(f"❌ Final verification failed for {len(verification_errors)} files")
                await self._restore_backup(backup_path)
                return False

            logger.info("✅ Final verification passed")

            # ================================================================
            # STEP 7: Clean up and finalize
            # ================================================================
            if progress_callback:
                progress_callback(98, 100, "Cleaning up...")

            logger.info("🧹 Cleaning up staging area...")
            try:
                shutil.rmtree(self.staging_dir)
                self.staging_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"⚠️  Could not clean staging directory: {e}")

            # Update version
            self._save_version(new_version)
            self.current_version = new_version

            if progress_callback:
                progress_callback(100, 100, "Update complete!")

            # ================================================================
            # SUCCESS!
            # ================================================================
            logger.info("="*70)
            logger.info("✅ UPDATE COMPLETED SUCCESSFULLY!")
            logger.info("="*70)
            logger.info(f"   Old version: {manifest.get('old_version', 'unknown')}")
            logger.info(f"   New version: {new_version}")
            logger.info(f"   Files applied: {applied_count}")
            logger.info(f"   Files deleted: {deleted_count}")

            if requires_restart:
                logger.info("   ⚠️  APPLICATION RESTART REQUIRED")
            else:
                logger.info("   ℹ️  No restart needed")

            logger.info("="*70)

            return True

        except Exception as e:
            logger.error("="*70)
            logger.error("❌ APPLY FAILED WITH EXCEPTION")
            logger.error("="*70)
            logger.error(f"Error: {str(e)}")

            import traceback
            logger.error(traceback.format_exc())

            # Attempt rollback
            if backup_path and backup_path.exists():
                try:
                    logger.warning("⚠️  Attempting to restore from backup...")
                    await self._restore_backup(backup_path)
                    logger.info("✅ Backup restored successfully")
                except Exception as rollback_error:
                    logger.error(f"❌ Rollback also failed: {rollback_error}")
                    logger.error("   Manual intervention may be required!")

            logger.error("="*70)
            return False

    async def _download_file(self, relative_path: str, expected_checksum: str = None) -> bool:
        """
        Download a single file from server and verify

        Args:
            relative_path: Path relative to app root
            expected_checksum: Expected SHA-256 checksum

        Returns:
            True if successful, False otherwise
        """
        try:
            download_url = f"{self.server_url}/download/{relative_path}"
            logger.debug(f"Downloading from: {download_url}")

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    download_url,
                    params={"installation_id": self.installation_id},
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:

                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"❌ Download failed: HTTP {response.status}")
                        logger.error(f"   URL: {download_url}")
                        logger.error(f"   Response: {error_text}")
                        return False

                    # Save to staging
                    staged_file = self.staging_dir / relative_path
                    staged_file.parent.mkdir(parents=True, exist_ok=True)

                    content = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        content.extend(chunk)

                    staged_file.write_bytes(content)

                    # Verify checksum
                    server_checksum = response.headers.get('X-Checksum')
                    local_checksum = hashlib.sha256(content).hexdigest()

                    if server_checksum and server_checksum != local_checksum:
                        logger.error(f"❌ Server checksum mismatch: {relative_path}")
                        logger.error(f"   Server: {server_checksum}")
                        logger.error(f"   Local:  {local_checksum}")
                        return False

                    if expected_checksum and expected_checksum != local_checksum:
                        logger.error(f"❌ Expected checksum mismatch: {relative_path}")
                        logger.error(f"   Expected: {expected_checksum}")
                        logger.error(f"   Local:    {local_checksum}")
                        return False

                    logger.debug(f"✓ Downloaded: {relative_path} ({len(content)} bytes, checksum: {local_checksum[:12]}...)")
                    return True

        except Exception as e:
            logger.error(f"❌ Download error for {relative_path}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def _mark_for_deletion(self, relative_path: str):
        """Mark file for deletion"""
        marker = self.staging_dir / f"{relative_path}.DELETE"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("DELETE")
        logger.debug(f"Marked for deletion: {relative_path}")

    def _apply_file(self, relative_path: str):
        """Copy file from staging to app directory"""
        staged_file = self.staging_dir / relative_path
        target_file = self.app_root / relative_path

        if not staged_file.exists():
            raise FileNotFoundError(f"Staged file not found: {staged_file}")

        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_file, target_file)

    def _delete_file(self, relative_path: str):
        """Delete file from app directory"""
        target_file = self.app_root / relative_path

        if target_file.exists():
            target_file.unlink()
            logger.debug(f"Deleted: {relative_path}")
        else:
            logger.warning(f"File to delete not found: {relative_path}")

    async def _create_backup(self) -> Path:
        """Create backup of current app state"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"backup_{self.current_version}_{timestamp}"

        logger.debug(f"Creating backup at: {backup_path}")

        # Copy entire app directory
        shutil.copytree(
            self.app_root,
            backup_path,
            dirs_exist_ok=False,
            ignore=shutil.ignore_patterns('data', 'runtime', '__pycache__', '*.pyc',
                                         'update_staging', 'update_backups')
        )

        return backup_path

    async def _restore_backup(self, backup_path: Path):
        """Restore from backup"""
        logger.warning(f"⚠️  Restoring from backup: {backup_path.name}")

        if not backup_path.exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")

        # Remove current (failed) state
        for item in self.app_root.iterdir():
            if item.name in {'data', 'runtime', 'update_staging', 'update_backups'}:
                continue

            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except Exception as e:
                logger.warning(f"Could not remove {item}: {e}")

        # Restore backup
        for item in backup_path.iterdir():
            try:
                dest = self.app_root / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
            except Exception as e:
                logger.error(f"Failed to restore {item}: {e}")
                raise

        logger.info("✅ Backup restored successfully")

    def _save_version(self, version: str):
        """Save current version to file"""
        version_file = self.data_dir / "app_version.txt"
        version_file.write_text(version)
        logger.debug(f"Saved version: {version}")

    async def connect_websocket(self, update_callback=None):
        """
        Connect to WebSocket for real-time update notifications

        Args:
            update_callback: Called when update notification received
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
        """Handle WebSocket message from server"""
        msg_type = data.get("type")

        if msg_type == "connected":
            logger.info("📡 Server connection confirmed")

        elif msg_type == "update_available":
            logger.info("="*70)
            logger.info("🔔 UPDATE NOTIFICATION RECEIVED VIA WEBSOCKET")
            logger.info("="*70)
            logger.info(f"   Changes: {data.get('changes_count', 'unknown')} files")
            logger.info(f"   New version: {data.get('new_version', 'unknown')}")

            # Call callback if provided
            if self.update_callback:
                try:
                    self.update_callback(data)
                except Exception as e:
                    logger.error(f"Error in update callback: {e}")

        elif msg_type == "pong":
            logger.debug("Received pong from server")

        else:
            logger.warning(f"Unknown message type: {msg_type}")

# ============================================================================
# HELPER FUNCTION FOR INTEGRATION
# ============================================================================

async def check_and_apply_updates(
    server_url: str,
    app_root: Path,
    data_dir: Path,
    current_version: str,
    show_dialog_func=None,
    progress_func=None,
    scan_extensions: Optional[Set[str]] = None
) -> bool:
    """
    Helper function to check and apply updates

    Args:
        server_url: Update server URL
        app_root: Application root directory
        data_dir: Data directory
        current_version: Current app version
        show_dialog_func: Function to show update dialog (returns bool)
        progress_func: Function to show progress (current, total, message)
        scan_extensions: Set of file extensions to scan (e.g. {'.py', '.js'})

    Returns:
        True if update was applied, False otherwise
    """

    client = SafeUpdateClient(
        server_url,
        app_root,
        data_dir,
        current_version,
        scan_extensions=scan_extensions
    )

    # Check for updates
    update_info = await client.check_for_updates()

    if not update_info or not update_info.get("update_available"):
        return False

    # Ask user if dialog function provided
    if show_dialog_func:
        if not show_dialog_func(update_info):
            logger.info("User declined update")
            return False

    # Download and apply
    success = await client.download_and_apply_update(progress_func)

    return success
