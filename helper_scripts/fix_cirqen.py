#!/usr/bin/env python3
"""
Cirqen Emergency Fix Script
Resolves common PostgreSQL startup issues
"""

import os
import sys
import signal
import shutil
import subprocess
import psutil
from pathlib import Path
import time

def print_section(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def kill_postgres_processes():
    """Kill all PostgreSQL processes"""
    print_section("Step 1: Killing Existing PostgreSQL Processes")

    killed = 0
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if 'postgres' in proc.info['name'].lower():
                print(f"Killing process: PID {proc.info['pid']} - {proc.info['name']}")
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"Could not kill process: {e}")
        except Exception as e:
            print(f"Error: {e}")

    if killed == 0:
        print("✅ No PostgreSQL processes found")
    else:
        print(f"✅ Killed {killed} PostgreSQL processes")
        time.sleep(2)

def kill_cirqen_processes():
    """Kill all Cirqen processes"""
    print_section("Step 2: Killing Existing Cirqen Processes")

    killed = 0
    current_pid = os.getpid()

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Skip current process
            if proc.info['pid'] == current_pid:
                continue

            name = proc.info['name'].lower()
            cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ''

            if 'cirqen' in name or 'cirqen' in cmdline.lower():
                print(f"Killing process: PID {proc.info['pid']} - {proc.info['name']}")
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception as e:
            print(f"Error: {e}")

    if killed == 0:
        print("✅ No Cirqen processes found")
    else:
        print(f"✅ Killed {killed} Cirqen processes")
        time.sleep(2)

def remove_lock_files():
    """Remove PostgreSQL lock files"""
    print_section("Step 3: Removing Lock Files")

    data_path = Path.home() / '.local' / 'share' / 'cirqen'

    lock_files = [
        data_path / 'postgres' / 'data' / 'postmaster.pid',
        data_path / 'postgres' / 'data' / 'postmaster.opts',
        data_path / '.cirqen.lock',
        data_path / '.session.json',
    ]

    removed = 0
    for lock_file in lock_files:
        if lock_file.exists():
            print(f"Removing: {lock_file}")
            try:
                lock_file.unlink()
                removed += 1
                print(f"  ✅ Removed")
            except Exception as e:
                print(f"  ❌ Error: {e}")
        else:
            print(f"Not found: {lock_file}")

    if removed > 0:
        print(f"\n✅ Removed {removed} lock files")
    else:
        print("\n✅ No lock files to remove")

def clear_temp_files():
    """Clear temporary files"""
    print_section("Step 4: Clearing Temporary Files")

    data_path = Path.home() / '.local' / 'share' / 'cirqen'

    temp_dirs = [
        data_path / 'temp',
        data_path / 'cache',
    ]

    for temp_dir in temp_dirs:
        if temp_dir.exists():
            print(f"Clearing: {temp_dir}")
            try:
                shutil.rmtree(temp_dir)
                temp_dir.mkdir(exist_ok=True)
                print(f"  ✅ Cleared")
            except Exception as e:
                print(f"  ⚠️  Error: {e}")
        else:
            print(f"Not found: {temp_dir}")

def check_postgres_data():
    """Check if PostgreSQL data directory is valid"""
    print_section("Step 5: Checking PostgreSQL Data Directory")

    data_path = Path.home() / '.local' / 'share' / 'cirqen'
    postgres_data = data_path / 'postgres' / 'data'

    if not postgres_data.exists():
        print(f"⚠️  PostgreSQL data directory doesn't exist: {postgres_data}")
        print(f"   Database will be initialized on next start")
        return True

    print(f"✅ PostgreSQL data directory exists: {postgres_data}")

    # Check for critical files
    critical_files = ['PG_VERSION', 'postgresql.conf']

    all_good = True
    for filename in critical_files:
        file_path = postgres_data / filename
        if not file_path.exists():
            print(f"  ❌ Missing: {filename}")
            all_good = False
        else:
            print(f"  ✅ Found: {filename}")

    if not all_good:
        print("\n⚠️  PostgreSQL data directory appears corrupted")

        response = input("\nDo you want to reinitialize the database? This will DELETE all data! (yes/no): ")
        if response.lower() == 'yes':
            print("\nReinitializing database...")
            backup_dir = data_path / 'postgres' / 'data_backup'

            # Backup old data
            if postgres_data.exists():
                print(f"Backing up to: {backup_dir}")
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                shutil.move(str(postgres_data), str(backup_dir))

            print("✅ Old data backed up")
            print("   Database will be reinitialized on next start")
        else:
            print("Skipping database reinitialization")
    else:
        print("\n✅ PostgreSQL data directory looks good")

    return True

def verify_runtime_files():
    """Verify runtime files exist"""
    print_section("Step 6: Verifying Runtime Files")

    # Try to find the Cirqen directory
    possible_paths = [
        Path.cwd() / 'dist' / 'Cirqen',
        Path.cwd() / 'Cirqen',
        Path.cwd(),
    ]

    cirqen_dir = None
    for path in possible_paths:
        if path.exists() and (path / 'runtime').exists():
            cirqen_dir = path
            break

    if not cirqen_dir:
        print("⚠️  Could not find Cirqen/runtime directory")
        print("   Skipping runtime verification")
        return True

    print(f"✅ Found Cirqen directory: {cirqen_dir}")

    # Check PostgreSQL
    postgres_bin = cirqen_dir / 'runtime' / 'postgresql' / 'bin' / 'postgres'
    if postgres_bin.exists():
        print(f"✅ PostgreSQL binary found")
    else:
        print(f"❌ PostgreSQL binary missing: {postgres_bin}")
        print(f"   You may need to rebuild the application")
        return False

    return True

def main():
    print("=" * 70)
    print(" " * 15 + "CIRQEN EMERGENCY FIX SCRIPT")
    print("=" * 70)
    print("\nThis script will:")
    print("  1. Kill all PostgreSQL and Cirqen processes")
    print("  2. Remove lock files")
    print("  3. Clear temporary files")
    print("  4. Check database integrity")
    print("  5. Verify runtime files")

    response = input("\nDo you want to continue? (yes/no): ")
    if response.lower() != 'yes':
        print("Aborted.")
        return

    # Run all fixes
    try:
        kill_postgres_processes()
        kill_cirqen_processes()
        remove_lock_files()
        clear_temp_files()
        check_postgres_data()
        verify_runtime_files()
    except Exception as e:
        print(f"\n❌ Error during fix: {e}")
        import traceback
        traceback.print_exc()
        return

    # Final message
    print_section("FIX COMPLETE")
    print("""
✅ Emergency fixes applied!

Next steps:
1. Try starting Cirqen again:
   cd dist/Cirqen
   ./start_cirqen.sh

2. If it still doesn't work, check the logs at:
   ~/.local/share/cirqen/logs/

3. For manual debugging, you can try starting PostgreSQL directly:
   cd dist/Cirqen
   ./runtime/postgresql/bin/postgres -D ~/.local/share/cirqen/postgres/data -p 2215

4. Note: Port 5432 is in use on your system. This is likely a system PostgreSQL.
   Cirqen will use dynamic ports (starting from 5442) for the HQ database.
    """)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
