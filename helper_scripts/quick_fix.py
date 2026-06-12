#!/usr/bin/env python3
"""
Cirqen Quick Fix - Simple cleanup script
Use this for fast cleanup before starting Cirqen
"""

import os
import sys
import subprocess
from pathlib import Path

def run_command(cmd, description):
    """Run a shell command"""
    print(f"→ {description}...")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  ✅ Done")
        else:
            if result.stdout:
                print(f"  Output: {result.stdout.strip()}")
            if result.stderr and 'no process found' not in result.stderr.lower():
                print(f"  Warning: {result.stderr.strip()}")
    except Exception as e:
        print(f"  ⚠️  {e}")

def main():
    print("=" * 60)
    print("  CIRQEN QUICK FIX")
    print("=" * 60)

    # Kill processes
    print("\n1. Stopping processes:")
    run_command("pkill -9 postgres 2>/dev/null", "Killing PostgreSQL")
    run_command("pkill -9 redis-server 2>/dev/null", "Killing Redis")
    run_command("pkill -9 Cirqen 2>/dev/null", "Killing Cirqen")

    # Remove lock files
    print("\n2. Removing lock files:")
    data_path = Path.home() / '.local' / 'share' / 'cirqen'

    locks = [
        data_path / 'postgres' / 'data' / 'postmaster.pid',
        data_path / '.cirqen.lock',
        data_path / '.session.json',
    ]

    for lock in locks:
        if lock.exists():
            try:
                lock.unlink()
                print(f"  ✅ Removed: {lock.name}")
            except Exception as e:
                print(f"  ⚠️  {lock.name}: {e}")

    print("\n" + "=" * 60)
    print("✅ Quick fix complete!")
    print("\nYou can now start Cirqen:")
    print("  cd dist/Cirqen")
    print("  ./start_cirqen.sh")
    print("=" * 60)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAborted.")
    except Exception as e:
        print(f"\n❌ Error: {e}")
