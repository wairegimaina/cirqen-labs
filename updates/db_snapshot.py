"""PostgreSQL snapshot and restore for updates (IMPROVEMENT_PLAN.md section 9).

The updater used to snapshot only SQLite files, so on the PostgreSQL clients a
failed migration rolled back the code but left the database migrated. These
helpers take a pg_dump of the public schema before migrating and restore it
atomically on rollback: the schema is dropped and the dump replayed inside a
single psql transaction, so a failed restore leaves the database as it was.

No Django imports: the rollback also runs from startup recovery.
"""
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class SnapshotError(RuntimeError):
    pass


def find_pg_tool(name):
    """Locate pg_dump / pg_restore / psql: $CIRQEN_PG_BIN, the embedded runtime, then PATH."""
    candidates = []
    if os.getenv("CIRQEN_PG_BIN"):
        candidates.append(Path(os.environ["CIRQEN_PG_BIN"]))
    if os.getenv("CIRQEN_RUNTIME_DIR"):
        candidates.append(Path(os.environ["CIRQEN_RUNTIME_DIR"]) / "postgresql" / "bin")
    candidates.append(Path(__file__).resolve().parents[1] / "runtime" / "postgresql" / "bin")
    for directory in candidates:
        tool = directory / name
        if tool.exists():
            return str(tool)
    found = shutil.which(name)
    if not found:
        raise SnapshotError(f"{name} not found")
    return found


def _env(db):
    env = dict(os.environ)
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = str(db["PASSWORD"])
    tool_lib = Path(find_pg_tool("psql")).parent.parent / "lib"
    if tool_lib.is_dir():
        env["LD_LIBRARY_PATH"] = f"{tool_lib}:{env.get('LD_LIBRARY_PATH', '')}"
    return env


def _conn_args(db):
    args = ["-h", str(db.get("HOST") or "127.0.0.1"), "-U", str(db["USER"])]
    if db.get("PORT"):
        args += ["-p", str(db["PORT"])]
    return args


def dump(db, dest):
    """pg_dump the public schema of ``db`` (a Django DATABASES entry) to ``dest``."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [find_pg_tool("pg_dump"), *_conn_args(db), "-d", str(db["NAME"]),
           "--format=custom", "--schema=public", "--no-owner", "--no-privileges", "-f", str(dest)]
    result = subprocess.run(cmd, env=_env(db), capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise SnapshotError(f"pg_dump failed: {result.stderr.strip()[:500]}")
    return dest


def restore(db, src):
    """Replace the public schema of ``db`` with the snapshot, all or nothing."""
    with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as sql_file:
        sql_path = Path(sql_file.name)
    try:
        to_sql = subprocess.run(
            [find_pg_tool("pg_restore"), "--no-owner", "--no-privileges", "-f", str(sql_path), str(src)],
            env=_env(db), capture_output=True, text=True, timeout=900,
        )
        if to_sql.returncode != 0:
            raise SnapshotError(f"pg_restore failed: {to_sql.stderr.strip()[:500]}")
        body = sql_path.read_text()
        # A --schema=public dump normally recreates the schema itself.
        create = "" if "CREATE SCHEMA public;" in body else "CREATE SCHEMA public;\n"
        script = "DROP SCHEMA IF EXISTS public CASCADE;\n" + create + body
        sql_path.write_text(script)
        replay = subprocess.run(
            [find_pg_tool("psql"), *_conn_args(db), "-d", str(db["NAME"]),
             "--single-transaction", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(sql_path)],
            env=_env(db), capture_output=True, text=True, timeout=1800,
        )
        if replay.returncode != 0:
            raise SnapshotError(f"restore rolled back, database unchanged: {replay.stderr.strip()[:500]}")
    finally:
        sql_path.unlink(missing_ok=True)
