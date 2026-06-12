#!/usr/bin/env python3
"""
PostgreSQL Diagnostic Script for Cirqen
Checks why PostgreSQL won't start and provides detailed diagnostics
"""

import os
import sys
import subprocess
from pathlib import Path
import json

def print_section(title):
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70)

def check_data_directory():
    """Check the data directory structure"""
    print_section("1. DATA DIRECTORY CHECK")

    data_path = Path.home() / '.local/share/cirqen'

    print(f"Data path: {data_path}")
    print(f"Exists: {data_path.exists()}")

    if not data_path.exists():
        print("❌ Data directory does not exist!")
        return False

    # Check subdirectories
    subdirs = ['postgres', 'redis', 'logs', 'media', 'sync_state', 'sockets']

    for subdir in subdirs:
        path = data_path / subdir
        exists = path.exists()
        symbol = "✅" if exists else "❌"
        print(f"  {symbol} {subdir}/")

        if exists and subdir == 'postgres':
            # Check PostgreSQL data directory
            pg_version = path / 'PG_VERSION'
            if pg_version.exists():
                version = pg_version.read_text().strip()
                print(f"      PostgreSQL version: {version}")
            else:
                print(f"      ❌ PG_VERSION not found - database not initialized!")
                return False

    return True

def check_runtime_directory():
    """Check the runtime PostgreSQL binaries"""
    print_section("2. RUNTIME DIRECTORY CHECK")

    # Try to find where the app is installed
    possible_locations = [
        Path.home() / 'Desktop/project/cirqen_desktop',
        Path('/home/ians/Desktop/project/cirqen_desktop'),
        Path.cwd(),
    ]

    runtime_dir = None

    for location in possible_locations:
        if location.exists():
            dist_dir = location / 'dist/Cirqen/runtime'
            if dist_dir.exists():
                runtime_dir = dist_dir
                break

    if not runtime_dir:
        print("❌ Could not find runtime directory!")
        print("\nSearched locations:")
        for loc in possible_locations:
            print(f"  • {loc}/dist/Cirqen/runtime")
        return False

    print(f"Runtime directory: {runtime_dir}")

    # Check PostgreSQL binaries
    pg_dir = runtime_dir / 'postgresql'
    if not pg_dir.exists():
        print(f"❌ PostgreSQL directory not found: {pg_dir}")
        return False

    print(f"  ✅ PostgreSQL directory: {pg_dir}")

    # Check critical binaries
    binaries = ['postgres', 'initdb', 'pg_ctl', 'psql']
    pg_bin = pg_dir / 'bin'

    if not pg_bin.exists():
        print(f"  ❌ bin/ directory not found: {pg_bin}")
        return False

    print(f"  ✅ bin/ directory exists")

    all_exist = True
    for binary in binaries:
        binary_path = pg_bin / binary
        exists = binary_path.exists()
        executable = binary_path.is_file() and os.access(binary_path, os.X_OK) if exists else False

        if exists and executable:
            print(f"    ✅ {binary} (executable)")
        elif exists:
            print(f"    ⚠️  {binary} (not executable)")
            all_exist = False
        else:
            print(f"    ❌ {binary} (missing)")
            all_exist = False

    # Check share directory (CRITICAL!)
    pg_share = pg_dir / 'share'
    if not pg_share.exists():
        print(f"  ❌ share/ directory not found: {pg_share}")
        print("     This is CRITICAL - initdb will fail without it!")
        return False

    print(f"  ✅ share/ directory exists")

    # Check for postgres.bki (most critical file)
    postgres_bki = list(pg_share.rglob('postgres.bki'))
    if postgres_bki:
        print(f"    ✅ postgres.bki found: {postgres_bki[0].relative_to(pg_share)}")
    else:
        print(f"    ❌ postgres.bki NOT FOUND!")
        print("       This file is REQUIRED for database initialization!")
        return False

    return all_exist

def check_ports():
    """Check if required ports are available"""
    print_section("3. PORT AVAILABILITY CHECK")

    import socket

    ports = {
        2215: 'PostgreSQL Local',
        5432: 'PostgreSQL HQ',
        7788: 'Redis',
        8000: 'Django',
        59999: 'Instance Lock'
    }

    for port, service in ports.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()

        if result == 0:
            print(f"  ⚠️  Port {port} ({service}): IN USE")

            # Try to find what's using it
            try:
                output = subprocess.check_output(['lsof', '-i', f':{port}'],
                                                stderr=subprocess.DEVNULL,
                                                text=True)
                lines = output.strip().split('\n')[1:]  # Skip header
                if lines:
                    print(f"      Used by: {lines[0].split()[0]} (PID: {lines[0].split()[1]})")
            except:
                pass
        else:
            print(f"  ✅ Port {port} ({service}): Available")

    return True

def check_session_file():
    """Check the session file"""
    print_section("4. SESSION FILE CHECK")

    session_file = Path.home() / '.local/share/cirqen/session.json'

    if not session_file.exists():
        print("  ℹ️  No session file (normal for first run)")
        return True

    print(f"Session file: {session_file}")

    try:
        with open(session_file, 'r') as f:
            session_data = json.load(f)

        print(f"  • PID: {session_data.get('pid')}")
        print(f"  • Timestamp: {session_data.get('timestamp')}")
        print(f"  • Ports: {session_data.get('ports')}")

        # Check if PID is still running
        pid = session_data.get('pid')
        if pid:
            try:
                import psutil
                if psutil.pid_exists(pid):
                    proc = psutil.Process(pid)
                    print(f"  ⚠️  Process {pid} still exists: {proc.name()}")
                else:
                    print(f"  ℹ️  Process {pid} no longer exists (stale session)")
            except:
                pass

    except Exception as e:
        print(f"  ❌ Error reading session file: {e}")
        return False

    return True

def check_lock_file():
    """Check the lock file"""
    print_section("5. LOCK FILE CHECK")

    lock_file = Path.home() / '.local/share/cirqen/cirqen.lock'

    if not lock_file.exists():
        print("  ℹ️  No lock file (normal)")
        return True

    print(f"Lock file: {lock_file}")

    try:
        with open(lock_file, 'r') as f:
            lock_data = json.load(f)

        print(f"  • PID: {lock_data.get('pid')}")
        print(f"  • Lock Port: {lock_data.get('lock_port')}")
        print(f"  • Timestamp: {lock_data.get('timestamp')}")

        # Check if PID is still running
        pid = lock_data.get('pid')
        if pid:
            try:
                import psutil
                if psutil.pid_exists(pid):
                    proc = psutil.Process(pid)
                    print(f"  ⚠️  Another instance running: PID {pid} ({proc.name()})")
                    print(f"     You need to close it or run cleanup_cirqen.py")
                    return False
                else:
                    print(f"  ℹ️  Process {pid} no longer exists (stale lock)")
            except:
                pass

    except Exception as e:
        print(f"  ❌ Error reading lock file: {e}")

    return True

def check_logs():
    """Check recent logs"""
    print_section("6. LOG FILES CHECK")

    log_dir = Path.home() / '.local/share/cirqen/logs'

    if not log_dir.exists():
        print("  ℹ️  No logs directory yet")
        return True

    log_files = [
        'cirqen_app.log',
        'postgres.log',
        'postgres_init.log',
        'redis.log',
        'django.log',
        'celery.log',
        'sync_agent.log'
    ]

    for log_file in log_files:
        log_path = log_dir / log_file

        if log_path.exists():
            size = log_path.stat().st_size
            print(f"  ✅ {log_file} ({size:,} bytes)")

            # Show last few lines if it has errors
            try:
                with open(log_path, 'r') as f:
                    lines = f.readlines()

                    # Look for errors in last 50 lines
                    error_lines = [l for l in lines[-50:] if 'error' in l.lower() or 'failed' in l.lower()]

                    if error_lines:
                        print(f"     ⚠️  Found {len(error_lines)} error(s) in last 50 lines:")
                        for error_line in error_lines[-3:]:  # Show last 3 errors
                            print(f"        {error_line.strip()[:100]}")
            except:
                pass
        else:
            print(f"  ℹ️  {log_file} (not created yet)")

    return True

def check_permissions():
    """Check file permissions"""
    print_section("7. PERMISSIONS CHECK")

    data_path = Path.home() / '.local/share/cirqen'

    if not data_path.exists():
        print("  ℹ️  Data directory doesn't exist yet")
        return True

    # Check if we can write to data directory
    test_file = data_path / '.permission_test'
    try:
        test_file.write_text('test')
        test_file.unlink()
        print(f"  ✅ Can write to data directory")
    except Exception as e:
        print(f"  ❌ Cannot write to data directory: {e}")
        return False

    # Check postgres directory permissions
    pg_data = data_path / 'postgres'
    if pg_data.exists():
        import stat
        mode = pg_data.stat().st_mode
        print(f"  ℹ️  postgres/ permissions: {oct(stat.S_IMODE(mode))}")

    return True

def provide_recommendations():
    """Provide recommendations based on findings"""
    print_section("8. RECOMMENDATIONS")

    print("""
Based on the diagnostics above, here are the likely issues:

1. IF PostgreSQL binaries are missing:
   → Re-run the build script: python bulid_backup.py
   → Ensure runtime/postgresql/ is properly copied to dist/

2. IF share/ directory is missing:
   → This is CRITICAL - PostgreSQL cannot initialize without it
   → Check build_backup.py copy_system_postgresql() function
   → Ensure /usr/share/postgresql files are copied

3. IF ports are in use:
   → Run: python cleanup_cirqen.py
   → Or manually kill processes using the ports

4. IF another instance is running:
   → Close the other instance
   → Or run: python cleanup_cirqen.py

5. IF permissions issues:
   → Check that ~/.local/share/cirqen is writable
   → Run: chmod -R u+w ~/.local/share/cirqen

6. IF database not initialized:
   → Delete ~/.local/share/cirqen/postgres/
   → Restart Cirqen (it will re-initialize)

To get detailed PostgreSQL logs:
  tail -f ~/.local/share/cirqen/logs/postgres.log
  tail -f ~/.local/share/cirqen/logs/cirqen_app.log
    """)

def main():
    print("\n" + "="*70)
    print("  CIRQEN POSTGRESQL DIAGNOSTIC TOOL")
    print("="*70)
    print("\nThis tool will check why PostgreSQL won't start\n")

    checks = [
        check_data_directory,
        check_runtime_directory,
        check_ports,
        check_session_file,
        check_lock_file,
        check_logs,
        check_permissions,
    ]

    all_passed = True

    for check in checks:
        try:
            if not check():
                all_passed = False
        except Exception as e:
            print(f"❌ Check failed with error: {e}")
            import traceback
            traceback.print_exc()
            all_passed = False

    provide_recommendations()

    print("\n" + "="*70)
    if all_passed:
        print("✅ ALL CHECKS PASSED")
        print("\nIf PostgreSQL still won't start, check the detailed logs:")
        print("  ~/.local/share/cirqen/logs/postgres.log")
        print("  ~/.local/share/cirqen/logs/cirqen_app.log")
    else:
        print("⚠️  ISSUES FOUND - See recommendations above")
    print("="*70)
    print()

    return 0 if all_passed else 1

if __name__ == '__main__':
    sys.exit(main())
