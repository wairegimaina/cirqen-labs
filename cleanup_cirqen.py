#!/usr/bin/env python3
"""
Cirqen Complete Cleanup Utility
Comprehensive cleanup of all Cirqen processes, ports, locks, and sessions
Handles both port-based services and worker processes
"""

import os
import sys
import psutil
import signal
import time
import json
import socket
import shutil
from pathlib import Path
from typing import List, Dict, Tuple, Optional

# ============================================================================
# CONFIGURATION
# ============================================================================

# Port ranges for different services
PORT_RANGES = {
    'postgresql_local': (2215, 2250),
    'postgresql_hq': (3315, 3350),
    'redis': (7788, 7820),
    'django': (8000, 8050),
    'instance_lock': (59999, 60050)
}

# Process patterns to search for
PROCESS_PATTERNS = {
    'celery': ['celery', 'celery worker', 'celery beat', 'billiard'],
    'postgres': ['postgres', 'postgresql'],
    'redis': ['redis-server', 'redis'],
    'django': ['runserver', 'gunicorn', 'uwsgi'],
    'sync_agent': ['sync_agent', 'sync_agent.py'],
    'cirqen': ['cirqen', 'Cirqen.exe', 'main.py']
}

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_data_path() -> Path:
    """Get the Cirqen data directory"""
    if sys.platform == 'win32':
        data_path = Path(os.getenv('APPDATA')) / 'cirqen'
    else:
        data_path = Path.home() / '.local/share/cirqen'
    return data_path


def print_banner(text: str, char: str = "="):
    """Print formatted banner"""
    print(f"\n{char * 70}")
    print(f"  {text}")
    print(f"{char * 70}")


def print_section(text: str):
    """Print section header"""
    print(f"\n{'─' * 70}")
    print(f"  {text}")
    print(f"{'─' * 70}")


def print_success(message: str, indent: int = 2):
    """Print success message"""
    print(f"{' ' * indent}✅ {message}")


def print_warning(message: str, indent: int = 2):
    """Print warning message"""
    print(f"{' ' * indent}⚠️  {message}")


def print_error(message: str, indent: int = 2):
    """Print error message"""
    print(f"{' ' * indent}❌ {message}")


def print_info(message: str, indent: int = 2):
    """Print info message"""
    print(f"{' ' * indent}ℹ️  {message}")


# ============================================================================
# SESSION MANAGEMENT
# ============================================================================

def load_session_data() -> Optional[Dict]:
    """Load session data including ports"""
    session_file = get_data_path() / 'session.json'

    if not session_file.exists():
        return None

    try:
        with open(session_file, 'r') as f:
            session_data = json.load(f)

        if session_data.get('ports'):
            print_info("Found session file with active ports:")
            for service, port in session_data['ports'].items():
                print(f"    • {service}: {port}")

        return session_data
    except Exception as e:
        print_warning(f"Could not read session file: {e}")
        return None


# ============================================================================
# PROCESS MANAGEMENT
# ============================================================================

def kill_process_safely(proc: psutil.Process, timeout: int = 5) -> bool:
    """
    Safely terminate a process with graceful shutdown attempt

    Returns:
        bool: True if process was killed successfully
    """
    try:
        pid = proc.pid
        name = proc.name()

        # Don't kill ourselves
        if pid == os.getpid():
            return False

        print(f"    🔪 Terminating {name} (PID: {pid})")

        # Try graceful termination first
        proc.terminate()

        try:
            proc.wait(timeout=timeout)
            print(f"       ✓ Terminated gracefully")
            return True
        except psutil.TimeoutExpired:
            # Force kill if needed
            print(f"       ⚡ Force killing...")
            proc.kill()
            proc.wait()
            print(f"       ✓ Force killed")
            return True

    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied as e:
        print_warning(f"Access denied for PID {proc.pid}: {e}", indent=6)
        return False
    except Exception as e:
        print_error(f"Error killing process: {e}", indent=6)
        return False


def kill_processes_by_pattern(patterns: List[str], service_name: str) -> int:
    """
    Kill all processes matching any of the given patterns

    Args:
        patterns: List of patterns to match in process name or command line
        service_name: Name of service for display

    Returns:
        int: Number of processes killed
    """
    killed_count = 0
    seen_pids = set()

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.pid in seen_pids:
                continue

            proc_info = proc.info
            proc_name = (proc_info['name'] or '').lower()
            cmdline = ' '.join(proc_info['cmdline'] or []).lower()

            # Check if process matches any pattern
            for pattern in patterns:
                pattern_lower = pattern.lower()

                if pattern_lower in proc_name or pattern_lower in cmdline:
                    if kill_process_safely(proc):
                        killed_count += 1
                        seen_pids.add(proc.pid)
                    break  # Don't check other patterns for this process

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return killed_count


def kill_processes_on_port(port: int) -> int:
    """
    Kill all processes using a specific port

    Returns:
        int: Number of processes killed
    """
    killed_count = 0

    for proc in psutil.process_iter(['pid', 'name', 'connections']):
        try:
            connections = proc.connections()

            for conn in connections:
                if hasattr(conn.laddr, 'port') and conn.laddr.port == port:
                    if kill_process_safely(proc):
                        killed_count += 1
                    break  # Only kill once per process

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return killed_count


def kill_processes_in_port_range(start: int, end: int) -> Tuple[int, List[int]]:
    """
    Kill processes in a port range

    Returns:
        Tuple of (processes_killed, ports_freed)
    """
    killed_count = 0
    ports_freed = []

    for port in range(start, end + 1):
        killed = kill_processes_on_port(port)
        if killed > 0:
            killed_count += killed
            ports_freed.append(port)

    return killed_count, ports_freed


# ============================================================================
# LOCK FILE MANAGEMENT
# ============================================================================

def remove_lock_files() -> Tuple[int, List[str]]:
    """
    Remove all lock and session files

    Returns:
        Tuple of (files_removed, failed_files)
    """
    data_path = get_data_path()

    lock_files = [
        ('Instance lock', data_path / 'cirqen.lock'),
        ('Session file', data_path / 'session.json'),
        ('PostgreSQL Local lock', data_path / 'postgres' / 'postmaster.pid'),
        ('PostgreSQL HQ lock', data_path / 'postgres_hq' / 'postmaster.pid'),
    ]

    removed_count = 0
    failed_files = []

    for description, file_path in lock_files:
        if file_path.exists():
            try:
                # Show file info if it's JSON
                if file_path.suffix == '.json':
                    try:
                        with open(file_path, 'r') as f:
                            data = json.load(f)
                        print(f"    📄 {description}:")
                        print(f"       PID: {data.get('pid', 'N/A')}")
                        if 'ports' in data:
                            print(f"       Ports: {len(data['ports'])} configured")
                    except:
                        pass

                file_path.unlink()
                print_success(f"Removed {description}", indent=4)
                removed_count += 1

            except Exception as e:
                print_error(f"Failed to remove {description}: {e}", indent=4)
                failed_files.append(str(file_path))
        else:
            print_info(f"{description} not found", indent=4)

    return removed_count, failed_files


def cleanup_temp_files(interactive: bool = True) -> int:
    """Clean up temporary files"""
    data_path = get_data_path()

    temp_patterns = [
        '*.tmp',
        '*.log.old',
        '*.pyc',
        '__pycache__',
    ]

    if interactive:
        print_info("This will remove temporary files but keep your data")
        response = input("\n  Proceed with temp file cleanup? (y/N): ").strip().lower()

        if response != 'y':
            print_info("Skipped temporary file cleanup")
            return 0

    removed_count = 0

    for pattern in temp_patterns:
        if pattern == '__pycache__':
            for pycache in data_path.rglob('__pycache__'):
                try:
                    shutil.rmtree(pycache)
                    print(f"    ✓ Removed: {pycache.relative_to(data_path)}")
                    removed_count += 1
                except Exception as e:
                    print_warning(f"Could not remove {pycache.name}: {e}", indent=4)
        else:
            for temp_file in data_path.rglob(pattern):
                try:
                    temp_file.unlink()
                    print(f"    ✓ Removed: {temp_file.relative_to(data_path)}")
                    removed_count += 1
                except Exception as e:
                    print_warning(f"Could not remove {temp_file.name}: {e}", indent=4)

    return removed_count


# ============================================================================
# VERIFICATION FUNCTIONS
# ============================================================================

def verify_no_processes() -> Tuple[bool, List[str]]:
    """Verify no Cirqen processes are running"""
    remaining = []

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.pid == os.getpid():
                continue

            proc_name = (proc.info['name'] or '').lower()
            cmdline = ' '.join(proc.info['cmdline'] or []).lower()

            # Check against all patterns
            for service, patterns in PROCESS_PATTERNS.items():
                for pattern in patterns:
                    if pattern.lower() in proc_name or pattern.lower() in cmdline:
                        remaining.append(f"{proc.info['name']} (PID: {proc.pid})")
                        break

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return len(remaining) == 0, remaining


def verify_ports_free(session_ports: Optional[Dict] = None) -> Tuple[bool, List[str]]:
    """Verify ports are free"""
    ports_in_use = []

    if session_ports:
        # Check specific session ports
        for service, port in session_ports.items():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)

            try:
                if sock.connect_ex(('127.0.0.1', port)) == 0:
                    ports_in_use.append(f"{service}:{port}")
            finally:
                sock.close()
    else:
        # Sample check of port ranges
        for service, (start, end) in PORT_RANGES.items():
            # Check first 3 ports in each range
            for port in range(start, min(start + 3, end + 1)):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)

                try:
                    if sock.connect_ex(('127.0.0.1', port)) == 0:
                        ports_in_use.append(f"{service}:{port}")
                        break
                finally:
                    sock.close()

    return len(ports_in_use) == 0, ports_in_use


def verify_no_locks() -> Tuple[bool, List[str]]:
    """Verify no lock files exist"""
    data_path = get_data_path()

    lock_files = [
        data_path / 'cirqen.lock',
        data_path / 'session.json',
        data_path / 'postgres' / 'postmaster.pid',
        data_path / 'postgres_hq' / 'postmaster.pid',
    ]

    existing = [f.name for f in lock_files if f.exists()]

    return len(existing) == 0, existing


# ============================================================================
# MAIN CLEANUP PROCESS
# ============================================================================

def cleanup_all_processes(session_data: Optional[Dict] = None) -> Dict[str, int]:
    """
    Clean up all Cirqen processes

    Returns:
        Dictionary with cleanup statistics
    """
    stats = {}
    total_killed = 0

    print_section("STEP 1: Stopping Cirqen Processes")

    # 1. Celery Workers (no port binding)
    print("\n  🔄 Celery Workers:")
    killed = kill_processes_by_pattern(PROCESS_PATTERNS['celery'], 'Celery')
    stats['celery'] = killed
    total_killed += killed
    if killed > 0:
        print_success(f"Stopped {killed} Celery process(es)", indent=4)
    else:
        print_info("No Celery processes found", indent=4)

    # 2. Django Web Server
    print("\n  🌐 Django Web Server:")
    killed = kill_processes_by_pattern(PROCESS_PATTERNS['django'], 'Django')
    stats['django'] = killed
    total_killed += killed
    if killed > 0:
        print_success(f"Stopped {killed} Django process(es)", indent=4)
    else:
        print_info("No Django processes found", indent=4)

    # 3. PostgreSQL
    print("\n  💾 PostgreSQL:")
    killed = kill_processes_by_pattern(PROCESS_PATTERNS['postgres'], 'PostgreSQL')
    stats['postgres'] = killed
    total_killed += killed
    if killed > 0:
        print_success(f"Stopped {killed} PostgreSQL process(es)", indent=4)
    else:
        print_info("No PostgreSQL processes found", indent=4)

    # 4. Redis
    print("\n  ⚡ Redis:")
    killed = kill_processes_by_pattern(PROCESS_PATTERNS['redis'], 'Redis')
    stats['redis'] = killed
    total_killed += killed
    if killed > 0:
        print_success(f"Stopped {killed} Redis process(es)", indent=4)
    else:
        print_info("No Redis processes found", indent=4)

    # 5. Sync Agent
    print("\n  🔄 Sync Agent:")
    killed = kill_processes_by_pattern(PROCESS_PATTERNS['sync_agent'], 'Sync Agent')
    stats['sync_agent'] = killed
    total_killed += killed
    if killed > 0:
        print_success(f"Stopped {killed} Sync Agent process(es)", indent=4)
    else:
        print_info("No Sync Agent processes found", indent=4)

    # 6. Main Cirqen Application
    print("\n  🖥️  Main Cirqen Application:")
    killed = kill_processes_by_pattern(PROCESS_PATTERNS['cirqen'], 'Cirqen')
    stats['cirqen'] = killed
    total_killed += killed
    if killed > 0:
        print_success(f"Stopped {killed} Cirqen process(es)", indent=4)
    else:
        print_info("No Cirqen processes found", indent=4)

    stats['total'] = total_killed

    if total_killed > 0:
        print_info(f"\n⏳ Waiting for processes to terminate completely...")
        time.sleep(3)

    return stats


def cleanup_ports(session_data: Optional[Dict] = None) -> Dict[str, int]:
    """Clean up processes on ports"""
    print_section("STEP 2: Freeing Ports")

    stats = {'total_killed': 0, 'ports_freed': 0}

    if session_data and session_data.get('ports'):
        # Clean specific session ports
        print_info("Cleaning session-specific ports...")

        for service, port in session_data['ports'].items():
            killed = kill_processes_on_port(port)
            if killed > 0:
                print_success(f"Freed port {port} ({service})", indent=4)
                stats['total_killed'] += killed
                stats['ports_freed'] += 1
    else:
        # Clean port ranges
        print_info("Scanning default port ranges...")

        for service, (start, end) in PORT_RANGES.items():
            print(f"\n  📡 {service} ({start}-{end}):")
            killed, ports = kill_processes_in_port_range(start, end)

            if killed > 0:
                print_success(f"Freed {len(ports)} port(s)", indent=4)
                stats['total_killed'] += killed
                stats['ports_freed'] += len(ports)
            else:
                print_info("All ports free", indent=4)

    if stats['total_killed'] == 0:
        print_info("\n✓ All ports were already free")
    else:
        print_info(f"\n⏳ Waiting for ports to release...")
        time.sleep(2)

    return stats


def main():
    """Main cleanup function"""
    print_banner("CIRQEN COMPLETE CLEANUP UTILITY", "=")

    data_path = get_data_path()
    print(f"\n📁 Data Directory: {data_path}")

    # Check if data directory exists
    if not data_path.exists():
        print_warning("\nData directory does not exist")
        print_info("Cirqen has never been run on this system")
        print_banner("Nothing to clean up!", "=")
        return 0

    # Load session data
    print_info("\n🔍 Checking for active session...")
    session_data = load_session_data()

    # Cleanup processes
    process_stats = cleanup_all_processes(session_data)

    # Cleanup ports
    port_stats = cleanup_ports(session_data)

    # Remove lock files
    print_section("STEP 3: Removing Lock Files")
    removed, failed = remove_lock_files()

    if removed > 0:
        print_success(f"\nRemoved {removed} lock file(s)")
    if failed:
        print_warning(f"Failed to remove {len(failed)} file(s)")

    # Optional: Clean temp files
    print_section("STEP 4: Cleaning Temporary Files (Optional)")
    temp_removed = cleanup_temp_files(interactive=True)

    if temp_removed > 0:
        print_success(f"\nRemoved {temp_removed} temporary file(s)")

    # Verification
    print_section("STEP 5: Verification")

    procs_ok, remaining_procs = verify_no_processes()
    ports_ok, ports_in_use = verify_ports_free(session_data.get('ports') if session_data else None)
    locks_ok, existing_locks = verify_no_locks()

    # Display verification results
    if procs_ok:
        print_success("✓ No Cirqen processes running", indent=2)
    else:
        print_warning(f"Found {len(remaining_procs)} process(es) still running:", indent=2)
        for proc in remaining_procs[:5]:
            print(f"      • {proc}")
        if len(remaining_procs) > 5:
            print(f"      ... and {len(remaining_procs) - 5} more")

    if ports_ok:
        print_success("✓ All checked ports are free", indent=2)
    else:
        print_info(f"{len(ports_in_use)} port(s) in use (Cirqen will use alternatives)", indent=2)

    if locks_ok:
        print_success("✓ No lock files remain", indent=2)
    else:
        print_warning(f"Found {len(existing_locks)} lock file(s):", indent=2)
        for lock in existing_locks:
            print(f"      • {lock}")

    # Final summary
    print_banner("CLEANUP SUMMARY", "=")

    success = procs_ok and locks_ok

    if success:
        print("\n  ✅ CLEANUP SUCCESSFUL!")
        print(f"\n  📊 Statistics:")
        print(f"     • Processes stopped: {process_stats['total']}")
        print(f"     • Ports freed: {port_stats['ports_freed']}")
        print(f"     • Lock files removed: {removed}")
        if temp_removed > 0:
            print(f"     • Temp files cleaned: {temp_removed}")

        print(f"\n  🚀 You can now start Cirqen again")

        if not ports_ok:
            print(f"\n  ℹ️  Note: Some ports are in use by other applications")
            print(f"     Cirqen will automatically find alternative ports")
    else:
        print("\n  ⚠️  CLEANUP INCOMPLETE")
        print(f"\n  Some issues remain - see warnings above")

        print(f"\n  🔧 Troubleshooting:")
        if not procs_ok:
            print(f"     • Try running this script again")
            print(f"     • Check Task Manager/Activity Monitor")
            print(f"     • Reboot if processes persist")

        if not locks_ok:
            print(f"     • Try running as administrator/sudo")
            print(f"     • Or manually delete lock files")

        print(f"\n  📚 For more help, check the Cirqen documentation")

    print("\n" + "=" * 70)

    # Keep window open on Windows
    if sys.platform == 'win32':
        input("\nPress Enter to exit...")

    return 0 if success else 1


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Cleanup interrupted by user")
        if sys.platform == 'win32':
            input("\nPress Enter to exit...")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")

        import traceback
        print("\nDetailed traceback:")
        traceback.print_exc()

        if sys.platform == 'win32':
            input("\nPress Enter to exit...")
        sys.exit(1)
