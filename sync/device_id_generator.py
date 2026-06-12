#!/usr/bin/env python3
"""
device_id_generator.py - Auto-Generate Unique Device IDs

This module provides functions to automatically generate and persist
unique device identifiers for sync agents.

Usage:
    from device_id_generator import get_or_create_client_id

    client_id = get_or_create_client_id()
    print(f"This device's ID: {client_id}")
"""

import os
import uuid
import socket
import hashlib
import platform
from pathlib import Path
from typing import Optional
import logging

LOG = logging.getLogger(__name__)


def get_or_create_client_id(force_regenerate: bool = False) -> str:
    """
    Get or create a unique CLIENT_ID for this device.

    Priority:
    1. Environment variable CLIENT_ID (manual override)
    2. Saved ID file (~/.cmms/client_id)
    3. Auto-generate based on hardware

    Args:
        force_regenerate: If True, ignore saved ID and generate new one

    Returns:
        str: Unique client ID for this device

    Examples:
        >>> client_id = get_or_create_client_id()
        >>> print(client_id)
        device-a1b2c3d4e5f6g7h8

        >>> # Force new ID (useful for testing)
        >>> new_id = get_or_create_client_id(force_regenerate=True)
    """

    # Priority 1: Check environment variable (highest priority)
    if not force_regenerate:
        env_client_id = os.getenv('CLIENT_ID', '').strip()
        if env_client_id:
            LOG.info(f"✅ Using CLIENT_ID from environment: {env_client_id}")
            return env_client_id

    # Get storage location
    client_id_file = _get_client_id_file()

    # Priority 2: Check saved file
    if not force_regenerate and client_id_file.exists():
        try:
            saved_id = client_id_file.read_text().strip()
            if saved_id:
                LOG.info(f"✅ Using saved CLIENT_ID: {saved_id}")
                LOG.info(f"   From: {client_id_file}")
                return saved_id
        except Exception as e:
            LOG.warning(f"⚠️  Could not read saved CLIENT_ID: {e}")

    # Priority 3: Auto-generate new ID
    LOG.info("🆕 Generating new CLIENT_ID...")
    client_id = _generate_unique_client_id()

    # Save for future use
    try:
        client_id_file.parent.mkdir(parents=True, exist_ok=True)
        client_id_file.write_text(client_id)
        LOG.info(f"✅ Generated and saved CLIENT_ID: {client_id}")
        LOG.info(f"   Saved to: {client_id_file}")
    except Exception as e:
        LOG.error(f"❌ Failed to save CLIENT_ID: {e}")
        LOG.warning("   CLIENT_ID will be regenerated on next restart!")

    return client_id


def _get_client_id_file() -> Path:
    """Get the path to the client ID storage file"""
    # Try standard locations
    config_dirs = [
        Path.home() / '.cmms',           # Primary
        Path.home() / '.config' / 'cmms', # XDG standard
        Path('/etc/cmms'),                # System-wide (requires root)
    ]

    # Use first writable directory
    for config_dir in config_dirs:
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            test_file = config_dir / '.test_write'
            test_file.write_text('test')
            test_file.unlink()
            return config_dir / 'client_id'
        except (PermissionError, OSError):
            continue

    # Fallback: temp directory (not ideal, but works)
    import tempfile
    temp_dir = Path(tempfile.gettempdir()) / 'cmms'
    temp_dir.mkdir(parents=True, exist_ok=True)
    LOG.warning(f"⚠️  Using temp directory for CLIENT_ID: {temp_dir}")
    return temp_dir / 'client_id'


def _generate_unique_client_id() -> str:
    """
    Generate a unique device ID based on hardware characteristics.

    Methods (in order of preference):
    1. MAC address + hostname (most reliable)
    2. Machine ID (Linux/systemd)
    3. Hardware UUID (cross-platform)
    4. Random UUID (fallback)

    Returns:
        str: Unique device ID in format "device-XXXXXXXXXXXXXXXX"
    """

    # Method 1: MAC address + hostname (most common)
    try:
        mac = uuid.getnode()
        mac_hex = f"{mac:012x}"
        hostname = socket.gethostname()

        # Verify MAC is not a random fallback (would be different each time)
        if mac != uuid.getnode():
            raise ValueError("MAC address is unstable")

        # Create deterministic hash
        unique_string = f"{hostname}-{mac_hex}"
        device_hash = hashlib.sha256(unique_string.encode()).hexdigest()[:16]

        client_id = f"device-{device_hash}"
        LOG.info(f"   Generated from: MAC={mac_hex}, hostname={hostname}")
        return client_id

    except Exception as e:
        LOG.warning(f"   Method 1 (MAC) failed: {e}")

    # Method 2: Linux machine-id (very stable on Linux)
    try:
        machine_id_files = [
            '/etc/machine-id',
            '/var/lib/dbus/machine-id'
        ]

        for machine_id_file in machine_id_files:
            if Path(machine_id_file).exists():
                machine_id = Path(machine_id_file).read_text().strip()
                if machine_id:
                    device_hash = machine_id[:16]
                    client_id = f"device-{device_hash}"
                    LOG.info(f"   Generated from: machine-id ({machine_id_file})")
                    return client_id
    except Exception as e:
        LOG.warning(f"   Method 2 (machine-id) failed: {e}")

    # Method 3: Platform-specific hardware UUID
    try:
        system = platform.system()

        if system == 'Linux':
            # Try to read DMI UUID
            dmi_uuid_file = '/sys/class/dmi/id/product_uuid'
            if Path(dmi_uuid_file).exists():
                hw_uuid = Path(dmi_uuid_file).read_text().strip()
                device_hash = hashlib.sha256(hw_uuid.encode()).hexdigest()[:16]
                client_id = f"device-{device_hash}"
                LOG.info(f"   Generated from: DMI UUID")
                return client_id

        elif system == 'Darwin':  # macOS
            import subprocess
            result = subprocess.run(
                ['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
                capture_output=True,
                text=True
            )
            if 'IOPlatformUUID' in result.stdout:
                # Extract UUID from output
                for line in result.stdout.split('\n'):
                    if 'IOPlatformUUID' in line:
                        hw_uuid = line.split('"')[3]
                        device_hash = hashlib.sha256(hw_uuid.encode()).hexdigest()[:16]
                        client_id = f"device-{device_hash}"
                        LOG.info(f"   Generated from: IOPlatformUUID")
                        return client_id

        elif system == 'Windows':
            import subprocess
            result = subprocess.run(
                ['wmic', 'csproduct', 'get', 'UUID'],
                capture_output=True,
                text=True
            )
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                hw_uuid = lines[1].strip()
                device_hash = hashlib.sha256(hw_uuid.encode()).hexdigest()[:16]
                client_id = f"device-{device_hash}"
                LOG.info(f"   Generated from: Windows UUID")
                return client_id

    except Exception as e:
        LOG.warning(f"   Method 3 (Hardware UUID) failed: {e}")

    # Method 4: Random UUID (last resort - NOT hardware-based)
    LOG.warning("   ⚠️  Using random UUID - will change if file is lost!")
    random_id = uuid.uuid4().hex[:16]
    client_id = f"device-{random_id}"
    LOG.info(f"   Generated from: Random UUID (fallback)")

    return client_id


def get_device_info() -> dict:
    """
    Get detailed information about this device for registration.

    Returns:
        dict: Device information including hostname, OS, etc.
    """
    try:
        return {
            'hostname': socket.gethostname(),
            'platform': platform.system(),
            'platform_release': platform.release(),
            'platform_version': platform.version(),
            'architecture': platform.machine(),
            'processor': platform.processor(),
            'python_version': platform.python_version(),
        }
    except Exception as e:
        LOG.error(f"Failed to get device info: {e}")
        return {
            'hostname': 'unknown',
            'platform': 'unknown'
        }


def validate_client_id(client_id: str) -> bool:
    """
    Validate that a CLIENT_ID follows the expected format.

    Args:
        client_id: The client ID to validate

    Returns:
        bool: True if valid, False otherwise

    Examples:
        >>> validate_client_id("device-a1b2c3d4e5f6g7h8")
        True
        >>> validate_client_id("invalid-id")
        False
    """
    if not client_id:
        return False

    # Should start with "device-" or be a custom format
    if client_id.startswith('device-'):
        # Should have at least 10 characters after "device-"
        return len(client_id) >= 17

    # Allow custom formats (for manual override)
    # Just check it's not empty and reasonable length
    return 3 <= len(client_id) <= 100


# CLI for testing
if __name__ == '__main__':
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )

    parser = argparse.ArgumentParser(
        description='Device ID Generator - Generate and manage device IDs'
    )
    parser.add_argument(
        '--regenerate',
        action='store_true',
        help='Force regenerate ID (ignore saved)'
    )
    parser.add_argument(
        '--info',
        action='store_true',
        help='Show device information'
    )

    args = parser.parse_args()

    print()
    print("=" * 80)
    print("🔧 DEVICE ID GENERATOR")
    print("=" * 80)
    print()

    # Get or create ID
    client_id = get_or_create_client_id(force_regenerate=args.regenerate)

    print()
    print("=" * 80)
    print(f"📱 YOUR DEVICE ID: {client_id}")
    print("=" * 80)
    print()

    if args.info:
        print("📊 Device Information:")
        print("-" * 80)
        device_info = get_device_info()
        for key, value in device_info.items():
            print(f"  {key:20s}: {value}")
        print()

    print("💡 Add to your .env file:")
    print(f"   CLIENT_ID={client_id}")
    print()
    print("   Or leave blank for auto-generation:")
    print(f"   CLIENT_ID=")
    print()
    print("=" * 80)
