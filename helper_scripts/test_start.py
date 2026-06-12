#!/usr/bin/env python3
import sys
import os
from pathlib import Path

print("1. Starting test...")

# Set up paths like the app does
BUNDLE_DIR = Path.cwd()
RUNTIME_DIR = BUNDLE_DIR / 'runtime'
print(f"2. RUNTIME_DIR: {RUNTIME_DIR}")

print("3. Importing time...")
import time
print("4. Time imported OK")

print("5. Importing psycopg2...")
import psycopg2
print("6. psycopg2 imported OK")

print("7. Checking PostgreSQL binary...")
pg_bin = RUNTIME_DIR / 'postgresql' / 'bin' / 'postgres'
print(f"8. PostgreSQL path: {pg_bin}")
print(f"9. Exists: {pg_bin.exists()}")

if pg_bin.exists():
    print("10. PostgreSQL binary found!")
    print(f"11. Size: {pg_bin.stat().st_size} bytes")

    print("12. Testing subprocess...")
    import subprocess
    result = subprocess.run([str(pg_bin), '--version'], capture_output=True, text=True, timeout=5)
    print(f"13. Version check result: {result.stdout}")
else:
    print("10. ERROR: PostgreSQL binary NOT found!")

print("14. Test complete!")
