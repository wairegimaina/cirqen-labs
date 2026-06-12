#!/usr/bin/env python3
"""
ENHANCED WITH CODE DIRECTORY UPDATE SYSTEM

Cirqen Desktop Complete Build System - Fixed Runtime Embedding
- FIXED: PostgreSQL/Redis now properly embedded in dist
- FIXED: Post-build copy ensures all runtime files are included
- Enhanced logging and verification
- Automatic Django app discovery
- Configuration management via config.py
"""

import sys
import os
import platform
import subprocess
import shutil
import urllib.request
import zipfile
import tarfile
import logging
from pathlib import Path
from datetime import datetime

# ============================
# Logging Setup
# ============================
def setup_logging():
    """Setup enhanced logging"""
    log_dir = Path(__file__).parent / 'build_logs'
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f'build_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

    logger = logging.getLogger(__name__)
    logger.info("="*70)
    logger.info("CIRQEN BUILD SYSTEM - Fixed Runtime Embedding Edition")
    logger.info(f"Log file: {log_file}")
    logger.info("="*70)

    return logger

logger = setup_logging()

# ============================
# Configuration
# ============================
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MAC = platform.system() == "Darwin"

PROJECT_ROOT = Path(__file__).parent.resolve()
RUNTIME_DIR = PROJECT_ROOT / "runtime"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
RESOURCES_DIR = PROJECT_ROOT / "resources"

logger.info(f"Project root: {PROJECT_ROOT}")
logger.info(f"Platform: {platform.system()} {platform.release()}")
logger.info(f"Python: {sys.version}")
logger.info(f"Architecture: {platform.machine()}")

# PostgreSQL Download URLs (multiple sources)
POSTGRES_SOURCES = {
    'windows': [
        ('EnterpriseDB', 'https://get.enterprisedb.com/postgresql/postgresql-16.1-1-windows-x64-binaries.zip'),
        ('PostgreSQL.org Mirror', 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1258649'),
    ],
    'linux': [
        ('PostgreSQL Official', 'https://ftp.postgresql.org/pub/binary/v16.1/linux/binaries/postgresql-16.1-linux-x64-binaries.tar.gz'),
        ('System Copy', 'system'),  # Fallback to system PostgreSQL
    ]
}

# Redis Download URLs
REDIS_SOURCES = {
    'windows': [
        ('Memurai (Redis Windows)', 'https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip'),
        ('Microsoft Archive', 'https://github.com/microsoftarchive/redis/releases/download/win-3.2.100/Redis-x64-3.2.100.zip'),
    ],
    'linux': [
        ('Redis.io', 'https://download.redis.io/releases/redis-7.2.3.tar.gz'),
        ('System Copy', 'system'),
    ]
}

# ============================
# Helper Functions
# ============================
def print_banner(text):
    """Print section banner"""
    logger.info("\n" + "=" * 70)
    logger.info(f"  {text}")
    logger.info("=" * 70)

def print_step(step_num, total, text):
    """Print build step"""
    logger.info(f"\n[{step_num}/{total}] {text}\n")

def run_cmd(cmd, cwd=None, description="", timeout=300):
    """Run command with detailed logging (timeout in seconds)"""
    logger.info(f"Running: {' '.join(str(c) for c in cmd)}")
    if cwd:
        logger.info(f"Working directory: {cwd}")
    if description:
        logger.info(f"Description: {description}")

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout  # Use parameter timeout (default 300s)
        )

        if result.stdout:
            logger.debug(f"stdout: {result.stdout[:500]}")
        if result.stderr:
            logger.debug(f"stderr: {result.stderr[:500]}")

        logger.info("✅ Command completed successfully")
        return True

    except subprocess.TimeoutExpired:
        logger.error(f"❌ Command timed out ({timeout} seconds = {timeout/60:.1f} minutes)")
        logger.error("💡 TIP: Increase timeout parameter for long-running commands")
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Command failed with exit code {e.returncode}")
        logger.error(f"stdout: {e.stdout}")
        logger.error(f"stderr: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return False

def download_file(url, destination, description=""):
    """Download file with progress and retry"""
    logger.info(f"📥 Downloading: {Path(url).name}")
    logger.info(f"From: {url}")
    logger.info(f"To: {destination}")
    if description:
        logger.info(f"Description: {description}")

    max_retries = 3

    for attempt in range(max_retries):
        try:
            # Add headers to avoid 403 errors
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            )

            logger.info(f"Attempt {attempt + 1}/{max_retries}...")

            with urllib.request.urlopen(req, timeout=60) as response:
                total_size = int(response.headers.get('content-length', 0))
                logger.info(f"File size: {total_size / (1024*1024):.1f} MB")

                with open(destination, 'wb') as f:
                    downloaded = 0
                    chunk_size = 8192

                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break

                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            if downloaded % (1024 * 1024) == 0:  # Log every MB
                                logger.info(f"Progress: {percent:.1f}% ({downloaded / (1024*1024):.1f} MB)")

            logger.info("✅ Download complete")
            return True

        except urllib.error.HTTPError as e:
            logger.warning(f"⚠️ HTTP Error {e.code}: {e.reason}")
            if attempt < max_retries - 1:
                logger.info("Retrying...")
                continue
        except urllib.error.URLError as e:
            logger.warning(f"⚠️ URL Error: {e.reason}")
            if attempt < max_retries - 1:
                logger.info("Retrying...")
                continue
        except Exception as e:
            logger.warning(f"⚠️ Download failed: {e}")
            if attempt < max_retries - 1:
                logger.info("Retrying...")
                continue

    logger.error("❌ Download failed after all retries")
    return False


def verify_utilities_in_dist():
    """
    Verify that all utility scripts are present in dist
    This is a safety check to ensure critical files weren't missed
    """
    print_banner("Verifying Utilities in Distribution")

    dist_dir = DIST_DIR / "Cirqen"

    required_utilities = {
        'cleanup_cirqen.py': 'Cleanup utility (CRITICAL)',
        'launch_cirqen.py': 'Launch script',
        'Start_Cirqen.bat' if IS_WINDOWS else 'start_cirqen.sh': 'Platform launcher',
    }

    all_present = True

    for util_file, description in required_utilities.items():
        util_path = dist_dir / util_file

        if util_path.exists():
            size = util_path.stat().st_size
            logger.info(f"✅ {util_file}")
            logger.info(f"   {description}")
            logger.info(f"   Size: {size:,} bytes")

            # Verify it's not empty
            if size < 100:
                logger.warning(f"⚠️  {util_file} seems too small ({size} bytes)")
                all_present = False
        else:
            logger.error(f"❌ MISSING: {util_file}")
            logger.error(f"   {description}")
            all_present = False

    if all_present:
        logger.info("✅ All utility scripts verified and present")
        return True
    else:
        logger.error("❌ Some utility scripts are missing or invalid")
        return False



def extract_archive(archive_path, extract_to):
    """Extract zip or tar with logging"""
    logger.info(f"📦 Extracting: {Path(archive_path).name}")
    logger.info(f"To: {extract_to}")

    try:
        if archive_path.suffix == '.zip':
            with zipfile.ZipFile(archive_path, 'r') as z:
                members = z.namelist()
                logger.info(f"Archive contains {len(members)} files")
                z.extractall(extract_to)
        else:
            with tarfile.open(archive_path, 'r:*') as t:
                members = t.getmembers()
                logger.info(f"Archive contains {len(members)} files")
                t.extractall(extract_to)

        logger.info("✅ Extraction complete")
        return True

    except Exception as e:
        logger.error(f"❌ Extraction failed: {e}")
        return False

def find_settings_py():
    """Find Django settings.py with multiple strategies"""
    logger.info("🔍 Searching for Django settings.py...")

    # Strategy 1: Common locations
    common_locations = [
        PROJECT_ROOT / 'Equiper' / 'settings.py',
        PROJECT_ROOT / 'config' / 'settings.py',
        PROJECT_ROOT / 'settings' / 'settings.py',
        PROJECT_ROOT / 'settings.py',
    ]

    for location in common_locations:
        if location.exists():
            logger.info(f"✅ Found settings.py at: {location.relative_to(PROJECT_ROOT)}")
            return location

    # Strategy 2: Search for any settings.py
    logger.info("Searching project tree for settings.py...")
    for settings_file in PROJECT_ROOT.rglob('settings.py'):
        # Exclude venv, build, dist directories
        if any(part in settings_file.parts for part in ['.venv', 'venv', 'build', 'dist', '__pycache__']):
            continue

        logger.info(f"✅ Found settings.py at: {settings_file.relative_to(PROJECT_ROOT)}")
        return settings_file

    # Strategy 3: Look for settings module
    logger.info("Checking for settings module...")
    for settings_dir in PROJECT_ROOT.iterdir():
        if settings_dir.is_dir() and not settings_dir.name.startswith('.'):
            settings_file = settings_dir / 'settings.py'
            if settings_file.exists():
                logger.info(f"✅ Found settings.py at: {settings_file.relative_to(PROJECT_ROOT)}")
                return settings_file

    logger.warning("⚠️ Django settings.py not found")
    logger.warning("This may cause issues during build")
    return None

def find_django_apps():
    """Automatically discover all Django apps with detailed logging"""
    logger.info("🔍 Discovering Django apps...")

    apps = []

    # Method 1: Check for directories with apps.py or models.py
    logger.info("Method 1: Scanning for app directories...")
    for item in PROJECT_ROOT.iterdir():
        if item.is_dir() and not item.name.startswith('.') and item.name not in ['build', 'dist', 'runtime', '__pycache__']:
            # Check if it's a Django app
            has_apps_py = (item / 'apps.py').exists()
            has_models_py = (item / 'models.py').exists()

            if has_apps_py or has_models_py:
                apps.append(item.name)
                logger.info(f"  ✓ Found app: {item.name} (apps.py={has_apps_py}, models.py={has_models_py})")

    # Method 2: Parse settings.py for INSTALLED_APPS
    logger.info("Method 2: Parsing settings.py for INSTALLED_APPS...")
    settings_file = find_settings_py()

    if settings_file:
        try:
            with open(settings_file, 'r') as f:
                content = f.read()

            if 'INSTALLED_APPS' in content:
                import re
                pattern = r"['\"]([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)['\"]"
                matches = re.findall(pattern, content)

                for match in matches:
                    # Check if it's a local app
                    if '.' not in match and match not in ['django', 'rest_framework', 'corsheaders', 'celery']:
                        app_dir = PROJECT_ROOT / match
                        if app_dir.exists() and match not in apps:
                            apps.append(match)
                            logger.info(f"  ✓ Found app from settings: {match}")
        except Exception as e:
            logger.warning(f"  ⚠️ Could not parse settings.py: {e}")

    # Remove duplicates and sort
    apps = sorted(list(set(apps)))

    logger.info(f"\n✅ Discovered {len(apps)} Django apps:")
    for app in apps:
        logger.info(f"     • {app}")

    if len(apps) == 0:
        logger.warning("⚠️ No Django apps found! This seems wrong.")

    return apps
def copy_system_postgresql():
    """
    Copy PostgreSQL from system installation (Linux) - COMPLETE FIXED VERSION
    Handles multiple PostgreSQL installation locations and ensures share/ is copied
    """
    logger.info("📄 Attempting to copy system PostgreSQL...")

    pg_dir = RUNTIME_DIR / "postgresql"
    pg_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # STEP 1: LOCATE POSTGRESQL INSTALLATION
    # ========================================================================
    logger.info("🔍 Searching for PostgreSQL installation...")

    possible_locations = [
        Path("/usr/lib/postgresql"),      # Debian/Ubuntu standard
        Path("/usr/pgsql-16"),             # RHEL/CentOS PostgreSQL 16
        Path("/usr/pgsql-15"),             # RHEL/CentOS PostgreSQL 15
        Path("/usr/pgsql-14"),             # RHEL/CentOS PostgreSQL 14
        Path("/opt/postgresql"),           # Custom installations
        Path("/usr/local/pgsql"),          # Source installations
    ]

    system_pg = None
    for location in possible_locations:
        if location.exists():
            logger.info(f"  ✓ Found PostgreSQL at: {location}")
            system_pg = location
            break
        else:
            logger.debug(f"  ✗ Not found: {location}")

    if not system_pg:
        logger.error("❌ System PostgreSQL not found in any standard location")
        logger.error("📋 Searched locations:")
        for loc in possible_locations:
            logger.error(f"    • {loc}")
        logger.error("\n💡 Install PostgreSQL with:")
        logger.error("    Ubuntu/Debian: sudo apt-get install postgresql postgresql-contrib")
        logger.error("    RHEL/CentOS:   sudo yum install postgresql-server")
        return False

    # ========================================================================
    # STEP 2: DETERMINE POSTGRESQL VERSION
    # ========================================================================
    if system_pg.name == "postgresql":
        # Debian/Ubuntu style: /usr/lib/postgresql/14/
        pg_versions = sorted([d for d in system_pg.iterdir() if d.is_dir()], reverse=True)
        if not pg_versions:
            logger.error(f"❌ No PostgreSQL versions found in {system_pg}")
            return False
        latest_pg = pg_versions[0]
        pg_version = latest_pg.name
    else:
        # RHEL/CentOS style: /usr/pgsql-16/
        latest_pg = system_pg
        pg_version = system_pg.name.replace("pgsql-", "").replace("/usr/", "")

    logger.info(f"📋 Using PostgreSQL version: {pg_version}")
    logger.info(f"📁 Installation path: {latest_pg}")

    # ========================================================================
    # STEP 3: CREATE TARGET DIRECTORY STRUCTURE
    # ========================================================================
    logger.info("📁 Creating runtime directory structure...")

    pg_bin = pg_dir / "bin"
    pg_lib = pg_dir / "lib"
    pg_share = pg_dir / "share"

    pg_bin.mkdir(parents=True, exist_ok=True)
    pg_lib.mkdir(parents=True, exist_ok=True)
    pg_share.mkdir(parents=True, exist_ok=True)

    logger.info(f"  ✓ Created: {pg_bin.relative_to(PROJECT_ROOT)}")
    logger.info(f"  ✓ Created: {pg_lib.relative_to(PROJECT_ROOT)}")
    logger.info(f"  ✓ Created: {pg_share.relative_to(PROJECT_ROOT)}")

    # ========================================================================
    # STEP 4: COPY BINARIES (bin/)
    # ========================================================================
    logger.info("\n📦 COPYING BINARIES...")

    src_bin = latest_pg / "bin"
    bin_count = 0
    bin_errors = []

    if not src_bin.exists():
        logger.error(f"❌ Binary directory not found: {src_bin}")
        return False

    logger.info(f"  Source: {src_bin}")
    logger.info(f"  Target: {pg_bin}")

    # Critical binaries that MUST exist
    critical_bins = ['postgres', 'initdb', 'pg_ctl', 'psql']
    found_critical = []

    for item in src_bin.iterdir():
        if item.is_file():
            dest = pg_bin / item.name
            try:
                shutil.copy2(item, dest)
                dest.chmod(0o755)  # Make executable
                bin_count += 1

                if item.name in critical_bins:
                    found_critical.append(item.name)
                    logger.info(f"    ✓ {item.name} (CRITICAL)")
                elif bin_count % 10 == 0:
                    logger.debug(f"    • Copied {bin_count} binaries...")

            except Exception as e:
                bin_errors.append(f"{item.name}: {e}")
                logger.warning(f"  ⚠️  Could not copy {item.name}: {e}")

    logger.info(f"✅ Copied {bin_count} binaries")

    # Verify critical binaries
    missing_critical = set(critical_bins) - set(found_critical)
    if missing_critical:
        logger.error(f"❌ Missing critical binaries: {missing_critical}")
        return False
    else:
        logger.info(f"  ✓ All critical binaries present: {', '.join(found_critical)}")

    if bin_errors:
        logger.warning(f"  ⚠️  {len(bin_errors)} files had copy errors")

    # ========================================================================
    # STEP 5: COPY LIBRARIES (lib/)
    # ========================================================================
    logger.info("\n📦 COPYING LIBRARIES...")

    src_lib = latest_pg / "lib"
    lib_count = 0
    lib_errors = []

    if src_lib.exists():
        logger.info(f"  Source: {src_lib}")
        logger.info(f"  Target: {pg_lib}")

        for item in src_lib.rglob("*"):
            if item.is_file():
                rel_path = item.relative_to(src_lib)
                dest = pg_lib / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)

                try:
                    shutil.copy2(item, dest)
                    lib_count += 1

                    if lib_count % 100 == 0:
                        logger.debug(f"    • Copied {lib_count} library files...")

                except Exception as e:
                    lib_errors.append(f"{rel_path}: {e}")
                    logger.debug(f"  ⚠️  Could not copy {rel_path}: {e}")

        logger.info(f"✅ Copied {lib_count} library files")

        if lib_errors:
            logger.warning(f"  ⚠️  {len(lib_errors)} files had copy errors")
    else:
        logger.warning(f"⚠️  Library directory not found: {src_lib}")
        logger.warning("  PostgreSQL may have runtime issues without libraries")

    # ========================================================================
    # STEP 6: COPY SHARE FILES (share/) - MOST CRITICAL!
    # ========================================================================
    logger.info("\n📦 COPYING SHARE FILES (CRITICAL FOR INITDB)...")

    share_count = 0
    share_errors = []
    share_copied = False

    # Try multiple share locations
    share_locations = [
        (latest_pg / "share", "PostgreSQL installation directory"),
        (Path("/usr/share/postgresql") / pg_version, f"System share for version {pg_version}"),
        (Path("/usr/share/postgresql-common"), "PostgreSQL common files"),
        (Path("/usr/share/pgsql"), "Alternative PostgreSQL share"),
    ]

    logger.info("  🔍 Searching for share files in multiple locations...")

    for src_share, description in share_locations:
        if src_share.exists():
            logger.info(f"\n  ✓ Found: {src_share}")
            logger.info(f"    Description: {description}")

            # Check if it has files
            test_files = list(src_share.rglob('*.sql'))
            if not test_files:
                logger.warning(f"    ⚠️  Directory exists but has no SQL files, skipping...")
                continue

            logger.info(f"    • Contains {len(test_files)} SQL files")
            logger.info(f"    • Copying all files from share/...")

            # Copy all files from this location
            location_count = 0
            for item in src_share.rglob("*"):
                if item.is_file():
                    rel_path = item.relative_to(src_share)
                    dest = pg_share / rel_path
                    dest.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        shutil.copy2(item, dest)
                        location_count += 1
                        share_count += 1

                        if location_count % 100 == 0:
                            logger.debug(f"      • Progress: {location_count} files...")

                    except Exception as e:
                        share_errors.append(f"{rel_path}: {e}")
                        logger.debug(f"      ⚠️  Could not copy {rel_path}: {e}")

            logger.info(f"    ✅ Copied {location_count} files from this location")
            share_copied = True

        else:
            logger.debug(f"  ✗ Not found: {src_share}")

    if share_copied:
        logger.info(f"\n✅ TOTAL: Copied {share_count} share files")
    else:
        logger.error("\n❌ CRITICAL ERROR: No share files found or copied!")
        logger.error("📋 Searched locations:")
        for src_share, description in share_locations:
            logger.error(f"    • {src_share}")

    if share_errors:
        logger.warning(f"  ⚠️  {len(share_errors)} files had copy errors")

    # ========================================================================
    # STEP 7: VERIFY CRITICAL SHARE FILES
    # ========================================================================
    logger.info("\n📋 VERIFYING CRITICAL SHARE FILES...")

    critical_share_files = [
        ("postgres.bki", "Database catalog initialization"),
        ("postgres.description", "System catalog descriptions"),
        ("system_views.sql", "System views definitions"),
    ]

    critical_share_dirs = [
        ("extension", "PostgreSQL extensions"),
        ("timezone", "Timezone data"),
        ("tsearch_data", "Full-text search data"),
    ]

    missing_files = []
    missing_dirs = []

    # Check files
    logger.info("  Critical files:")
    for filename, description in critical_share_files:
        found = list(pg_share.rglob(filename))
        if found:
            logger.info(f"    ✓ {filename} - {description}")
        else:
            # Only postgres.bki is truly critical
            if filename == "postgres.bki":
                logger.error(f"    ✗ {filename} - {description} (CRITICAL - MISSING)")
                missing_files.append(filename)
            else:
                logger.warning(f"    ⚠️  {filename} - {description} (optional, not found)")

    # Check directories
    logger.info("  Critical directories:")
    for dirname, description in critical_share_dirs:
        dir_path = pg_share / dirname
        if dir_path.exists():
            file_count = len(list(dir_path.rglob('*')))
            logger.info(f"    ✓ {dirname}/ - {description} ({file_count} items)")
        else:
            # Only extension/ is truly critical
            if dirname == "extension":
                logger.error(f"    ✗ {dirname}/ - {description} (CRITICAL - MISSING)")
                missing_dirs.append(dirname)
            else:
                logger.warning(f"    ⚠️  {dirname}/ - {description} (optional, not found)")

    # Count SQL files
    sql_files = list(pg_share.rglob('*.sql'))
    logger.info(f"  ✓ Found {len(sql_files)} SQL files total")

    # ========================================================================
    # STEP 8: FINAL VERIFICATION
    # ========================================================================
    logger.info("\n📋 FINAL VERIFICATION...")

    postgres_bin = pg_bin / "postgres"
    initdb_bin = pg_bin / "initdb"

    all_checks_passed = True

    # Check binaries
    if postgres_bin.exists():
        logger.info(f"  ✓ postgres binary: {postgres_bin.stat().st_size:,} bytes")
    else:
        logger.error("  ✗ postgres binary: MISSING")
        all_checks_passed = False

    if initdb_bin.exists():
        logger.info(f"  ✓ initdb binary: {initdb_bin.stat().st_size:,} bytes")
    else:
        logger.error("  ✗ initdb binary: MISSING")
        all_checks_passed = False

    # Check share files
    if not share_copied or share_count == 0:
        logger.error("  ✗ share/ directory: EMPTY OR NOT COPIED")
        logger.error("     WITHOUT SHARE FILES, INITDB WILL FAIL!")
        all_checks_passed = False
    else:
        logger.info(f"  ✓ share/ directory: {share_count} files")

    # Check libraries
    if lib_count > 0:
        logger.info(f"  ✓ lib/ directory: {lib_count} files")
    else:
        logger.warning(f"  ⚠️  lib/ directory: No files (may cause runtime issues)")

    # Final summary
    logger.info("\n" + "="*70)
    if all_checks_passed and not missing_files:
        logger.info("✅ PostgreSQL successfully copied from system")
        logger.info(f"📊 Summary:")
        logger.info(f"    • Binaries:  {bin_count} files")
        logger.info(f"    • Libraries: {lib_count} files")
        logger.info(f"    • Share:     {share_count} files (including {len(sql_files)} SQL files)")
        logger.info(f"    • Total:     {bin_count + lib_count + share_count} files")
        logger.info("="*70)
        return True
    else:
        logger.error("❌ PostgreSQL copy incomplete or has critical issues")
        logger.error("📋 Issues found:")

        if not postgres_bin.exists():
            logger.error("    • Missing postgres binary")
        if not initdb_bin.exists():
            logger.error("    • Missing initdb binary")
        if not share_copied or share_count == 0:
            logger.error("    • Share directory empty or not copied (CRITICAL!)")
        if missing_files:
            logger.error(f"    • Missing critical files: {', '.join(missing_files)}")
        if missing_dirs:
            logger.error(f"    • Missing critical directories: {', '.join(missing_dirs)}")

        # Only fail if truly critical items are missing
        if 'postgres.bki' in missing_files or 'extension' in missing_dirs:
            logger.error("\n💡 Troubleshooting:")
            logger.error("    1. Ensure PostgreSQL is fully installed:")
            logger.error("       sudo apt-get install postgresql postgresql-contrib")
            logger.error("    2. Check system PostgreSQL installation:")
            logger.error("       dpkg -L postgresql-14 | grep share")
            logger.error("    3. Verify share files exist:")
            logger.error(f"       ls -la /usr/share/postgresql/{pg_version}/")
            logger.error("="*70)
            return False
        else:
            logger.warning("\n⚠️  Some optional files missing, but PostgreSQL should work")
            logger.warning("    Missing items are not critical for basic operation")
            logger.info("="*70)
            logger.info("✅ PostgreSQL successfully copied (with minor warnings)")
            logger.info(f"📊 Summary:")
            logger.info(f"    • Binaries:  {bin_count} files")
            logger.info(f"    • Libraries: {lib_count} files")
            logger.info(f"    • Share:     {share_count} files (including {len(sql_files)} SQL files)")
            logger.info(f"    • Total:     {bin_count + lib_count + share_count} files")
            logger.info("="*70)
            return True

def fix_postgresql_run_permissions():
    """
    Fix /var/run/postgresql permissions on Linux so that the embedded
    PostgreSQL process (running as a normal user, not the system 'postgres'
    account) can create its lock file there.

    Root cause
    ----------
    Ubuntu (and most Debian-based distros) ships /var/run/postgresql owned
    by the 'postgres' system user with mode 2775.  When Cirqen's embedded
    PostgreSQL starts as a regular user it cannot write the lock file
      /var/run/postgresql/.s.PGSQL.<port>.lock
    and immediately exits with:
      FATAL: could not create lock file "...": Permission denied

    Fix applied
    -----------
    1. chmod 1777 /var/run/postgresql   (world-writable + sticky bit)
       ← takes effect immediately for this session.
    2. echo "d /var/run/postgresql 1777 root root -"
           > /etc/tmpfiles.d/postgresql.conf
       ← makes the fix survive reboots via systemd-tmpfiles.

    Both operations require root (sudo).  If sudo is unavailable or the
    user declines, a clear warning is printed and the build continues —
    the fix can be applied manually before running the app.
    """
    if not IS_LINUX:
        return True

    print_banner("Fixing /var/run/postgresql Permissions (Linux)")

    run_dir = Path("/var/run/postgresql")

    # ── Check current state ────────────────────────────────────────────────
    if run_dir.exists():
        current_mode = oct(run_dir.stat().st_mode)
        logger.info(f"  Current /var/run/postgresql mode: {current_mode}")

        # Already world-writable?  (mode & 0o002 != 0)
        if run_dir.stat().st_mode & 0o002:
            logger.info("  ✅ /var/run/postgresql is already world-writable — no fix needed")
            return True
    else:
        logger.warning("  ⚠️  /var/run/postgresql does not exist — PostgreSQL may create it")
        return True

    # ── Apply immediate fix ────────────────────────────────────────────────
    logger.info("  Applying chmod 1777 /var/run/postgresql …")
    chmod_ok = run_cmd(
        ["sudo", "chmod", "1777", str(run_dir)],
        description="Make /var/run/postgresql world-writable (sticky bit)"
    )

    if chmod_ok:
        logger.info("  ✅ chmod 1777 applied successfully")
    else:
        logger.warning("  ⚠️  chmod failed — sudo may not be available")
        logger.warning("     Run manually before starting the app:")
        logger.warning("       sudo chmod 1777 /var/run/postgresql")

    # ── Apply persistent fix via systemd-tmpfiles ──────────────────────────
    tmpfiles_conf = Path("/etc/tmpfiles.d/postgresql.conf")
    tmpfiles_line = "d /var/run/postgresql 1777 root root -\n"

    logger.info(f"  Writing systemd-tmpfiles rule → {tmpfiles_conf} …")

    write_ok = run_cmd(
        ["sudo", "bash", "-c",
         f"echo 'd /var/run/postgresql 1777 root root -' > {tmpfiles_conf}"],
        description="Persist /var/run/postgresql permissions across reboots"
    )

    if write_ok:
        logger.info("  ✅ Persistent fix written — permissions will survive reboots")
        logger.info(f"     Rule: {tmpfiles_line.strip()}")
        logger.info(f"     File: {tmpfiles_conf}")
    else:
        logger.warning("  ⚠️  Could not write tmpfiles rule — sudo may not be available")
        logger.warning("     To make the fix permanent, run once as root:")
        logger.warning(f"       sudo bash -c \"echo '{tmpfiles_line.strip()}'"
                       f" > {tmpfiles_conf}\"")

    # ── Verify ────────────────────────────────────────────────────────────
    if run_dir.exists():
        new_mode = oct(run_dir.stat().st_mode)
        logger.info(f"  Verified /var/run/postgresql mode after fix: {new_mode}")

    # Non-fatal — the app may still work if the user has other permissions
    return True


def setup_postgresql():
    """Setup PostgreSQL with multiple download sources"""
    print_banner("Setting up PostgreSQL")

    pg_dir = RUNTIME_DIR / "postgresql"

    # Check if already setup
    if IS_WINDOWS:
        postgres_exe = pg_dir / "bin" / "postgres.exe"
    else:
        postgres_exe = pg_dir / "bin" / "postgres"

    if postgres_exe.exists():
        logger.info("✅ PostgreSQL already setup")
        logger.info(f"Location: {postgres_exe}")
        return True

    pg_dir.mkdir(parents=True, exist_ok=True)

    # Try each source
    platform_key = 'windows' if IS_WINDOWS else 'linux'
    sources = POSTGRES_SOURCES.get(platform_key, [])

    for source_name, source_url in sources:
        logger.info(f"\n📥 Trying source: {source_name}")

        if source_url == 'system' and IS_LINUX:
            if copy_system_postgresql():
                # Fix /var/run/postgresql permissions so the embedded
                # PostgreSQL process (running as a normal user) can create
                # its lock file there without a "Permission denied" error.
                fix_postgresql_run_permissions()
                return True
            continue

        # Download
        if IS_WINDOWS:
            pg_archive = RUNTIME_DIR / "postgres.zip"
        else:
            pg_archive = RUNTIME_DIR / "postgres.tar.gz"

        if not download_file(source_url, pg_archive, source_name):
            logger.warning(f"⚠️ Failed to download from {source_name}")
            continue

        # Extract
        if not extract_archive(pg_archive, pg_dir):
            logger.warning(f"⚠️ Failed to extract from {source_name}")
            pg_archive.unlink(missing_ok=True)
            continue

        # Reorganize if needed
        pgsql = pg_dir / "pgsql"
        if pgsql.exists():
            logger.info("Reorganizing PostgreSQL directory structure...")
            for item in pgsql.iterdir():
                shutil.move(str(item), str(pg_dir))
            pgsql.rmdir()

        # Make binaries executable on Linux
        if IS_LINUX:
            pg_bin = pg_dir / "bin"
            if pg_bin.exists():
                logger.info("Making binaries executable...")
                for bin_file in pg_bin.iterdir():
                    if bin_file.is_file():
                        bin_file.chmod(0o755)

        # Clean up
        pg_archive.unlink(missing_ok=True)

        # Verify
        if postgres_exe.exists():
            logger.info(f"✅ PostgreSQL ready from {source_name}")
            logger.info(f"Location: {postgres_exe}")
            return True
        else:
            logger.warning(f"⚠️ PostgreSQL binary not found after extraction from {source_name}")

    logger.error("❌ Failed to setup PostgreSQL from all sources")
    return False

def setup_redis():
    """Setup Redis with multiple sources"""
    print_banner("Setting up Redis")

    redis_dir = RUNTIME_DIR / "redis"

    # Check if already setup
    if IS_WINDOWS:
        redis_exe = redis_dir / "redis-server.exe"
    else:
        redis_exe = redis_dir / "redis-server"

    if redis_exe.exists():
        logger.info("✅ Redis already setup")
        logger.info(f"Location: {redis_exe}")
        return True

    redis_dir.mkdir(parents=True, exist_ok=True)

    # Try system Redis first on Linux
    if IS_LINUX:
        system_redis = Path("/usr/bin/redis-server")
        if system_redis.exists():
            logger.info("Using system Redis")
            # Copy instead of symlink for better portability
            shutil.copy2(system_redis, redis_exe)
            redis_exe.chmod(0o755)
            logger.info("✅ Redis configured (system)")
            return True

    # Download Redis
    platform_key = 'windows' if IS_WINDOWS else 'linux'
    sources = REDIS_SOURCES.get(platform_key, [])

    for source_name, source_url in sources:
        if source_url == 'system':
            continue

        logger.info(f"\n📥 Trying source: {source_name}")

        if IS_WINDOWS:
            redis_archive = RUNTIME_DIR / "redis.zip"
        else:
            redis_archive = RUNTIME_DIR / "redis.tar.gz"

        if not download_file(source_url, redis_archive, source_name):
            logger.warning(f"⚠️ Failed to download from {source_name}")
            continue

        if not extract_archive(redis_archive, redis_dir):
            logger.warning(f"⚠️ Failed to extract from {source_name}")
            redis_archive.unlink(missing_ok=True)
            continue

        # Make executable on Linux
        if IS_LINUX and redis_exe.exists():
            redis_exe.chmod(0o755)

        redis_archive.unlink(missing_ok=True)

        if redis_exe.exists():
            logger.info(f"✅ Redis ready from {source_name}")
            logger.info(f"Location: {redis_exe}")
            return True

    logger.warning("⚠️ Redis setup incomplete")
    logger.warning("The application may have caching issues")
    return True  # Don't fail build

def check_requirements():
    """Check requirements with detailed logging"""
    print_banner("Checking Requirements")

    # Python version
    logger.info(f"Python version: {sys.version}")
    if sys.version_info < (3, 8):
        logger.error("❌ Python 3.8+ required")
        return False
    logger.info(f"✅ Python version OK: {sys.version.split()[0]}")

    # Check files
    required_files = ['manage.py', 'main.py', 'requirements.txt']
    optional_files = ['config.py', 'celery.py']

    logger.info("\nChecking required files:")
    for file in required_files:
        file_path = PROJECT_ROOT / file
        if not file_path.exists():
            logger.error(f"❌ Missing required file: {file}")
            return False
        logger.info(f"✅ Found: {file}")

    logger.info("\nChecking optional files:")
    for file in optional_files:
        if (PROJECT_ROOT / file).exists():
            logger.info(f"✅ Found: {file}")
        else:
            # Search in subdirectories
            found_files = list(PROJECT_ROOT.rglob(file))
            if found_files:
                logger.info(f"✅ Found: {found_files[0].relative_to(PROJECT_ROOT)}")
            else:
                logger.warning(f"⚠️ Optional file not found: {file}")

    # Check for settings.py
    settings = find_settings_py()
    if settings:
        logger.info(f"✅ Django settings.py: {settings.relative_to(PROJECT_ROOT)}")
    else:
        logger.warning("⚠️ Django settings.py not found")
        logger.warning("Build may fail if Django apps cannot be discovered")

    return True

def verify_requirements():
    """Verify all required packages with detailed logging"""
    print_banner("Verifying Requirements")

    required_packages = {
        'Django': 'django',
        'PySide6': 'PySide6',
        'PyInstaller': 'PyInstaller',
        'psycopg2': 'psycopg2',
        'Celery': 'celery',
        'Redis': 'redis',
        'Channels': 'channels',
        'DRF': 'rest_framework',
        'Plotly': 'plotly',
        'Pillow': 'PIL',
        'python-dotenv': 'dotenv',
    }

    missing = []
    installed = []

    for name, import_name in required_packages.items():
        try:
            module = __import__(import_name)
            version = getattr(module, '__version__', 'unknown')
            installed.append(name)
            logger.info(f"  ✓ {name} (version: {version})")
        except ImportError:
            missing.append(name)
            logger.error(f"  ✗ {name} - MISSING")

    logger.info(f"\n📊 Status: {len(installed)}/{len(required_packages)} packages installed")

    if missing:
        logger.warning(f"\n⚠️ Missing packages: {', '.join(missing)}")
        logger.info("These will be installed in the next step")
    else:
        logger.info("✅ All required packages installed")

    return True

def get_project_folders():
    """Get all important project folders to include in the build"""
    print("🔍 Discovering project folders...")

    folders = []

    # Essential folders
    essential = ['templates', 'static', 'staticfiles', 'media', 'signatures','locale', 'fixtures']

    for folder in essential:
        folder_path = PROJECT_ROOT / folder
        if folder_path.exists():
            folders.append(folder)
            print(f"  ✓ Found: {folder}")

    # Check for project package (usually cmms, config, etc.)
    for item in PROJECT_ROOT.iterdir():
        if item.is_dir() and (item / 'settings.py').exists():
            folders.append(item.name)
            print(f"  ✓ Found project package: {item.name}")

    print(f"\n✅ Found {len(folders)} project folders")
    return folders

def setup_config():
    """Setup configuration using config.py"""
    print_banner("Setting Up Configuration")

    try:
        # Import config module
        sys.path.insert(0, str(PROJECT_ROOT))
        from config import CirqenConfig

        # Create temporary data path for build
        temp_data = PROJECT_ROOT / 'build_temp_data'
        temp_data.mkdir(exist_ok=True)

        # Initialize config
        print("📋 Initializing configuration...")
        config = CirqenConfig(temp_data)

        # Print summary
        print(config.get_config_summary())

        # Validate
        is_valid, errors = config.validate_config()
        if not is_valid:
            print("⚠️ Configuration validation warnings:")
            for error in errors:
                print(f"  • {error}")

        # Export to .env for build process
        env_path = PROJECT_ROOT / '.env.build'
        config.export_to_env_file(env_path)
        print(f"✅ Configuration exported to {env_path}")

        # Clean up temp data
        shutil.rmtree(temp_data, ignore_errors=True)

        return True

    except Exception as e:
        print(f"❌ Configuration setup failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def create_resources():
    """Create resources directory with icon"""
    print_banner("Creating Resources")

    RESOURCES_DIR.mkdir(exist_ok=True)

    # Create icon using PIL
    try:
        from PIL import Image, ImageDraw, ImageFont

        # Create 256x256 icon
        img = Image.new('RGB', (256, 256), color='#1a1a2e')
        draw = ImageDraw.Draw(img)

        # Draw circle
        draw.ellipse([48, 48, 208, 208], outline='#e94560', width=8, fill='#16213e')

        # Draw text
        try:
            font = ImageFont.truetype("arial.ttf", 60)
        except:
            font = ImageFont.load_default()

        draw.text((128, 128), "C", font=font, fill='#e94560', anchor='mm')

        # Save
        if IS_WINDOWS:
            img.save(RESOURCES_DIR / 'icon.ico', format='ICO', sizes=[(256, 256)])
            print("✅ Created icon.ico")

        img.save(RESOURCES_DIR / 'icon.png', format='PNG')
        print("✅ Created icon.png")

    except ImportError:
        print("⚠️ PIL not installed, skipping icon creation")
        print("   Install with: pip install Pillow")

    return True

def install_dependencies():
    """Install dependencies"""
    print_banner("Installing Dependencies")

    # Upgrade pip first
    print("📦 Upgrading pip...")
    run_cmd([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])

    print("📦 Installing requirements from requirements.txt...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("⚠️ Some dependencies failed:")
        print(result.stderr)
        print("\nContinuing with available packages...")
    else:
        print("✅ All requirements installed successfully")

    # Ensure critical packages are installed
    critical_packages = [
        "pyinstaller>=6.3.0",
        "python-dotenv>=1.0.0",
        "PySide6>=6.6.0",
        "psycopg2-binary==2.9.7",
        "Django>=4.2,<5.0",
        "celery>=5.3",
        "redis>=5.0.0",
    ]

    print("\n📦 Verifying critical packages...")
    for package in critical_packages:
        pkg_name = package.split(">=")[0].split("==")[0]
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", pkg_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print(f"  ✓ {pkg_name} installed")
            else:
                print(f"  ⚠️ {pkg_name} missing, installing...")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", package],
                    capture_output=True
                )
        except Exception as e:
            print(f"  ⚠️ Could not verify {pkg_name}: {e}")

    print("\n✅ Dependencies installation complete")
    return True

def verify_build():
    """
    Comprehensive verification of built distribution
    Checks that all critical files are present and valid
    """
    print_banner("Verifying Built Distribution")

    dist_app = DIST_DIR / "Cirqen"

    if not dist_app.exists():
        logger.error("❌ Distribution directory not found!")
        return False

    logger.info(f"📂 Checking: {dist_app}")

    # ========================================================================
    # 1. VERIFY EXECUTABLE
    # ========================================================================
    logger.info("\n🔍 Verifying executable:")

    if IS_WINDOWS:
        exe_name = "Cirqen.exe"
    else:
        exe_name = "Cirqen"

    exe_path = dist_app / exe_name

    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        logger.info(f"  ✓ {exe_name} ({size_mb:.1f} MB)")
    else:
        logger.error(f"  ❌ {exe_name} NOT FOUND")
        return False

    # ========================================================================
    # 2. VERIFY RUNTIME DIRECTORIES
    # ========================================================================
    logger.info("\n🔍 Verifying runtime components:")

    runtime_dir = dist_app / "runtime"

    # PostgreSQL
    if IS_WINDOWS:
        pg_exe = runtime_dir / "postgresql" / "bin" / "postgres.exe"
    else:
        pg_exe = runtime_dir / "postgresql" / "bin" / "postgres"

    if pg_exe.exists():
        logger.info(f"  ✓ PostgreSQL: {pg_exe.name}")

        # Check share directory (critical!)
        pg_share = runtime_dir / "postgresql" / "share"
        if pg_share.exists():
            share_files = list(pg_share.rglob('*'))
            logger.info(f"    • share/ directory: {len(share_files)} files")
        else:
            logger.error(f"    ❌ share/ directory MISSING (CRITICAL!)")
            return False
    else:
        logger.error(f"  ❌ PostgreSQL NOT FOUND")
        return False

    # Redis
    if IS_WINDOWS:
        redis_exe = runtime_dir / "redis" / "redis-server.exe"
    else:
        redis_exe = runtime_dir / "redis" / "redis-server"

    if redis_exe.exists():
        logger.info(f"  ✓ Redis: {redis_exe.name}")
    else:
        logger.warning(f"  ⚠️  Redis not found (optional)")

    # ========================================================================
    # 3. VERIFY TEMPLATES
    # ========================================================================
    logger.info("\n🔍 Verifying templates:")

    template_locations = [
        dist_app / 'templates',
        dist_app / '_internal' / 'templates',
    ]

    # Also check in Django apps
    django_apps = find_django_apps()
    for app in django_apps:
        template_locations.append(dist_app / app / 'templates')
        template_locations.append(dist_app / '_internal' / app / 'templates')

    templates_found = False
    total_templates = 0

    for tmpl_dir in template_locations:
        if tmpl_dir.exists():
            html_files = list(tmpl_dir.rglob('*.html'))
            if html_files:
                templates_found = True
                total_templates += len(html_files)
                logger.info(f"  ✓ {tmpl_dir.relative_to(dist_app)}: {len(html_files)} templates")

    if templates_found:
        logger.info(f"  ✅ Total templates found: {total_templates}")
    else:
        logger.error(f"  ❌ NO TEMPLATES FOUND!")
        logger.error(f"     This will cause Django to fail!")
        return False

    # ========================================================================
    # 4. VERIFY STATIC FILES
    # ========================================================================
    logger.info("\n🔍 Verifying static files:")

    static_locations = [
        dist_app / 'static',
        dist_app / 'staticfiles',
        dist_app / '_internal' / 'static',
        dist_app / '_internal' / 'staticfiles',
    ]



    # Also check in Django apps
    for app in django_apps:
        static_locations.append(dist_app / app / 'static')
        static_locations.append(dist_app / '_internal' / app / 'static')

    static_found = False
    total_css = 0
    total_js = 0
    total_images = 0

    for static_dir in static_locations:
        if static_dir.exists():
            css = list(static_dir.rglob('*.css'))
            js = list(static_dir.rglob('*.js'))
            images = (list(static_dir.rglob('*.png')) +
                     list(static_dir.rglob('*.jpg')) +
                     list(static_dir.rglob('*.svg')))

            if css or js or images:
                static_found = True
                total_css += len(css)
                total_js += len(js)
                total_images += len(images)
                logger.info(f"  ✓ {static_dir.relative_to(dist_app)}")
                logger.info(f"    • CSS: {len(css)}, JS: {len(js)}, Images: {len(images)}")

    if static_found:
        logger.info(f"  ✅ Total static files:")
        logger.info(f"     • CSS: {total_css}")
        logger.info(f"     • JS: {total_js}")
        logger.info(f"     • Images: {total_images}")
    else:
        logger.warning(f"  ⚠️  No static files found")
        logger.warning(f"     Django may have styling issues")

    # ========================================================================
    # 5. VERIFY UTILITY SCRIPTS
    # ========================================================================
    logger.info("\n🔍 Verifying utility scripts:")

    utilities = [
        'cleanup_cirqen.py',
        'launch_cirqen.py',
        'Start_Cirqen.bat' if IS_WINDOWS else 'start_cirqen.sh',
    ]

    utils_ok = True
    for util in utilities:
        util_path = dist_app / util
        if util_path.exists():
            size = util_path.stat().st_size
            logger.info(f"  ✓ {util} ({size:,} bytes)")
        else:
            logger.error(f"  ❌ {util} MISSING")
            utils_ok = False

    # ========================================================================
    # 6. VERIFY DOCUMENTATION
    # ========================================================================
    logger.info("\n🔍 Verifying documentation:")

    docs = ['README.txt', 'QUICK_START.txt', 'BUILD_INFO.txt']
    for doc in docs:
        doc_path = dist_app / doc
        if doc_path.exists():
            logger.info(f"  ✓ {doc}")
        else:
            logger.warning(f"  ⚠️  {doc} missing")

    # ========================================================================
    # 7. FINAL SUMMARY
    # ========================================================================
    logger.info("\n" + "="*70)
    logger.info("VERIFICATION SUMMARY")
    logger.info("="*70)

    checks = {
        'Executable': exe_path.exists(),
        'PostgreSQL': pg_exe.exists(),
        'PostgreSQL share/': (runtime_dir / "postgresql" / "share").exists(),
        'Templates': templates_found,
        'Static files': static_found,
        'Utility scripts': utils_ok,
    }

    all_passed = all(checks.values())

    for check_name, passed in checks.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"  {status}: {check_name}")

    logger.info("="*70)

    if all_passed:
        logger.info("\n✅ ALL VERIFICATIONS PASSED")
        logger.info("   Build is ready for distribution!")
        return True
    else:
        logger.error("\n❌ SOME VERIFICATIONS FAILED")
        logger.error("   Please review errors above")
        return False

def collect_static():
    """Collect static files"""
    print_banner("Collecting Static Files")

    print("📦 Running collectstatic...")
    run_cmd([sys.executable, "manage.py", "collectstatic", "--noinput", "--clear"])

    print("✅ Static files collected")
    return True

def generate_spec():
    """
    FIXED: Generate comprehensive PyInstaller spec with templates & static
    Ensures ALL data files are properly collected and included
    """
    print_banner("Generating Complete Build Spec (FIXED - INCLUDES ALL DATA)")

    # Discover Django apps and project structure
    django_apps = find_django_apps()
    project_folders = get_project_folders()

    # Find main project package
    main_package = None
    for folder in PROJECT_ROOT.iterdir():
        if folder.is_dir() and (folder / '__init__.py').exists():
            if folder.name not in ['venv', 'env', '.venv', 'node_modules', 'build', 'dist', '__pycache__']:
                if (folder / 'settings.py').exists() or (folder / 'celery.py').exists():
                    main_package = folder.name
                    logger.info(f"  ✓ Found main package: {main_package}")
                    break

    logger.info(f"\n✅ Discovered {len(django_apps)} Django apps")
    logger.info(f"✅ Discovered {len(project_folders)} project folders")
    if main_package:
        logger.info(f"✅ Main package: {main_package}")

    # ========================================================================
    # CRITICAL: COLLECT ALL DATA FILES
    # ========================================================================

    logger.info("\n" + "="*70)
    logger.info("COLLECTING ALL DATA FILES (TEMPLATES, STATIC, ETC.)")
    logger.info("="*70)

    datas_collected = []

    # -----------------------------------------------------------------------
    # 1. CORE FILES (manage.py, config.py, etc.)
    # -----------------------------------------------------------------------
    logger.info("\n📋 Core files:")
    core_files = [
        'manage.py',
        'config.py',
        'django_runner.py',
        'main.py',             # Main application entry point
        'update_manager.py',   # UPDATE MANAGER - Non-blocking updates with reconnection
        'update_client.py'     # UPDATE CLIENT - Server communication
    ]
    for file in core_files:
        file_path = PROJECT_ROOT / file
        if file_path.exists():
            datas_collected.append((str(file), '.'))
            logger.info(f"  ✓ {file}")
        else:
            # Warn if update files are missing
            if 'update' in file:
                logger.warning(f"  ⚠️  {file} not found - update functionality may not work")

    # -----------------------------------------------------------------------
    # 2. DJANGO APPS (complete directories with templates & static)
    # -----------------------------------------------------------------------

    logger.info("\n📦 Sync modules:")
    sync_path = PROJECT_ROOT / 'sync'
    if sync_path.exists():
        datas_collected.append((str(sync_path), 'sync'))
        logger.info(f"  ✓ sync/")

        # Check what's inside
        has_mirror = (sync_path / 'mirror.py').exists()
        has_sync_agent = (sync_path / 'sync_agent.py').exists()
        has_soft_delete_handler = (sync_path / 'soft_delete_handler.py').exists()

        if has_mirror:
            logger.info(f"    • mirror.py ✓")
        if has_sync_agent:
            logger.info(f"    • sync_agent.py ✓")
        if has_soft_delete_handler:
            logger.info(f"    • soft_delete_handler.py ✓")


    logger.info("\n📦 Django apps:")
    for app in django_apps:
        app_path = PROJECT_ROOT / app
        if app_path.exists():
            datas_collected.append((str(app), app))
            logger.info(f"  ✓ {app}/")

            # Check what's inside
            has_templates = (app_path / 'templates').exists()
            has_static = (app_path / 'static').exists()
            has_migrations = (app_path / 'migrations').exists()

            if has_templates:
                logger.info(f"    • templates/")
            if has_static:
                logger.info(f"    • static/")
            if has_migrations:
                logger.info(f"    • migrations/")

    # -----------------------------------------------------------------------
    # 3. MAIN PROJECT PACKAGE
    # -----------------------------------------------------------------------
    if main_package:
        logger.info(f"\n📦 Main package:")
        package_path = PROJECT_ROOT / main_package
        if package_path.exists():
            datas_collected.append((str(main_package), main_package))
            logger.info(f"  ✓ {main_package}/")

    # -----------------------------------------------------------------------
    # 4. PROJECT-LEVEL TEMPLATES (CRITICAL!)
    # -----------------------------------------------------------------------
    logger.info("\n📄 Project-level templates:")

    possible_template_dirs = [
        PROJECT_ROOT / 'templates',
        PROJECT_ROOT / 'template',
    ]

    if main_package:
        possible_template_dirs.append(PROJECT_ROOT / main_package / 'templates')

    templates_found = False
    for tmpl_dir in possible_template_dirs:
        if tmpl_dir and tmpl_dir.exists():
            rel_path = tmpl_dir.relative_to(PROJECT_ROOT)
            datas_collected.append((str(tmpl_dir), str(rel_path)))

            # Count templates
            html_files = list(tmpl_dir.rglob('*.html'))
            logger.info(f"  ✓ {rel_path}/ ({len(html_files)} HTML files)")

            # Show subdirectories
            subdirs = [d.name for d in tmpl_dir.iterdir() if d.is_dir()]
            if subdirs:
                logger.info(f"    Subdirs: {', '.join(subdirs[:5])}")
                if len(subdirs) > 5:
                    logger.info(f"    ... and {len(subdirs) - 5} more")

            templates_found = True

    if not templates_found:
        logger.warning("  ⚠️  No project-level templates found")
        logger.warning("     (OK if templates are only in apps)")

    # -----------------------------------------------------------------------
    # 5. PROJECT-LEVEL STATIC FILES (CRITICAL!)
    # -----------------------------------------------------------------------
    logger.info("\n🎨 Project-level static files:")

    possible_static_dirs = [
        PROJECT_ROOT / 'static',
        PROJECT_ROOT / 'staticfiles',
        PROJECT_ROOT / 'assets',
    ]

    if main_package:
        possible_static_dirs.append(PROJECT_ROOT / main_package / 'static')

    static_found = False
    for static_dir in possible_static_dirs:
        if static_dir and static_dir.exists():
            rel_path = static_dir.relative_to(PROJECT_ROOT)
            datas_collected.append((str(rel_path), str(rel_path)))

            # Count files by type
            css_files = list(static_dir.rglob('*.css'))
            js_files = list(static_dir.rglob('*.js'))
            img_files = (list(static_dir.rglob('*.png')) +
                        list(static_dir.rglob('*.jpg')) +
                        list(static_dir.rglob('*.svg')))

            logger.info(f"  ✓ {rel_path}/")
            logger.info(f"    • {len(css_files)} CSS files")
            logger.info(f"    • {len(js_files)} JS files")
            logger.info(f"    • {len(img_files)} images")

            static_found = True

    if not static_found:
        logger.warning("  ⚠️  No project-level static files found")
        logger.warning("     (OK if static files are only in apps)")

    # -----------------------------------------------------------------------
    # 6. ADDITIONAL DATA DIRECTORIES
    # -----------------------------------------------------------------------
    logger.info("\n📁 Additional data directories:")

    extra_folders = ['locale', 'fixtures', 'media', 'docs']
    for folder in extra_folders:
        folder_path = PROJECT_ROOT / folder
        if folder_path.exists():
            datas_collected.append((str(folder), folder))
            item_count = len(list(folder_path.rglob('*')))
            logger.info(f"  ✓ {folder}/ ({item_count} items)")

    # -----------------------------------------------------------------------
    # 7. RESOURCES
    # -----------------------------------------------------------------------
    logger.info("\n🎨 Resources:")
    resources_dir = PROJECT_ROOT / 'resources'
    if resources_dir.exists():
        for item in resources_dir.rglob('*'):
            if item.is_file():
                rel_path = item.relative_to(PROJECT_ROOT)
                datas_collected.append((str(rel_path), str(rel_path.parent)))

        resource_count = len([x for x in datas_collected if 'resources' in x[0]])
        logger.info(f"  ✓ resources/ ({resource_count} files)")

    # ========================================================================
    # SUMMARY OF DATA COLLECTION
    # ========================================================================
    logger.info("\n" + "="*70)
    logger.info(f"📊 DATA COLLECTION SUMMARY")
    logger.info("="*70)
    logger.info(f"Total data entries: {len(datas_collected)}")
    logger.info("")

    # Count by category
    templates_count = sum(1 for x in datas_collected if 'template' in x[0].lower())
    static_count = sum(1 for x in datas_collected if 'static' in x[0].lower())
    apps_count = len(django_apps)

    logger.info(f"  • Django apps: {apps_count}")
    logger.info(f"  • Template entries: {templates_count}")
    logger.info(f"  • Static entries: {static_count}")
    logger.info(f"  • Other data: {len(datas_collected) - templates_count - static_count - apps_count}")

    # ========================================================================
    # HIDDEN IMPORTS (COMPREHENSIVE - COMPLETE VERSION)
    # ========================================================================
    hidden_imports = [
    # ===== DJANGO CORE =====
    'django',
    'django.contrib.admin',
    'django.contrib.admin.templatetags',
    'django.contrib.admin.templatetags.admin_list',
    'django.contrib.admin.templatetags.admin_modify',
    'django.contrib.admin.templatetags.admin_urls',
    'django.contrib.admin.templatetags.base',
    'django.contrib.admin.templatetags.log',
    'django.contrib.auth',
    'django.contrib.auth.backends',
    'django.contrib.auth.hashers',
    'django.contrib.auth.middleware',
    'django.contrib.auth.context_processors',
    'django.contrib.contenttypes',
    'django.contrib.contenttypes.forms',
    'django.contrib.sessions',
    'django.contrib.sessions.backends',
    'django.contrib.sessions.backends.db',
    'django.contrib.sessions.backends.cache',
    'django.contrib.sessions.backends.file',
    'django.contrib.sessions.backends.cached_db',
    'django.contrib.sessions.middleware',
    'django.contrib.messages',
    'django.contrib.messages.middleware',
    'django.contrib.messages.context_processors',
    'django.contrib.messages.storage',
    'django.contrib.messages.storage.base',
    'django.contrib.messages.storage.cookie',
    'django.contrib.messages.storage.fallback',
    'django.contrib.messages.storage.session',
    'django.contrib.staticfiles',
    'django.contrib.staticfiles.finders',
    'django.contrib.staticfiles.storage',
    'django.contrib.humanize',
    'django.core',
    'django.core.management',
    'django.core.management.commands',
    'django.core.management.commands.runserver',
    'django.core.management.commands.migrate',
    'django.core.management.commands.makemigrations',
    'django.core.management.commands.collectstatic',
    'django.core.management.commands.check',
    'django.core.management.commands.shell',
    'django.core.wsgi',
    'django.core.asgi',
    'django.core.handlers',
    'django.core.handlers.wsgi',
    'django.core.handlers.asgi',
    'django.core.cache',
    'django.core.cache.backends',
    'django.core.cache.backends.base',
    'django.core.cache.backends.db',
    'django.core.cache.backends.dummy',
    'django.core.cache.backends.filebased',
    'django.core.cache.backends.locmem',
    'django.core.cache.backends.memcached',
    'django.core.cache.backends.redis',
    'django.core.mail',
    'django.core.mail.backends',
    'django.core.mail.backends.smtp',
    'django.core.mail.backends.console',
    'django.core.mail.backends.filebased',
    'django.core.mail.backends.locmem',
    'django.core.mail.backends.dummy',
    'django.middleware',
    'django.middleware.cache',
    'django.middleware.clickjacking',
    'django.middleware.common',
    'django.middleware.csrf',
    'django.middleware.gzip',
    'django.middleware.http',
    'django.middleware.locale',
    'django.middleware.security',
    'django.template',
    'django.template.loaders',
    'django.template.loaders.filesystem',
    'django.template.loaders.app_directories',
    'django.template.loaders.cached',
    'django.template.backends',
    'django.template.backends.django',
    'django.template.backends.jinja2',
    'django.template.context_processors',
    'django.template.defaultfilters',
    'django.template.defaulttags',
    'django.template.response',
    'django.template.loader',
    'django.template.engine',
    'django.templatetags',
    'django.templatetags.i18n',
    'django.templatetags.l10n',
    'django.templatetags.tz',
    'django.templatetags.cache',
    'django.templatetags.static',
    'django.forms',
    'django.forms.fields',
    'django.forms.widgets',
    'django.forms.models',
    'django.forms.formsets',
    'django.forms.boundfield',
    'django.forms.utils',
    'django.forms.renderers',
    'django.shortcuts',
    'django.views',
    'django.views.generic',
    'django.views.generic.base',
    'django.views.generic.detail',
    'django.views.generic.list',
    'django.views.generic.edit',
    'django.views.generic.dates',
    'django.views.decorators',
    'django.views.decorators.cache',
    'django.views.decorators.csrf',
    'django.views.decorators.http',
    'django.urls',
    'django.urls.resolvers',
    'django.urls.converters',
    'django.urls.exceptions',
    'django.http',
    'django.http.request',
    'django.http.response',
    'django.http.multipartparser',
    'django.utils',
    'django.utils.functional',
    'django.utils.decorators',
    'django.utils.deprecation',
    'django.utils.encoding',
    'django.utils.html',
    'django.utils.http',
    'django.utils.timezone',
    'django.utils.translation',
    'django.utils.dateformat',
    'django.utils.text',
    'django.utils.safestring',
    'django.utils.datastructures',
    'django.utils.regex_helper',
    'django.conf',
    'django.conf.urls',
    'django.conf.urls.static',
    'psutil'

    # ===== SYNC MODULE =====
    'sync',
    'sync.sync_agent',
    'sync.mirror',
    'sync.soft_delete_handler',

    # ===== UPDATE SYSTEM =====
    'update_manager',       # Update manager with reconnection support
    'update_client',        # Update client for server communication

    # ===== DATABASE =====
    'django.db.backends.postgresql',
    'django.db.backends.postgresql.base',
    'django.db.backends.postgresql.features',
    'django.db.backends.postgresql.operations',
    'django.db.backends.postgresql.schema',
    'django.db.backends.postgresql.introspection',
    'django.db.backends.postgresql.creation',
    'django.db.backends.postgresql.client',
    'psycopg2',
    'psycopg2._psycopg',
    'psycopg2.extensions',
    'psycopg2.extras',
    'psycopg2.pool',
    'psycopg2.sql',
    'psycopg2.tz',
    'psycopg2.errorcodes',

    # ===== REST FRAMEWORK =====
    'rest_framework',
    'rest_framework.views',
    'rest_framework.viewsets',
    'rest_framework.serializers',
    'rest_framework.parsers',
    'rest_framework.parsers.json',
    'rest_framework.parsers.multipart',
    'rest_framework.parsers.formparser',
    'rest_framework.renderers',
    'rest_framework.renderers.json',
    'rest_framework.renderers.browsable',
    'rest_framework.renderers.template',
    'rest_framework.authentication',
    'rest_framework.permissions',
    'rest_framework.decorators',
    'rest_framework.response',
    'rest_framework.status',
    'rest_framework.fields',
    'rest_framework.relations',
    'rest_framework.exceptions',
    'rest_framework.filters',
    'rest_framework.pagination',
    'rest_framework.routers',
    'rest_framework.metadata',
    'rest_framework.schemas',
    'rest_framework.settings',
    'rest_framework.utils',

    # ===== CHANNELS & WEBSOCKETS =====
    'channels',
    'channels.layers',
    'channels.routing',
    'channels.consumer',
    'channels.generic',
    'channels.generic.websocket',
    'channels.generic.http',
    'channels.db',
    'channels.sessions',
    'channels.auth',
    'channels.middleware',
    'channels.security',
    'channels.handler',
    'channels_redis',
    'channels_redis.core',
    'channels_redis.pubsub',
    'daphne',
    'daphne.server',
    'daphne.cli',
    'daphne.endpoints',
    'daphne.http_protocol',
    'daphne.ws_protocol',

    # ===== REDIS & DJANGO-REDIS =====
    'redis',
    'redis.client',
    'redis.connection',
    'redis.exceptions',
    'redis.sentinel',
    'django_redis',
    'django_redis.cache',
    'django_redis.client',
    'django_redis.client.default',
    'django_redis.compressors',
    'django_redis.compressors.identity',
    'django_redis.compressors.zlib',
    'django_redis.serializers',
    'django_redis.serializers.json',
    'django_redis.serializers.msgpack',
    'django_redis.serializers.pickle',

    #========= networkx====
    'networkx',

    'pytz',
    # ===== CELERY COMPLETE STACK =====
    # CORE
    'celery',
    'celery.local',
    'celery.datastructures',
    'celery.five',
    'celery._state',
    'celery.exceptions',
    'celery.app',
    'celery.app.base',
    'celery.app.task',
    'celery.app.defaults',
    'celery.app.control',
    'celery.app.registry',
    'celery.app.routes',
    'celery.app.utils',
    'celery.app.annotations',
    'celery.app.autoretry',
    'celery.app.builtins',
    'celery.app.log',
    'celery.app.amqp',
    'celery.contrib.django',
    'celery.app.events',
    'sqlalchemy',
    # BACKENDS
    'celery.backends',
    'celery.backends.base',
    'celery.backends.redis',
    'celery.backends.database',
    'celery.backends.cache',
    'celery.backends.asynchronous',
    'celery.backends.filesystem',
    'celery.backends.rpc',

    # BIN
    'celery.bin',
    'celery.bin.base',
    'celery.bin.celery',
    'celery.bin.worker',
    'celery.bin.beat',
    'celery.bin.events',
    'celery.bin.control',

    # BOOTSTEPS
    'celery.bootsteps',

    # CANVAS
    'celery.canvas',

    # CONCURRENCY
    'celery.concurrency',
    'celery.concurrency.prefork',
    'celery.concurrency.solo',
    'celery.concurrency.base',
    'celery.concurrency.thread',

    # CONTRIB
    'celery.contrib',
    'celery.contrib.abortable',

    # EVENTS
    'celery.events',
    'celery.events.state',
    'celery.events.receiver',
    'celery.events.snapshot',

    # FIXUPS (CRITICAL FOR DJANGO)
    'celery.fixups',
    'celery.fixups.django',

    # LOADERS
    'celery.loaders',
    'celery.loaders.app',
    'celery.loaders.base',
    'celery.loaders.default',

    # PLATFORMS
    'celery.platforms',

    # RESULT
    'celery.result',

    # SCHEDULES
    'celery.schedules',

    # SECURITY
    'celery.security',
    'celery.security.certificate',
    'celery.security.key',
    'celery.security.serialization',

    # SIGNALS
    'celery.signals',

    # TASK
    'celery.task',
    'celery.task.base',
    'celery.task.trace',

    'aiohttp',
    # UTILS (WHERE ORIGINAL ERROR OCCURRED)
    'celery.utils',
    'celery.utils.abstract',
    'celery.utils.collections',
    'celery.utils.debug',
    'celery.utils.deprecated',
    'celery.utils.dispatch',
    'celery.utils.dispatch.saferef',
    'celery.utils.dispatch.signal',
    'celery.utils.encoding',
    'celery.utils.functional',
    'celery.utils.graph',
    'celery.utils.imports',
    'celery.utils.iso8601',
    'celery.utils.log',
    'celery.utils.nodenames',
    'celery.utils.objects',
    'celery.utils.saferepr',
    'celery.utils.serialization',
    'celery.utils.sysinfo',
    'celery.utils.term',
    'celery.utils.text',
    'celery.utils.threads',
    'celery.utils.time',
    'celery.contrib.django.task',


    # WORKER
    'celery.worker',
    'celery.worker.control',
    'celery.worker.consumer',
    'celery.worker.consumer.consumer',
    'celery.worker.consumer.connection',
    'celery.worker.consumer.mingle',
    'celery.worker.consumer.gossip',
    'celery.worker.consumer.heart',
    'celery.worker.consumer.tasks',
    'celery.worker.strategy',
    'celery.worker.state',
    'celery.worker.autoscale',
    'celery.worker.autoreload',
    'celery.worker.heartbeat',
    'celery.worker.loops',
    'celery.worker.pidbox',
    'celery.worker.request',
    'celery.worker.components',

    # ===== DJANGO-CELERY-BEAT =====
    'django_celery_beat',
    'django_celery_beat.admin',
    'django_celery_beat.apps',
    'django_celery_beat.clockedschedule',
    'django_celery_beat.managers',
    'django_celery_beat.migrations',
    'django_celery_beat.models',
    'django_celery_beat.schedulers',
    'django_celery_beat.utils',
    'django_celery_beat.tzcrontab',

    # ===== KOMBU (CELERY MESSAGING) =====
    'kombu',
    'kombu.common',
    'kombu.mixins',
    'kombu.simple',
    'kombu.clocks',
    'kombu.pidbox',
    'kombu.asynchronous',
    'kombu.asynchronous.hub',
    'kombu.asynchronous.semaphore',
    'kombu.asynchronous.timer',
    'kombu.transport',
    'kombu.transport.base',
    'kombu.transport.redis',
    'kombu.transport.pyamqp',
    'kombu.transport.memory',
    'kombu.transport.virtual',
    'kombu.transport.virtual.base',
    'kombu.transport.virtual.exchange',
    'kombu.serialization',
    'kombu.compression',
    'kombu.utils',
    'kombu.utils.encoding',
    'kombu.utils.json',
    'kombu.utils.objects',
    'kombu.utils.functional',
    'kombu.entity',
    'kombu.message',
    'kombu.connection',
    'kombu.resource',
    'kombu.abstract',
    'kombu.exceptions',
    'kombu.log',
    'kombu.pools',

    # ===== BILLIARD (CELERY MULTIPROCESSING) =====
    'billiard',
    'billiard.pool',
    'billiard.process',
    'billiard.context',
    'billiard.exceptions',
    'billiard.einfo',
    'billiard.connection',
    'billiard.forking',
    'billiard.queues',
    'billiard.synchronize',
    'billiard.util',

    # ===== AMQP =====
    'amqp',
    'amqp.connection',
    'amqp.channel',
    'amqp.exceptions',
    'amqp.method_framing',
    'amqp.serialization',
    'amqp.abstract_channel',
    'amqp.basic_message',
    'amqp.protocol',

    # ===== VINE (PROMISES) =====
    'vine',
    'vine.abstract',
    'vine.promises',
    'vine.synchronization',

    # ===== PYSIDE6 (QT) =====
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebChannel',
    'PySide6.QtNetwork',
    'PySide6.QtPrintSupport',

    # ===== PLOTLY & DASH =====
    'plotly',
    'plotly.graph_objs',
    'plotly.graph_objects',
    'plotly.express',
    'plotly.io',
    'plotly.subplots',
    'plotly.figure_factory',
    'plotly.colors',
    'plotly.data',
    'dash',
    'dash.dependencies',
    'dash.exceptions',
    'dash.development',
    'dash.resources',
    'dash_bootstrap_components',
    'dash_bootstrap_components.themes',
    'django_plotly_dash',
    'django_plotly_dash.apps',
    'django_plotly_dash.middleware',
    'django_plotly_dash.finders',
    'django_plotly_dash.models',
    'django_plotly_dash.views',
    'django_plotly_dash.util',
    'dpd_components',
    'django_plotly_dash.urls',

    # ===== NUMPY (CRITICAL FOR MATPLOTLIB & SCIENTIFIC COMPUTING) =====
    'numpy',
    'numpy.core',
    'numpy.core._multiarray_umath',
    'numpy.core.multiarray',
    'numpy.core.numeric',
    'numpy.core.umath',
    'numpy.core.defchararray',
    'numpy.core._dtype',
    'numpy.core._methods',
    'numpy.core.arrayprint',
    'numpy.core.fromnumeric',
    'numpy.core.shape_base',
    'numpy.core._internal',
    'numpy.core._string_helpers',
    'numpy.core._type_aliases',
    'numpy.core._ufunc_config',
    'numpy.core.numerictypes',
    'numpy.core.records',
    'numpy.core.memmap',
    'numpy.core.function_base',
    'numpy.core.machar',
    'numpy.core.getlimits',
    'numpy.core.einsumfunc',
    'numpy.fft',
    'numpy.fft.helper',
    'numpy.linalg',
    'numpy.linalg.linalg',
    'numpy.random',
    'numpy.random._common',
    'numpy.random._generator',
    'numpy.random._mt19937',
    'numpy.random._pcg64',
    'numpy.random._philox',
    'numpy.random._sfc64',
    'numpy.random.bit_generator',
    'numpy.random.mtrand',
    'numpy.lib',
    'numpy.lib.format',
    'numpy.lib.mixins',
    'numpy.lib.scimath',
    'numpy.lib.stride_tricks',
    'numpy.lib.npyio',
    'numpy.lib.financial',
    'numpy.lib.arraysetops',
    'numpy.lib.arrayterator',
    'numpy.lib.arraypad',
    'numpy.lib._iotools',
    'numpy.ma',
    'numpy.ma.core',
    'numpy.ma.extras',
    'numpy.matrixlib',
    'numpy.matrixlib.defmatrix',
    'numpy.polynomial',
    'numpy.polynomial.polynomial',
    'numpy.polynomial.chebyshev',
    'numpy.polynomial.legendre',
    'numpy.polynomial.hermite',
    'numpy.polynomial.hermite_e',
    'numpy.polynomial.laguerre',

    # ===== MATPLOTLIB =====
    'matplotlib',
    'matplotlib.pyplot',
    'matplotlib.backends',
    'matplotlib.backends.backend_agg',
    'matplotlib.backends.backend_pdf',
    'matplotlib.figure',
    'matplotlib.axes',
    'matplotlib.lines',
    'matplotlib.patches',
    'matplotlib.colors',
    'matplotlib.cm',
    'matplotlib.font_manager',
    'matplotlib.ticker',
    'matplotlib.legend',
    'matplotlib.artist',
    'matplotlib.cbook',
    'matplotlib.path',
    'matplotlib.transforms',
    'matplotlib.collections',
    'matplotlib.image',
    'matplotlib.text',
    'matplotlib.axis',
    'matplotlib.scale',
    'matplotlib.spines',

    # ===== OPENPYXL (EXCEL) =====
    'openpyxl',
    'openpyxl.styles',
    'openpyxl.utils',
    'openpyxl.worksheet',
    'openpyxl.workbook',
    'openpyxl.chart',
    'openpyxl.drawing',
    'openpyxl.formatting',
    'openpyxl.formula',
    'openpyxl.packaging',
    'openpyxl.reader',
    'openpyxl.writer',
    'openpyxl.xml',

    # ===== DOCX/DOCXTPL (WORD) =====
    'docxtpl',
    'docx',
    'docx.shared',
    'docx.oxml',
    'docx.parts',
    'docx.document',
    'docx.section',
    'docx.styles',
    'docx.table',
    'docx.text',
    'docx.enum',

    # ===== PDF =====
    'reportlab',
    'reportlab.pdfgen',
    'reportlab.pdfgen.canvas',
    'reportlab.lib',
    'reportlab.lib.pagesizes',
    'reportlab.lib.styles',
    'reportlab.lib.units',
    'reportlab.lib.colors',
    'reportlab.lib.enums',
    'reportlab.platypus',
    'reportlab.platypus.doctemplate',
    'reportlab.platypus.frames',
    'reportlab.platypus.paragraph',
    'reportlab.platypus.tables',
    'reportlab.platypus.flowables',
    'reportlab.graphics',
    'reportlab.graphics.shapes',
    'reportlab.graphics.charts',
    'PyPDF2',
    'PyPDF2.generic',
    'PyPDF2.pdf',
    'PyPDF2.filters',
    'PyPDF2.utils',

    # ===== IMAGE PROCESSING =====
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'PIL.ImageFont',
    'PIL.ImageFilter',
    'PIL.ImageEnhance',

    # ===== UTILITIES =====
    'qrcode',
    'qrcode.image',
    'qrcode.image.svg',
    'qrcode.image.pure',
    'qrcode.image.pil',
    'colorlog',
    'colorlog.formatter',
    'dateutil',
    'dateutil.parser',
    'dateutil.tz',
    'dateutil.relativedelta',
    'dateutil.rrule',
    'pytz',
    'requests',
    'requests.adapters',
    'requests.auth',
    'requests.cookies',
    'requests.exceptions',
    'requests.models',
    'requests.sessions',
    'urllib3',
    'urllib3.util',
    'urllib3.util.retry',
    'urllib3.util.ssl_',
    'urllib3.connection',
    'urllib3.connectionpool',
    'urllib3.poolmanager',
    'validators',
    'tqdm',
    'tqdm.auto',
    'cryptography',
    'cryptography.fernet',
    'cryptography.hazmat',
    'cryptography.hazmat.backends',
    'cryptography.hazmat.primitives',
    'shortuuid',

    # ===== CONFIG & ENV =====
    'dotenv',
    'environ',
    'decouple',

    # ===== CORS =====
    'corsheaders',
    'corsheaders.middleware',
    'corsheaders.conf',
    'corsheaders.defaults',
    'corsheaders.signals',

    # ===== DJANGO EXTENSIONS =====
    'django_extensions',

    # ===== DEBUG TOOLBAR (COMPLETE) =====
    'debug_toolbar',
    'debug_toolbar.middleware',
    'debug_toolbar.panels',
    'debug_toolbar.panels.alerts',
    'debug_toolbar.panels.cache',
    'debug_toolbar.panels.headers',
    'debug_toolbar.panels.history',
    'debug_toolbar.panels.logging',
    'debug_toolbar.panels.profiling',
    'debug_toolbar.panels.redirects',
    'debug_toolbar.panels.request',
    'debug_toolbar.panels.settings',
    'debug_toolbar.panels.signals',
    'debug_toolbar.panels.sql',
    'debug_toolbar.panels.staticfiles',
    'debug_toolbar.panels.templates',
    'debug_toolbar.panels.timer',
    'debug_toolbar.panels.versions',
    'debug_toolbar.panels.community',
    'debug_toolbar.toolbar',
    'debug_toolbar.utils',
    'debug_toolbar.forms',
    'debug_toolbar.decorators',
    'debug_toolbar.checks',
    'debug_toolbar.views',
    'debug_toolbar.models',
    'debug_toolbar.apps',
    'debug_toolbar.store',
    'debug_toolbar._stubs',

    # ===== CHARTJS =====
    'chartjs',

    # ===== KAFKA/REDPANDA =====
    'kafka',
    'kafka.errors',
    'kafka.producer',
    'kafka.producer.future',
    'kafka.consumer',
    'kafka.consumer.fetcher',
    'kafka.consumer.group_coordinator',
    'kafka.consumer.subscription_state',
    'kafka.admin',
    'kafka.cluster',
    'kafka.conn',
    'kafka.metrics',
    'kafka.partitioner',
    'kafka.protocol',
    'kafka.serializer',

    # ===== MULTIPROCESSING & ASYNC =====
    'multiprocessing',
    'multiprocessing.process',
    'multiprocessing.pool',
    'multiprocessing.managers',
    'multiprocessing.queues',
    'multiprocessing.synchronize',
    'multiprocessing.connection',
    'multiprocessing.context',
    'asyncio',
    'asyncio.events',
    'asyncio.tasks',
    'asyncio.futures',
    'asyncio.locks',
    'asyncio.queues',
    'asyncio.streams',
    'asyncio.subprocess',
    'concurrent',
    'concurrent.futures',
    'concurrent.futures.thread',
    'concurrent.futures.process',

    # ===== JINJA2 & TEMPLATING =====
    'jinja2',
    'jinja2.ext',
    'jinja2.filters',
    'jinja2.loaders',
    'jinja2.runtime',
    'jinja2.utils',

    # ===== STANDARD LIBRARY =====
    'uuid',
    'decimal',
    'datetime',
    'json',
    'pickle',
    'sqlite3',
    'ssl',
    'hashlib',
    'hmac',
    'signal',
    'logging',
    'logging.handlers',
    'email',
    'email.mime',
    'email.mime.text',
    'email.mime.multipart',
    'email.mime.base',
    'email.mime.image',
    'mimetypes',
    'tempfile',
    'shutil',
    'io',
    'os',
    'sys',
    'pathlib',
    'collections',
    'collections.abc',
    'itertools',
    'functools',
    're',
    'string',
    'copy',
    'base64',
    'binascii',
    'weakref',
    'types',
    'inspect',
    'traceback',
    'warnings',
    'contextlib',
    'threading',
    'queue',
    'atexit',
    'gc',
    'importlib',
    'importlib.metadata',
    'importlib.resources',
    'pkgutil',
    'modulefinder',
]


    # Add timezone support
    if sys.version_info < (3, 9):
        hidden_imports.append('backports.zoneinfo')
    else:
        hidden_imports.append('zoneinfo')

    # Add discovered Django apps
    for app in django_apps:
        hidden_imports.extend([
            f'{app}',
            f'{app}.apps',
            f'{app}.models',
            f'{app}.views',
            f'{app}.urls',
            f'{app}.admin',
        ])

    # Add main package
    if main_package:
        hidden_imports.extend([
            f'{main_package}',
            f'{main_package}.settings',
            f'{main_package}.urls',
            f'{main_package}.wsgi',
        ])

    # ========================================================================
    # GENERATE SPEC FILE CONTENT
    # ========================================================================

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
"""
Cirqen Desktop - Complete PyInstaller Spec (Auto-Generated)
INCLUDES: Django + Celery + PySide6 + All Dependencies + NumPy + Templates + Static
CRITICAL: Includes django_runner.py for Django subprocess
Generated by: build_cirqen.py
"""

import sys
from pathlib import Path

block_cipher = None

# Platform detection
IS_WINDOWS = sys.platform == 'win32'
IS_LINUX = sys.platform.startswith('linux')
IS_MAC = sys.platform == 'darwin'

# ============================================================================
# DATA FILES COLLECTION - COMPREHENSIVE
# ============================================================================

# All data files (templates, static, apps, resources)
datas = {repr(datas_collected)}

# ============================================================================
# HIDDEN IMPORTS - COMPLETE LIST
# ============================================================================

hiddenimports = {repr(hidden_imports)}

# ============================================================================
# PYINSTALLER CONFIGURATION
# ============================================================================

a = Analysis(
    ['main.py'],
    pathex=['{str(PROJECT_ROOT)}'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'scipy',
        'pandas',
        'jupyter',
        'notebook',
        'IPython',
        'test',
        'tests',
        'testing',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Cirqen',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Set to True for debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icon.ico' if IS_WINDOWS and Path('resources/icon.ico').exists() else
         'resources/icon.icns' if IS_MAC and Path('resources/icon.icns').exists() else
         'resources/icon.png' if Path('resources/icon.png').exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Cirqen',
)

# macOS App Bundle (optional)
if IS_MAC:
    app = BUNDLE(
        coll,
        name='Cirqen.app',
        icon='resources/icon.icns' if Path('resources/icon.icns').exists() else None,
        bundle_identifier='com.cirqen.desktop',
        info_plist={{
            'NSHighResolutionCapable': 'True',
            'LSBackgroundOnly': 'False',
        }},
    )

# ============================================================================
# BUILD SUMMARY
# ============================================================================

print("\\n" + "="*80)
print("BUILD CONFIGURATION SUMMARY")
print("="*80)
print(f"Platform: {{'Windows' if IS_WINDOWS else 'Linux' if IS_LINUX else 'macOS' if IS_MAC else 'Unknown'}}")
print(f"\\nDjango Apps: {len({repr(django_apps)})}")
{f'print(f"Main Package: {main_package}")' if main_package else ''}
print(f"Data Files: {{len(datas)}}")
print(f"Hidden Imports: {{len(hiddenimports)}}")
print("\\nCritical Components:")
print("  ✅ django_runner.py")
print("  ✅ Templates (project + apps)")
print("  ✅ Static files (project + apps)")
print("  ✅ NumPy (complete stack)")
print("  ✅ Matplotlib + NumPy")
print("  ✅ Celery complete stack")
print("  ✅ celery.fixups.django")
print("  ✅ Django + REST Framework")
print("  ✅ Channels + WebSockets")
print("  ✅ Redis + django-redis")
print("  ✅ PySide6 (Qt)")
print("  ✅ Plotly + Dash")
print("  ✅ PostgreSQL (psycopg2)")
print("  ✅ All discovered Django apps")
print("\\n" + "="*80)
'''

    # Write spec file
    spec_file = PROJECT_ROOT / "cirqen.spec"
    spec_file.write_text(spec_content)

    logger.info("\n" + "="*70)
    logger.info("✅ SPEC FILE GENERATED SUCCESSFULLY")
    logger.info("="*70)
    logger.info(f"📄 File: {spec_file}")
    logger.info(f"📊 Statistics:")
    logger.info(f"   • {len(datas_collected)} data file entries")
    logger.info(f"   • {len(hidden_imports)} hidden imports")
    logger.info(f"   • {len(django_apps)} Django apps")
    logger.info(f"\n✅ Templates: INCLUDED")
    logger.info(f"✅ Static files: INCLUDED")
    logger.info(f"✅ All apps: INCLUDED")
    logger.info("="*70)

    return True

def copy_runtime_to_dist():
    """
    CRITICAL FIX: Copy PostgreSQL and Redis runtime to dist after PyInstaller
    This ensures all binaries and libraries are properly included
    """
    print_banner("Copying Runtime Directories to Dist")

    dist_app = DIST_DIR / "Cirqen"
    dist_runtime = dist_app / "runtime"
    dist_runtime.mkdir(exist_ok=True)

    copied_items = []

    # Copy PostgreSQL
    src_pg = RUNTIME_DIR / "postgresql"
    if src_pg.exists():
        dest_pg = dist_runtime / "postgresql"
        logger.info(f"Copying PostgreSQL: {src_pg} -> {dest_pg}")

        if dest_pg.exists():
            logger.info("  Removing existing PostgreSQL directory...")
            shutil.rmtree(dest_pg)

        logger.info("  Copying files...")
        shutil.copytree(src_pg, dest_pg, symlinks=False)

        # Count files
        file_count = sum(1 for _ in dest_pg.rglob('*') if _.is_file())
        size_mb = sum(f.stat().st_size for f in dest_pg.rglob('*') if f.is_file()) / (1024*1024)

        logger.info(f"  ✅ Copied {file_count} files ({size_mb:.1f} MB)")
        copied_items.append(f"PostgreSQL ({file_count} files, {size_mb:.1f} MB)")

        # Make binaries executable on Linux
        if IS_LINUX:
            pg_bin = dest_pg / "bin"
            if pg_bin.exists():
                logger.info("  Making binaries executable...")
                for bin_file in pg_bin.rglob("*"):
                    if bin_file.is_file():
                        try:
                            bin_file.chmod(0o755)
                        except:
                            pass
    else:
        logger.warning("⚠️ PostgreSQL source directory not found!")
        logger.warning("  Application will NOT work without PostgreSQL!")

    # Copy Redis
    src_redis = RUNTIME_DIR / "redis"
    if src_redis.exists():
        dest_redis = dist_runtime / "redis"
        logger.info(f"Copying Redis: {src_redis} -> {dest_redis}")

        if dest_redis.exists():
            logger.info("  Removing existing Redis directory...")
            shutil.rmtree(dest_redis)

        logger.info("  Copying files...")
        shutil.copytree(src_redis, dest_redis, symlinks=False)

        # Count files
        file_count = sum(1 for _ in dest_redis.rglob('*') if _.is_file())
        size_mb = sum(f.stat().st_size for f in dest_redis.rglob('*') if f.is_file()) / (1024*1024)

        logger.info(f"  ✅ Copied {file_count} files ({size_mb:.1f} MB)")
        copied_items.append(f"Redis ({file_count} files, {size_mb:.1f} MB)")

        # Make binaries executable on Linux
        if IS_LINUX:
            for bin_file in dest_redis.rglob("*"):
                if bin_file.is_file() and 'redis' in bin_file.name:
                    try:
                        bin_file.chmod(0o755)
                    except:
                        pass
    else:
        logger.warning("⚠️ Redis source directory not found!")
        logger.warning("  Caching may not work properly!")

    # Verify
    print("\n📋 Runtime Copy Summary:")
    for item in copied_items:
        print(f"  ✅ {item}")

    # Final verification
    if IS_WINDOWS:
        pg_exe = dist_runtime / "postgresql" / "bin" / "postgres.exe"
        redis_exe = dist_runtime / "redis" / "redis-server.exe"
    else:
        pg_exe = dist_runtime / "postgresql" / "bin" / "postgres"
        redis_exe = dist_runtime / "redis" / "redis-server"

    pg_ok = pg_exe.exists()
    redis_ok = redis_exe.exists()

    print("\n📋 Runtime Verification:")
    print(f"  PostgreSQL: {'✅ OK' if pg_ok else '❌ MISSING'}")
    print(f"  Redis: {'✅ OK' if redis_ok else '⚠️ MISSING'}")

    if not pg_ok:
        logger.error("❌ CRITICAL: PostgreSQL not found in dist!")
        logger.error("   Application will NOT work!")
        return False

    print("\n✅ Runtime directories successfully copied to dist")
    return True

def build_executable():
    """Build with PyInstaller"""
    print_banner("Building Executable")

    # Clean
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)

    print("🔨 Building with PyInstaller (this takes several minutes)...")
    print("   ⏱️  Timeout: 30 minutes (for large Django projects)")
    print("   Please be patient...")

    # Use extended timeout for PyInstaller (30 minutes for large projects)
    if not run_cmd([sys.executable, "-m", "PyInstaller", "cirqen.spec", "--clean", "--noconfirm"], timeout=1800):
        print("❌ Build failed")
        print("\n💡 If build timed out, edit bulid_backup.py line ~2761:")
        print("   Change timeout=1800 to timeout=3600 (60 minutes)")
        return False

    dist_app = DIST_DIR / "Cirqen"
    if not dist_app.exists():
        print("❌ Build output not found")
        return False

    print(f"✅ Build complete: {dist_app}")

    # CRITICAL: Copy runtime directories after PyInstaller
    print("\n" + "="*70)
    print("IMPORTANT: Now copying runtime directories...")
    print("="*70)

    if not copy_runtime_to_dist():
        print("❌ Failed to copy runtime directories")
        return False

    return True

def create_launchers():
    """Create launcher scripts"""
    print_banner("Creating Launchers")

    dist_dir = DIST_DIR / "Cirqen"

    # Python launcher
    launcher_content = '''#!/usr/bin/env python3
"""Cirqen Desktop Launcher"""
import subprocess
import sys
import os
from pathlib import Path

script_dir = Path(__file__).parent
exe = script_dir / ("Cirqen.exe" if sys.platform == "win32" else "Cirqen")

if not exe.exists():
    print(f"❌ Error: {exe} not found!")
    input("Press Enter to exit...")
    sys.exit(1)

# Inherit env from start_cirqen.sh (GPU flags already set),
# and ensure they are present even if called directly.
env = os.environ.copy()
if sys.platform != "win32":
    env.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--no-sandbox --disable-gpu --disable-software-rasterizer "
        "--disable-gpu-sandbox --single-process"
    )
    env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    env.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.webenginecontext.info=false")

print("🚀 Starting Cirqen...")
subprocess.run([str(exe)], env=env)
'''
    (dist_dir / "launch_cirqen.py").write_text(launcher_content)
    print("✅ Created launch_cirqen.py")

    if IS_WINDOWS:
        launcher = dist_dir / "Start_Cirqen.bat"
        launcher.write_text('''@echo off
echo Starting Cirqen Desktop Application...
python launch_cirqen.py
if errorlevel 1 (
    echo.
    echo Failed to start Cirqen!
    pause
)
''')
        print("✅ Created Start_Cirqen.bat")

    elif IS_LINUX:
        launcher = dist_dir / "start_cirqen.sh"
        launcher.write_text('''\
#!/bin/bash
# ============================================================
# Cirqen Desktop Launcher Script
# Fixed for Linux (Ubuntu/Debian) - QtWebEngine + PostgreSQL fix
# ============================================================
cd "$(dirname "$0")"

APP_NAME="Cirqen"
SETUP_FLAG="$HOME/.local/share/cirqen/.pg_perms_ok"
PG_RUN_DIR="/var/run/postgresql"

# ── PostgreSQL run-directory permission fix ──────────────────
# /var/run/postgresql is owned by the system 'postgres' user (mode 2775).
# Our embedded PostgreSQL runs as the logged-in user and cannot create
# its lock file there, causing an immediate FATAL crash.
# We fix this once per machine; the flag file records success so we
# never ask for a password again.
fix_pg_permissions() {
    # Already fixed on this machine?
    if [ -f "$SETUP_FLAG" ]; then
        return 0
    fi

    # Check whether the fix is actually needed
    if [ -w "$PG_RUN_DIR" ]; then
        mkdir -p "$(dirname "$SETUP_FLAG")"
        touch "$SETUP_FLAG"
        return 0
    fi

    echo ""
    echo "┌──────────────────────────────────────────────────────┐"
    echo "│  Cirqen — One-time setup (requires administrator)    │"
    echo "│                                                       │"
    echo "│  Cirqen needs a small system tweak so its database   │"
    echo "│  can start correctly. This only happens once.        │"
    echo "└──────────────────────────────────────────────────────┘"
    echo ""

    # Helper script we'll run as root — written to a temp file so
    # pkexec can reference it without quoting nightmares.
    HELPER=$(mktemp /tmp/cirqen_setup_XXXXXX.sh)
    cat > "$HELPER" <<'HELPER_EOF'
#!/bin/bash
chmod 1777 /var/run/postgresql
echo "d /var/run/postgresql 1777 root root -" > /etc/tmpfiles.d/cirqen-postgresql.conf
HELPER_EOF
    chmod +x "$HELPER"

    # Try pkexec first (shows a GUI password dialog — friendly for non-technical users).
    # Fall back to sudo (terminal prompt) if pkexec is unavailable.
    if command -v pkexec &>/dev/null; then
        pkexec bash "$HELPER"
        RESULT=$?
    elif command -v sudo &>/dev/null; then
        echo "Please enter your system password to complete setup:"
        sudo bash "$HELPER"
        RESULT=$?
    else
        echo "❌  Could not apply setup — neither pkexec nor sudo found."
        echo "    Ask your system administrator to run:"
        echo "      sudo chmod 1777 /var/run/postgresql"
        rm -f "$HELPER"
        return 1
    fi

    rm -f "$HELPER"

    if [ $RESULT -eq 0 ]; then
        mkdir -p "$(dirname "$SETUP_FLAG")"
        touch "$SETUP_FLAG"
        echo "✅  Setup complete — you will not be asked again."
    else
        echo "⚠️   Setup was cancelled or failed. Cirqen may not start correctly."
        echo "    If it fails, run install.sh once as administrator."
    fi
}

# ── Qt / Chromium sandbox fix ────────────────────────────────
export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-software-rasterizer --disable-gpu-sandbox --single-process"
export QTWEBENGINE_DISABLE_SANDBOX=1
export QT_LOGGING_RULES="*.debug=false;qt.webenginecontext.info=false"

# ── Run setup if needed, then launch ────────────────────────
fix_pg_permissions
echo "🚀  Starting Cirqen ${APP_NAME}..."
python3 launch_cirqen.py
''')
        launcher.chmod(0o755)
        print("✅ Created start_cirqen.sh")

        # ── One-time install helper (clients run this once after extracting) ──
        install_sh = dist_dir / "install.sh"
        install_sh.write_text('''\
#!/bin/bash
# ============================================================
# Cirqen — First-time installation helper
# Run this ONCE after extracting Cirqen. It makes one small
# system change so the built-in database can start correctly.
# ============================================================

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║        Cirqen — First-time Setup                ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "This script will:"
echo "  1. Allow Cirqen's database to start without errors"
echo "  2. Make the change permanent (survives reboots)"
echo "  3. Create a desktop shortcut for Cirqen"
echo ""
echo "You will be asked for your system password once."
echo ""
read -rp "Press ENTER to continue, or Ctrl+C to cancel..."
echo ""

# ── Fix PostgreSQL run directory permissions ─────────────
echo "→  Configuring database permissions..."
sudo chmod 1777 /var/run/postgresql
echo "d /var/run/postgresql 1777 root root -" | sudo tee /etc/tmpfiles.d/cirqen-postgresql.conf > /dev/null

if [ $? -eq 0 ]; then
    echo "   ✅  Database permissions configured."
else
    echo "   ❌  Failed. Please contact Cirqen support."
    exit 1
fi

# ── Mark as done so start_cirqen.sh never asks again ────
SETUP_FLAG="$HOME/.local/share/cirqen/.pg_perms_ok"
mkdir -p "$(dirname "$SETUP_FLAG")"
touch "$SETUP_FLAG"

# ── Make launcher executable ─────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$SCRIPT_DIR/start_cirqen.sh"

# ── Optional: install desktop shortcut ──────────────────
DESKTOP_FILE="$HOME/Desktop/Cirqen.desktop"
cat > "$DESKTOP_FILE" <<DESK
[Desktop Entry]
Version=1.0
Type=Application
Name=Cirqen
Comment=Calibration and Maintenance Management System
Exec=$SCRIPT_DIR/start_cirqen.sh
Icon=$SCRIPT_DIR/resources/icon.png
Terminal=false
Categories=Office;Database;
DESK
chmod +x "$DESKTOP_FILE"
gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null  # Mark trusted on GNOME

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   ✅  Cirqen is ready!                          ║"
echo "║                                                  ║"
echo "║   To start: double-click the Cirqen icon        ║"
echo "║             on your Desktop, or run:             ║"
echo "║             ./start_cirqen.sh                   ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
''')
        install_sh.chmod(0o755)
        print("✅ Created install.sh")

        # Desktop file (for developer use / direct Exec path)
        desktop = dist_dir / "Cirqen.desktop"
        desktop.write_text(f'''[Desktop Entry]
Version=1.0
Type=Application
Name=Cirqen
Comment=Calibration & Maintenance Management System
Exec={dist_dir.absolute()}/start_cirqen.sh
Icon={dist_dir.absolute()}/resources/icon.png
Terminal=false
Categories=Office;Database;
''')
        desktop.chmod(0o755)
        print("✅ Created Cirqen.desktop")

    return True

def create_readme():
    """Create documentation"""
    print_banner("Creating Documentation")

    dist_dir = DIST_DIR / "Cirqen"

    # Determine platform-specific text
    platform_name = platform.system()
    if IS_WINDOWS:
        start_instruction = "Double-click: Start_Cirqen.bat"
        data_location = "%APPDATA%\\cirqen\\"
    else:
        start_instruction = "Run: ./start_cirqen.sh"
        data_location = "~/.local/share/cirqen/"

    readme = f'''
╔══════════════════════════════════════════════════════════════════════╗
║              CIRQEN DESKTOP APPLICATION                              ║
║         Calibration & Maintenance Management System                  ║
╚══════════════════════════════════════════════════════════════════════╝

Version: 1.0.0
Platform: {platform_name}
Built: Auto-configured with all Django apps

══════════════════════════════════════════════════════════════════════

📋 WHAT'S INCLUDED

✅ Embedded PostgreSQL database
✅ Embedded Redis server
✅ Django web application (all apps auto-detected)
✅ Celery background worker
✅ Sync agent for HQ connection
✅ Desktop UI (PySide6)
✅ Configuration manager (config.py)

══════════════════════════════════════════════════════════════════════

🚀 GETTING STARTED

{platform_name}:
  1. {start_instruction}
  2. Wait for services to start (~10-15 seconds)
  3. Application window opens automatically
  4. Login with HOD credentials

🔐 DEFAULT LOGIN (First Run):
  Username: maina.wairegi
  Email: mosemaina5@gmail.com
  Password: ChangeMe123!
  ⚠️  CHANGE PASSWORD ON FIRST LOGIN!

══════════════════════════════════════════════════════════════════════

📁 DATA STORAGE

{data_location}

Contents:
  • postgres/    → Database files
  • redis/       → Cache files
  • logs/        → Application logs
  • media/       → Uploaded files
  • sync_state/  → Sync tracking
  • config.json  → Configuration (auto-generated)

══════════════════════════════════════════════════════════════════════

⚙️  CONFIGURATION

Configuration is managed by config.py and stored in config.json

To modify settings:
  1. Edit config.json in the data directory, OR
  2. Create a .env file in the application directory

Key settings:
  • Sync intervals and behavior
  • Database connections (local & HQ)
  • Redis configuration
  • Mirror system settings
  • Redpanda/Kafka CDC

══════════════════════════════════════════════════════════════════════

🌐 ONLINE/OFFLINE MODE

✅ ONLINE:  Connected to HQ - automatic sync
❌ OFFLINE: Local only - syncs when connected

The application automatically detects connection status and
adjusts behavior accordingly.

══════════════════════════════════════════════════════════════════════

📋 LOGS

Check logs if issues occur:
  • logs/postgres.log      → Database
  • logs/redis.log         → Cache
  • logs/django.log        → Web server
  • logs/celery.log        → Background tasks
  • logs/sync_agent.log    → Synchronization

══════════════════════════════════════════════════════════════════════

🔧 TROUBLESHOOTING

1. PostgreSQL won't start:
   → Check logs/postgres.log
   → Ensure port 5432 is free
   → Delete postgres/ folder to reset

2. Redis won't start:
   → Check logs/redis.log
   → Ensure port 6379 is free

3. Can't connect to HQ:
   → Check internet connection
   → Verify firewall settings
   → Check config.json HQ settings

4. Configuration issues:
   → Review config.json in data directory
   → Check .env file if present
   → Validate settings using config.py

══════════════════════════════════════════════════════════════════════

📞 SUPPORT

For help, contact:
  Email: support@b12technologies.com
  Phone: +254-XXX-XXX-XXX

══════════════════════════════════════════════════════════════════════

© 2024 B12 Technologies - All Rights Reserved
'''

    (dist_dir / "README.txt").write_text(readme)
    print("✅ Created README.txt")

    # Quick start guide
    if IS_WINDOWS:
        quickstart_start = "Double-click: Start_Cirqen.bat"
    else:
        quickstart_start = "Run: ./start_cirqen.sh"

    quickstart = f'''
╔══════════════════════════════════════════════════════════════════════╗
║                        QUICK START GUIDE                             ║
╚══════════════════════════════════════════════════════════════════════╝

1️⃣  START
   {quickstart_start}

2️⃣  WAIT
   Services start in 10-15 seconds
   You'll see a splash screen with progress

3️⃣  LOGIN
   First run creates HOD user automatically:
   • Username: maina.wairegi
   • Password: ChangeMe123!
   ⚠️  Change password immediately!

4️⃣  WORK
   Application works online or offline
   Check status indicator in top bar

That's it! 🎉

══════════════════════════════════════════════════════════════════════

💡 TIPS

• All Django apps are automatically included
• Configuration auto-generated on first run
• Data persists between sessions
• Syncs automatically when online

══════════════════════════════════════════════════════════════════════
'''

    (dist_dir / "QUICK_START.txt").write_text(quickstart)
    print("✅ Created QUICK_START.txt")

    return True

def package_distribution():
    """Create distribution archive"""
    print_banner("Packaging Distribution")

    dist_dir = DIST_DIR / "Cirqen"

    # Calculate size
    total_size = sum(f.stat().st_size for f in dist_dir.rglob('*') if f.is_file())
    print(f"📊 Total size: {total_size / (1024*1024):.1f} MB")

    # Create archive
    platform_name = "windows" if IS_WINDOWS else "linux"
    archive_name = f"Cirqen_{platform_name}_v1.0.0"

    print(f"📦 Creating archive: {archive_name}...")

    if IS_WINDOWS:
        shutil.make_archive(str(DIST_DIR / archive_name), 'zip', DIST_DIR, 'Cirqen')
        archive_file = DIST_DIR / f"{archive_name}.zip"
    else:
        shutil.make_archive(str(DIST_DIR / archive_name), 'gztar', DIST_DIR, 'Cirqen')
        archive_file = DIST_DIR / f"{archive_name}.tar.gz"

    archive_size = archive_file.stat().st_size / (1024*1024)
    print(f"✅ Created: {archive_file.name}")
    print(f"📊 Archive size: {archive_size:.1f} MB")

    return True

def create_build_info():
    """Create build information file"""
    print_banner("Creating Build Info")

    dist_dir = DIST_DIR / "Cirqen"

    # Discover what was included
    django_apps = find_django_apps()
    project_folders = get_project_folders()

    import datetime
    build_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    build_info = f'''
╔══════════════════════════════════════════════════════════════════════╗
║                         BUILD INFORMATION                            ║
╚══════════════════════════════════════════════════════════════════════╝

Build Date: {build_time}
Platform: {platform.system()} {platform.release()}
Python: {sys.version.split()[0]}
Architecture: {platform.machine()}

══════════════════════════════════════════════════════════════════════

📦 INCLUDED COMPONENTS

Django Apps ({len(django_apps)}):
'''

    for app in sorted(django_apps):
        build_info += f"  ✓ {app}\n"

    build_info += f'''
Project Folders ({len(project_folders)}):
'''

    for folder in sorted(project_folders):
        build_info += f"  ✓ {folder}\n"

    build_info += '''
Runtime Components:
  ✓ PostgreSQL (embedded)
  ✓ Redis (embedded)
  ✓ Django Web Server
  ✓ Celery Worker
  ✓ Sync Agent
  ✓ Configuration Manager (config.py)

══════════════════════════════════════════════════════════════════════

🔧 BUILD FEATURES

✅ Auto-detected all Django apps
✅ Integrated configuration management
✅ Embedded database (PostgreSQL)
✅ Embedded cache (Redis)
✅ Online/Offline sync capability
✅ First-run auto-setup
✅ HOD user auto-creation
✅ Post-build runtime copy (FIXED)

══════════════════════════════════════════════════════════════════════

📁 NOTES

• Configuration managed via config.py
• All Django apps automatically discovered and included
• Supports both .env and config.json configuration
• Portable - no installation required
• All data stored in user data directory
• PostgreSQL and Redis properly embedded

══════════════════════════════════════════════════════════════════════
'''

    (dist_dir / "BUILD_INFO.txt").write_text(build_info)
    print(f"✅ Created BUILD_INFO.txt")
    print(f"   • {len(django_apps)} Django apps")
    print(f"   • {len(project_folders)} project folders")

    return True


def copy_utilities_to_dist():
    """
    Copy utility scripts to distribution
    This ensures cleanup_cirqen.py and other utilities are included in the build
    """
    print_banner("Copying Utility Scripts to Distribution")

    dist_dir = DIST_DIR / "Cirqen"

    utilities = [
        ('cleanup_cirqen.py', 'Enhanced cleanup utility (CRITICAL)'),
        ('launch_cirqen.py', 'Application launcher'),
        ('sync_agent.env.example', 'Sync agent environment template'),
    ]

    copied_count = 0

    for util_file, description in utilities:
        src = PROJECT_ROOT / util_file
        dest = dist_dir / util_file

        if src.exists():
            try:
                shutil.copy2(src, dest)

                # Make executable on Linux/Mac
                if not IS_WINDOWS:
                    dest.chmod(0o755)

                logger.info(f"✅ Copied: {util_file}")
                logger.info(f"   Description: {description}")
                copied_count += 1

            except Exception as e:
                logger.error(f"❌ Failed to copy {util_file}: {e}")
                return False
        else:
            logger.warning(f"⚠️  Source not found: {util_file}")

            # Create launch_cirqen.py if it doesn't exist
            if util_file == 'launch_cirqen.py':
                logger.info(f"Creating {util_file}...")
                create_launch_script(dest)
                copied_count += 1
            elif util_file == 'sync_agent.env.example':
                # Auto-generate the env template pointing to Render HQ
                logger.info(f"Auto-generating {util_file}...")
                env_template = (
                    "# ============================================================\n"
                    "# CIRQEN SYNC AGENT — Site Configuration\n"
                    "# Rename this file to .env and fill in your values\n"
                    "# ============================================================\n\n"
                    "# HQ Server (Render)\n"
                    "SYNC_API_URL=https://hq-server-dgs6.onrender.com/api/sync\n"
                    "HQ_UPDATE_URL=https://hq-server-dgs6.onrender.com/api/updates\n\n"
                    "# Auth token - get this from your HQ administrator\n"
                    "SYNC_AUTH_TOKEN=your_auth_token_here\n\n"
                    "# HQ PostgreSQL (Render)\n"
                    "POSTGRES_HQ_HOST=dpg-d7rk2sa8qa3s73diimb0-a.ohio-postgres.render.com\n"
                    "POSTGRES_HQ_PORT=5432\n"
                    "POSTGRES_HQ_DB=cirqen_hq\n"
                    "POSTGRES_HQ_USER=cirqen_hq\n"
                    "POSTGRES_HQ_PASSWORD=your_hq_db_password_here\n\n"
                    "# Local PostgreSQL (this site's database)\n"
                    "POSTGRES_LOCAL_HOST=127.0.0.1\n"
                    "POSTGRES_LOCAL_PORT=2215\n"
                    "POSTGRES_LOCAL_DB=cirqen1\n"
                    "POSTGRES_LOCAL_USER=cirqen1\n"
                    "POSTGRES_LOCAL_PASSWORD=your_local_db_password_here\n\n"
                    "# Local Redis\n"
                    "REDIS_HOST=127.0.0.1\n"
                    "REDIS_PORT=7788\n"
                    "REDIS_PASSWORD=\n"
                )
                dest.write_text(env_template)
                if not IS_WINDOWS:
                    dest.chmod(0o644)
                logger.info(f"✅ Generated: {util_file}")
                copied_count += 1
            else:
                logger.error(f"❌ {util_file} is required but not found!")
                logger.error(f"   Please create {util_file} in project root")
                return False

    logger.info(f"✅ Successfully copied {copied_count} utility scripts")
    return True


def create_launch_script(dest_path):
    """
    Create a simple launch script if it doesn't exist
    """
    launcher_content = '''#!/usr/bin/env python3
"""
Cirqen Desktop Launcher
Simple launcher script for the Cirqen application
"""
import subprocess
import sys
import os
from pathlib import Path



# ============================================================================
# CODE DIRECTORY CREATION FOR UPDATE SYSTEM
# ============================================================================

def create_code_directory(internal_dir: Path, django_apps: list) -> bool:
    """
    Create 'code' directory containing all Django apps and backend code

    This enables the update system to target only backend code,
    leaving frontend, lib, and user data untouched.

    Args:
        internal_dir: Path to _internal directory
        django_apps: List of Django app names

    Returns:
        True if successful
    """
    import hashlib

    logger.info("")
    logger.info("="*70)
    logger.info("CREATING CODE DIRECTORY FOR UPDATE SYSTEM")
    logger.info("="*70)

    try:
        internal_dir = Path(internal_dir)
        backend_dir = internal_dir / "backend"
        code_dir = internal_dir / "code"

        if not internal_dir.exists():
            logger.error(f"❌ _internal directory not found: {internal_dir}")
            return False

        # Check if we have a backend directory
        if not backend_dir.exists():
            logger.warning(f"⚠️  Backend directory not found")
            logger.info("   Looking for Django apps in _internal root...")
            backend_dir = internal_dir

        # Create code directory
        if code_dir.exists():
            logger.info(f"Removing existing code directory...")
            shutil.rmtree(code_dir)

        code_dir.mkdir(exist_ok=True)
        logger.info(f"📁 Created code directory: {code_dir}")

        # Items to include
        items_to_include = []

        # 1. All Django apps
        logger.info("")
        logger.info("Scanning for Django apps:")
        for app_name in django_apps:
            app_path = backend_dir / app_name
            if app_path.exists() and app_path.is_dir():
                items_to_include.append((app_path, app_name))
                logger.info(f"   ✓ {app_name}")
            else:
                app_path = internal_dir / app_name
                if app_path.exists() and app_path.is_dir():
                    items_to_include.append((app_path, app_name))
                    logger.info(f"   ✓ {app_name} (from root)")

        # 2. Django management files
        logger.info("")
        logger.info("Scanning for management files:")
        management_files = ['manage.py', 'config.py', 'settings.py', 'urls.py',
                          'wsgi.py', 'asgi.py', 'django_runner.py']

        for filename in management_files:
            filepath = backend_dir / filename
            if not filepath.exists():
                filepath = internal_dir / filename

            if filepath.exists():
                items_to_include.append((filepath, filename))
                logger.info(f"   ✓ {filename}")

        # 3. Django directories
        logger.info("")
        logger.info("Scanning for Django directories:")
        django_dirs = ['core', 'utils', 'shared', 'common', 'api', 'apps', 'sync']

        for dirname in django_dirs:
            dirpath = backend_dir / dirname
            if not dirpath.exists():
                dirpath = internal_dir / dirname

            if dirpath.exists() and dirpath.is_dir():
                if (dirpath / '__init__.py').exists() or \
                   (dirpath / 'models.py').exists() or \
                   (dirpath / 'views.py').exists():
                    items_to_include.append((dirpath, dirname))
                    logger.info(f"   ✓ {dirname}")

        # 4. Copy items
        logger.info("")
        logger.info("Copying items to code directory...")
        copied_count = 0

        for src_path, item_name in items_to_include:
            dst_path = code_dir / item_name

            try:
                if src_path.is_dir():
                    if dst_path.exists():
                        shutil.rmtree(dst_path)
                    shutil.copytree(
                        src_path, dst_path,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git')
                    )
                else:
                    shutil.copy2(src_path, dst_path)
                copied_count += 1
            except Exception as e:
                logger.error(f"   ❌ Failed to copy {item_name}: {e}")

        logger.info(f"✅ Copied {copied_count} items")

        # 5. Create documentation
        readme = f"""# Code Directory

This directory contains all Django apps and backend code.

## Purpose
The update system targets ONLY this directory for backend code updates.

## Structure
- {len([i for i in items_to_include if i[0].is_dir()])} Django apps
- Management files (manage.py, settings.py, etc.)
- Update configuration

## Updates
When updates are available:
1. Only files in this directory are compared
2. Automatic backup created before changes
3. Changes verified with SHA-256 checksums
4. Automatic rollback on failure

Created: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

© 2024 B12 Technologies
"""
        (code_dir / "README.txt").write_text(readme)

        # 6. Create config
        config = {
            "created": datetime.now().isoformat(),
            "django_apps": django_apps,
            "total_items": copied_count,
            "update_enabled": True,
            "update_server_url": "https://hq-server-dgs6.onrender.com/api/updates"
        }
        (code_dir / ".update_config.json").write_text(json.dumps(config, indent=2))

        # 7. Calculate checksums
        checksums = {}
        for file_path in code_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith('.'):
                try:
                    rel_path = str(file_path.relative_to(code_dir))
                    sha256 = hashlib.sha256()
                    with open(file_path, 'rb') as f:
                        while chunk := f.read(8192):
                            sha256.update(chunk)
                    checksums[rel_path] = sha256.hexdigest()
                except:
                    pass

        (code_dir / ".checksums.json").write_text(json.dumps(checksums, indent=2))
        logger.info(f"   ✓ Created checksums for {len(checksums)} files")

        # Summary
        logger.info("")
        logger.info("="*70)
        logger.info("CODE DIRECTORY SUMMARY")
        logger.info("="*70)
        logger.info(f"Location: {code_dir}")
        logger.info(f"Django Apps: {len(django_apps)}")
        logger.info(f"Items Copied: {copied_count}")
        logger.info(f"Files Checksummed: {len(checksums)}")
        logger.info(f"Update Ready: Yes")
        logger.info("="*70)

        return True

    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def create_code_directory_step():
    """Build step for code directory creation"""
    print_banner("Creating Code Directory for Update System")

    try:
        dist_dir = DIST_DIR / "Cirqen"
        internal_dir = dist_dir / "_internal"

        if not internal_dir.exists():
            logger.error(f"_internal not found: {internal_dir}")
            return False

        django_apps = find_django_apps()
        success = create_code_directory(internal_dir, django_apps)

        if success:
            logger.info("")
            logger.info("✅ CODE DIRECTORY CREATED SUCCESSFULLY")
            logger.info("Update system enabled!")

        return success

    except Exception as e:
        logger.error(f"Failed: {e}")
        return False



def main():
    """Launch the Cirqen application"""
    script_dir = Path(__file__).parent

    # Determine executable name
    if sys.platform == "win32":
        exe_name = "Cirqen.exe"
    else:
        exe_name = "Cirqen"

    exe_path = script_dir / exe_name

    # Check if executable exists
    if not exe_path.exists():
        print(f"❌ Error: {exe_name} not found!")
        print(f"   Expected at: {exe_path}")
        input("Press Enter to exit...")
        return 1

    # Launch the application
    print(f"🚀 Starting Cirqen...")
    print(f"   Executable: {exe_path}")
    print()

    try:
        env = os.environ.copy()
        if sys.platform != "win32":
            env.setdefault(
                "QTWEBENGINE_CHROMIUM_FLAGS",
                "--no-sandbox --disable-gpu --disable-software-rasterizer "
                "--disable-gpu-sandbox --single-process"
            )
            env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
            env.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.webenginecontext.info=false")
        result = subprocess.run([str(exe_path)], env=env)
        return result.returncode
    except Exception as e:
        print(f"❌ Error starting Cirqen: {e}")
        input("Press Enter to exit...")
        return 1

if __name__ == "__main__":
    sys.exit(main())
'''

    dest_path.write_text(launcher_content)

    # Make executable on Linux/Mac
    if not IS_WINDOWS:
        dest_path.chmod(0o755)

    logger.info(f"✅ Created: {dest_path.name}")


def verify_utilities_in_dist():
    """
    Verify that all utility scripts are present in dist
    This is a safety check to ensure critical files weren't missed
    """
    print_banner("Verifying Utilities in Distribution")

    dist_dir = DIST_DIR / "Cirqen"

    required_utilities = {
        'cleanup_cirqen.py': 'Cleanup utility (CRITICAL)',
        'launch_cirqen.py': 'Launch script',
        'Start_Cirqen.bat' if IS_WINDOWS else 'start_cirqen.sh': 'Platform launcher',
    }

    all_present = True

    for util_file, description in required_utilities.items():
        util_path = dist_dir / util_file

        if util_path.exists():
            size = util_path.stat().st_size
            logger.info(f"✅ {util_file}")
            logger.info(f"   {description}")
            logger.info(f"   Size: {size:,} bytes")

            # Verify it's not empty
            if size < 100:
                logger.warning(f"⚠️  {util_file} seems too small ({size} bytes)")
                all_present = False
        else:
            logger.error(f"❌ MISSING: {util_file}")
            logger.error(f"   {description}")
            all_present = False

    if all_present:
        logger.info("✅ All utility scripts verified and present")
        return True
    else:
        logger.error("❌ Some utility scripts are missing or invalid")
        return False



def verify_update_files_in_dist():
    """
    Verify that update system files are present in _internal
    """
    print_banner("Verifying Update System Files")

    internal_dir = DIST_DIR / "Cirqen" / "_internal"

    update_files = {
        'update_manager.py': 'Update manager with reconnection support',
        'update_client.py': 'Update client for server communication'
    }

    all_present = True

    for update_file, description in update_files.items():
        file_path = internal_dir / update_file

        if file_path.exists():
            size = file_path.stat().st_size
            logger.info(f"✅ {update_file}")
            logger.info(f"   {description}")
            logger.info(f"   Size: {size:,} bytes")

            # Verify it's not empty
            if size < 1000:
                logger.warning(f"⚠️  {update_file} seems too small ({size} bytes)")
                all_present = False
        else:
            logger.error(f"❌ MISSING: {update_file}")
            logger.error(f"   {description}")
            logger.error(f"   Update functionality will NOT work!")
            all_present = False

    if all_present:
        logger.info("✅ All update system files verified and present")
        return True
    else:
        logger.error("❌ Some update system files are missing")
        logger.error("   Application will work but updates won't be available")
        return True  # Don't fail build, just warn


# ==============================================================================
# STEP 2: UPDATE THE main() FUNCTION
# ==============================================================================

# Find the main() function and UPDATE the steps list:



# ============================================================================
# CODE DIRECTORY CREATION FOR UPDATE SYSTEM
# ============================================================================

def create_code_directory(internal_dir: Path, django_apps: list) -> bool:
    """
    Create 'code' directory containing all Django apps and backend code

    This enables the update system to target only backend code,
    leaving frontend, lib, and user data untouched.

    Args:
        internal_dir: Path to _internal directory
        django_apps: List of Django app names

    Returns:
        True if successful
    """
    import hashlib

    logger.info("")
    logger.info("="*70)
    logger.info("CREATING CODE DIRECTORY FOR UPDATE SYSTEM")
    logger.info("="*70)

    try:
        internal_dir = Path(internal_dir)
        backend_dir = internal_dir / "backend"
        code_dir = internal_dir / "code"

        if not internal_dir.exists():
            logger.error(f"❌ _internal directory not found: {internal_dir}")
            return False

        # Check if we have a backend directory
        if not backend_dir.exists():
            logger.warning(f"⚠️  Backend directory not found")
            logger.info("   Looking for Django apps in _internal root...")
            backend_dir = internal_dir

        # Create code directory
        if code_dir.exists():
            logger.info(f"Removing existing code directory...")
            shutil.rmtree(code_dir)

        code_dir.mkdir(exist_ok=True)
        logger.info(f"📁 Created code directory: {code_dir}")

        # Items to include
        items_to_include = []

        # 1. All Django apps
        logger.info("")
        logger.info("Scanning for Django apps:")
        for app_name in django_apps:
            app_path = backend_dir / app_name
            if app_path.exists() and app_path.is_dir():
                items_to_include.append((app_path, app_name))
                logger.info(f"   ✓ {app_name}")
            else:
                app_path = internal_dir / app_name
                if app_path.exists() and app_path.is_dir():
                    items_to_include.append((app_path, app_name))
                    logger.info(f"   ✓ {app_name} (from root)")

        # 2. Django management files
        logger.info("")
        logger.info("Scanning for management files:")
        management_files = ['manage.py', 'config.py', 'settings.py', 'urls.py',
                          'wsgi.py', 'asgi.py', 'django_runner.py']

        for filename in management_files:
            filepath = backend_dir / filename
            if not filepath.exists():
                filepath = internal_dir / filename

            if filepath.exists():
                items_to_include.append((filepath, filename))
                logger.info(f"   ✓ {filename}")

        # 3. Django directories
        logger.info("")
        logger.info("Scanning for Django directories:")
        django_dirs = ['core', 'utils', 'shared', 'common', 'api', 'apps', 'sync']

        for dirname in django_dirs:
            dirpath = backend_dir / dirname
            if not dirpath.exists():
                dirpath = internal_dir / dirname

            if dirpath.exists() and dirpath.is_dir():
                if (dirpath / '__init__.py').exists() or \
                   (dirpath / 'models.py').exists() or \
                   (dirpath / 'views.py').exists():
                    items_to_include.append((dirpath, dirname))
                    logger.info(f"   ✓ {dirname}")

        # 4. Copy items
        logger.info("")
        logger.info("Copying items to code directory...")
        copied_count = 0

        for src_path, item_name in items_to_include:
            dst_path = code_dir / item_name

            try:
                if src_path.is_dir():
                    if dst_path.exists():
                        shutil.rmtree(dst_path)
                    shutil.copytree(
                        src_path, dst_path,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git')
                    )
                else:
                    shutil.copy2(src_path, dst_path)
                copied_count += 1
            except Exception as e:
                logger.error(f"   ❌ Failed to copy {item_name}: {e}")

        logger.info(f"✅ Copied {copied_count} items")

        # 5. Create documentation
        readme = f"""# Code Directory

This directory contains all Django apps and backend code.

## Purpose
The update system targets ONLY this directory for backend code updates.

## Structure
- {len([i for i in items_to_include if i[0].is_dir()])} Django apps
- Management files (manage.py, settings.py, etc.)
- Update configuration

## Updates
When updates are available:
1. Only files in this directory are compared
2. Automatic backup created before changes
3. Changes verified with SHA-256 checksums
4. Automatic rollback on failure

Created: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

© 2024 B12 Technologies
"""
        (code_dir / "README.txt").write_text(readme)

        # 6. Create config
        config = {
            "created": datetime.now().isoformat(),
            "django_apps": django_apps,
            "total_items": copied_count,
            "update_enabled": True,
            "update_server_url": "https://hq-server-dgs6.onrender.com/api/updates"
        }
        (code_dir / ".update_config.json").write_text(json.dumps(config, indent=2))

        # 7. Calculate checksums
        checksums = {}
        for file_path in code_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith('.'):
                try:
                    rel_path = str(file_path.relative_to(code_dir))
                    sha256 = hashlib.sha256()
                    with open(file_path, 'rb') as f:
                        while chunk := f.read(8192):
                            sha256.update(chunk)
                    checksums[rel_path] = sha256.hexdigest()
                except:
                    pass

        (code_dir / ".checksums.json").write_text(json.dumps(checksums, indent=2))
        logger.info(f"   ✓ Created checksums for {len(checksums)} files")

        # Summary
        logger.info("")
        logger.info("="*70)
        logger.info("CODE DIRECTORY SUMMARY")
        logger.info("="*70)
        logger.info(f"Location: {code_dir}")
        logger.info(f"Django Apps: {len(django_apps)}")
        logger.info(f"Items Copied: {copied_count}")
        logger.info(f"Files Checksummed: {len(checksums)}")
        logger.info(f"Update Ready: Yes")
        logger.info("="*70)

        return True

    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def create_code_directory_step():
    """Build step for code directory creation"""
    print_banner("Creating Code Directory for Update System")

    try:
        dist_dir = DIST_DIR / "Cirqen"
        internal_dir = dist_dir / "_internal"

        if not internal_dir.exists():
            logger.error(f"_internal not found: {internal_dir}")
            return False

        django_apps = find_django_apps()
        success = create_code_directory(internal_dir, django_apps)

        if success:
            logger.info("")
            logger.info("✅ CODE DIRECTORY CREATED SUCCESSFULLY")
            logger.info("Update system enabled!")

        return success

    except Exception as e:
        logger.error(f"Failed: {e}")
        return False



def main():
    """Main build orchestrator"""

    print("\n" + "=" * 70)
    print(" " * 15 + "CIRQEN DESKTOP BUILD SYSTEM")
    print(" " * 15 + "(Enhanced with Dynamic Ports)")  # ← UPDATE THIS LINE
    print("=" * 70)
    print(f"Platform: {platform.system()} {platform.release()}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Architecture: {platform.machine()}")
    print("=" * 70)

    # REPLACE YOUR EXISTING steps LIST WITH THIS:
    steps = [
        ("Check Requirements", check_requirements),
        ("Verify Installed Packages", verify_requirements),
        ("Setup Configuration", setup_config),
        ("Create Resources", create_resources),
        ("Setup PostgreSQL", setup_postgresql),
        ("Fix PostgreSQL Run Permissions (Linux)", fix_postgresql_run_permissions),
        ("Setup Redis", setup_redis),
        ("Install Dependencies", install_dependencies),
        ("Verify Installation", verify_requirements),
        ("Collect Static Files", collect_static),
        ("Generate Spec File", generate_spec),
        ("Build Executable + Copy Runtime", build_executable),
        ("Copy Utility Scripts", copy_utilities_to_dist),
        ("Create Launchers", create_launchers),     # ← ADD THIS LINE
        ("Verify Utilities", verify_utilities_in_dist),        # ← ADD THIS LINE
        ("Verify Update Files", verify_update_files_in_dist),  # ← ADD THIS LINE (NEW!)
        ("Verify Build", verify_build),
        ("Create Documentation", create_readme),
        ("Create Build Info", create_build_info),
        ("Package Distribution", package_distribution),
    ]

    # Rest of main() function stays the same...
    total = len(steps)

    for i, (name, func) in enumerate(steps, 1):
        print_step(i, total, name)

        if not func():
            print(f"\n❌ BUILD FAILED at: {name}")
            print("\nPlease fix the error and run again.")
            return 1

    # Success section - UPDATE the output to mention cleanup utility:
    print("\n" + "=" * 70)
    print(" " * 20 + "BUILD SUCCESSFUL! 🎉")
    print("=" * 70)

    dist_app = DIST_DIR / "Cirqen"
    print(f"\n📦 Distribution ready at:")
    print(f"   {dist_app.absolute()}")

    # Show what was included
    django_apps = find_django_apps()
    print(f"\n✅ Included Components:")
    print(f"   • {len(django_apps)} Django apps (auto-detected)")
    print(f"   • Configuration manager (config.py)")
    print(f"   • Enhanced main.py with UpdateManager")      # ← CHANGED
    print(f"   • Update system (update_manager.py + update_client.py)")  # ← ADD THIS
    print(f"   • Cleanup utility (cleanup_cirqen.py)")      # ← ADD THIS
    print(f"   • Launch scripts")                           # ← ADD THIS
    print(f"   • Embedded PostgreSQL ✅ FIXED")
    print(f"   • Embedded Redis ✅ FIXED")
    print(f"   • All static files and templates")

    print(f"\n📋 Next steps:")
    print(f"   1. Test: cd {dist_app}")
    print(f"   2. Run: {'Start_Cirqen.bat' if IS_WINDOWS else './start_cirqen.sh'}")
    print(f"   3. Test cleanup: python cleanup_cirqen.py")  # ← ADD THIS
    print(f"   4. Review: BUILD_INFO.txt")
    print(f"   5. Package for distribution")

    print(f"\n💡 Enhanced Features:")                        # ← ADD THIS SECTION
    print(f"   ✓ Dynamic port allocation")
    print(f"   ✓ Session management")
    print(f"   ✓ Robust cleanup utility")
    print(f"   ✓ Single instance enforcement")
    print(f"   ✓ Comprehensive error handling")

    print("\n" + "=" * 70)

    return 0


if __name__ == '__main__':
    sys.exit(main())
