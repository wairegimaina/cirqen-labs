#!/usr/bin/env python3
import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable
from datetime import datetime, timedelta
import sys

logger = logging.getLogger(__name__)


class UpdateManager:
    """
    Manages application updates with graceful fallback when server unavailable
    """

    def __init__(
        self,
        server_url: str,
        app_root: Path,
        data_dir: Path,
        current_version: str,
        check_interval_minutes: int = 60,
        max_startup_wait_seconds: int = 5,
        enable_background_checking: bool = True
    ):
        """
        Initialize update manager

        Args:
            server_url: Update server URL (e.g., http://localhost:8001)
            app_root: Application root directory (_internal folder)
            data_dir: User data directory
            current_version: Current application version
            check_interval_minutes: How often to check for updates (background)
            max_startup_wait_seconds: Max time to wait for server during startup
            enable_background_checking: Enable periodic background checks
        """
        self.server_url = server_url
        self.app_root = Path(app_root)
        self.data_dir = Path(data_dir)
        self.current_version = current_version
        self.check_interval = timedelta(minutes=check_interval_minutes)
        self.max_startup_wait = max_startup_wait_seconds
        self.enable_background = enable_background_checking

        self.last_check_time: Optional[datetime] = None
        self.last_check_successful = False
        self.server_available = False
        self.background_task: Optional[asyncio.Task] = None

        # Callbacks
        self.on_update_available: Optional[Callable] = None
        self.on_server_reconnect: Optional[Callable] = None

    async def check_at_startup(self) -> Optional[dict]:
        """
        Check for updates at application startup

        This is non-blocking and will timeout if server is unavailable

        Returns:
            Update info if available, None otherwise
        """
        logger.info("="*70)
        logger.info("STARTUP UPDATE CHECK")
        logger.info("="*70)
        logger.info(f"Server: {self.server_url}")
        logger.info(f"Timeout: {self.max_startup_wait}s")
        logger.info(f"Version: {self.current_version}")

        try:
            # Try to check with timeout
            update_info = await asyncio.wait_for(
                self._perform_update_check(),
                timeout=self.max_startup_wait
            )

            if update_info:
                self.server_available = True
                self.last_check_successful = True
                self.last_check_time = datetime.now()

                if update_info.get("update_available"):
                    logger.info("="*70)
                    logger.info("📦 UPDATE AVAILABLE")
                    logger.info("="*70)
                    logger.info(f"Current version: {self.current_version}")
                    logger.info(f"New version: {update_info.get('new_version')}")
                    logger.info(f"Changes: {update_info.get('changes_count')} files")
                    logger.info("="*70)

                    # Trigger callback if set
                    if self.on_update_available:
                        try:
                            self.on_update_available(update_info)
                        except Exception as e:
                            logger.error(f"Error in update callback: {e}")
                else:
                    logger.info("✅ Application is up to date")

                return update_info
            else:
                self.server_available = False
                logger.info("⚠️  No update info received (server may be offline)")
                return None

        except asyncio.TimeoutError:
            self.server_available = False
            logger.warning("="*70)
            logger.warning("⚠️  UPDATE SERVER NOT RESPONDING")
            logger.warning("="*70)
            logger.warning(f"Server: {self.server_url}")
            logger.warning(f"Timeout: {self.max_startup_wait}s exceeded")
            logger.warning("Application will continue without update check")
            logger.warning("Updates will be checked in background")
            logger.warning("="*70)
            return None

        except Exception as e:
            self.server_available = False
            logger.error(f"❌ Update check failed: {e}")
            logger.error("Application will continue without update check")
            return None

    async def _perform_update_check(self) -> Optional[dict]:
        """
        Perform the actual update check

        Returns:
            Update info dict or None
        """
        try:
            # Import here to avoid startup delays if module has issues
            from sync.update_client import SafeUpdateClient

            client = SafeUpdateClient(
                server_url=self.server_url,
                app_root=self.app_root,
                data_dir=self.data_dir,
                current_version=self.current_version,
                scan_extensions=None  # Scan all files
            )

            # Check for updates
            update_info = await client.check_for_updates()
            return update_info

        except ImportError as e:
            logger.error(f"Could not import update client: {e}")
            return None
        except Exception as e:
            logger.debug(f"Update check error: {e}")
            raise

    async def start_background_checking(self):
        """
        Start background task to periodically check for updates
        """
        if not self.enable_background:
            logger.info("Background update checking is disabled")
            return

        if self.background_task and not self.background_task.done():
            logger.warning("Background checking already running")
            return

        logger.info("="*70)
        logger.info("BACKGROUND UPDATE CHECKING ENABLED")
        logger.info("="*70)
        logger.info(f"Check interval: {self.check_interval.total_seconds() / 60:.0f} minutes")
        logger.info(f"Server: {self.server_url}")
        logger.info("="*70)

        self.background_task = asyncio.create_task(self._background_check_loop())

    async def _background_check_loop(self):
        """
        Background loop that periodically checks for updates
        """
        # Wait a bit before first check to let app fully start
        await asyncio.sleep(300)  # 5 minutes

        while True:
            try:
                logger.info("🔍 Background: Checking for updates...")

                update_info = await self._perform_update_check()

                if update_info:
                    # Server is back online
                    if not self.server_available:
                        logger.info("="*70)
                        logger.info("✅ UPDATE SERVER RECONNECTED")
                        logger.info("="*70)
                        self.server_available = True

                        if self.on_server_reconnect:
                            try:
                                self.on_server_reconnect()
                            except Exception as e:
                                logger.error(f"Error in reconnect callback: {e}")

                    self.last_check_successful = True
                    self.last_check_time = datetime.now()

                    if update_info.get("update_available"):
                        logger.info("📦 Background: Update available!")

                        if self.on_update_available:
                            try:
                                self.on_update_available(update_info)
                            except Exception as e:
                                logger.error(f"Error in update callback: {e}")
                    else:
                        logger.info("✅ Background: App is up to date")
                else:
                    if self.server_available:
                        logger.warning("⚠️  Background: Server now offline")
                    self.server_available = False
                    self.last_check_successful = False

            except Exception as e:
                logger.debug(f"Background check error: {e}")
                self.last_check_successful = False
                if self.server_available:
                    logger.warning("⚠️  Background: Lost connection to server")
                self.server_available = False

            # Wait before next check
            await asyncio.sleep(self.check_interval.total_seconds())

    def stop_background_checking(self):
        """Stop background checking"""
        if self.background_task and not self.background_task.done():
            self.background_task.cancel()
            logger.info("Background update checking stopped")

    async def install_offline_update(self, bundle_path: Path, progress_callback=None) -> bool:
        """
        Install an offline update from a local ZIP bundle

        Args:
            bundle_path: Path to the update ZIP file
            progress_callback: Optional callback(current, total, message)

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Offline update requested: {bundle_path}")

        try:
            # Import here to avoid startup delays
            from sync.update_client import SafeUpdateClient

            client = SafeUpdateClient(
                server_url=self.server_url,
                app_root=self.app_root,
                data_dir=self.data_dir,
                current_version=self.current_version,
                scan_extensions=None
            )

            success = await client.install_offline_update(bundle_path, progress_callback)

            if success:
                self.last_check_successful = True
                self.last_check_time = datetime.now()

            return success

        except Exception as e:
            logger.error(f"Offline update failed: {e}")
            return False

    async def manual_check(self) -> Optional[dict]:
        """
        Manually trigger an update check (e.g., from UI button)

        Returns:
            Update info if available
        """
        logger.info("Manual update check requested")

        try:
            update_info = await asyncio.wait_for(
                self._perform_update_check(),
                timeout=30  # Longer timeout for manual check
            )

            if update_info:
                self.server_available = True
                self.last_check_time = datetime.now()
                self.last_check_successful = True
                return update_info
            else:
                self.server_available = False
                return None

        except asyncio.TimeoutError:
            logger.error("Manual check timed out - server not responding")
            return None
        except Exception as e:
            logger.error(f"Manual check failed: {e}")
            return None

    def get_status(self) -> dict:
        """
        Get current status of update manager

        Returns:
            Status dict with current state
        """
        return {
            "server_available": self.server_available,
            "last_check_time": self.last_check_time.isoformat() if self.last_check_time else None,
            "last_check_successful": self.last_check_successful,
            "background_checking": self.background_task is not None and not self.background_task.done(),
            "current_version": self.current_version,
            "server_url": self.server_url
        }


# ============================================================================
# INTEGRATION HELPERS
# ============================================================================

async def check_for_updates_at_startup(
    server_url: str,
    app_root: Path,
    data_dir: Path,
    current_version: str,
    max_wait_seconds: int = 5
) -> UpdateManager:
    """
    Helper function for simple startup integration

    This is non-blocking and will not delay app startup

    Args:
        server_url: Update server URL
        app_root: Application root directory
        data_dir: User data directory
        current_version: Current version
        max_wait_seconds: Max time to wait for server

    Returns:
        UpdateManager instance (can be used for background checking)
    """
    manager = UpdateManager(
        server_url=server_url,
        app_root=app_root,
        data_dir=data_dir,
        current_version=current_version,
        max_startup_wait_seconds=max_wait_seconds,
        enable_background_checking=True
    )

    # Check at startup (non-blocking)
    await manager.check_at_startup()

    # Start background checking
    await manager.start_background_checking()

    return manager
