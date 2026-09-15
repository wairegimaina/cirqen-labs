from .agent_prelude import LOG
import os
import json
import shutil
import threading
from pathlib import Path
from typing import Dict, Any, Optional


class StateManager:
    """
    Manages persistent state with atomic writes and corruption protection.
    Triple redundancy: Redis (optional) → File (durable) → Safe default
    """

    def __init__(self, state_dir: str, redis_client=None):
        self.state_dir = Path(os.path.expanduser(state_dir))
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.state_file = self.state_dir / "sync_state.json"
        self.state_file_backup = self.state_dir / "sync_state.json.bak"
        self.client_id_file = self.state_dir / "client_id"

        self.redis = redis_client
        self.use_redis = redis_client is not None

        # Serialises writes. Several threads persist state concurrently — the
        # upload loop, the download loop and the drift reconciler all call set().
        # Without this they race on the shared temp file: two writers create it,
        # the first rename consumes it, and the second fails with
        # "No such file or directory: sync_state.tmp -> sync_state.json".
        self._write_lock = threading.Lock()

        # Load initial state from file
        self._state_cache = self._load_state_from_file()

        LOG.info("State manager initialized")
        LOG.info("  State file: %s", self.state_file)
        LOG.info("  Redis: %s", "Enabled" if self.use_redis else "Disabled (file-only mode)")

    def _load_state_from_file(self) -> Dict[str, Any]:
        """Load state from file with backup fallback"""
        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)
                LOG.debug("Loaded state from primary file")
                return state
        except FileNotFoundError:
            LOG.debug("No state file found, starting fresh")
            return {}
        except json.JSONDecodeError as e:
            LOG.warning("State file corrupted: %s, trying backup", e)
        except Exception as e:
            LOG.warning("Failed to load state file: %s, trying backup", e)

        try:
            with open(self.state_file_backup, "r") as f:
                state = json.load(f)
                LOG.info("✅ Recovered state from backup file")
                self._save_state_to_file(state)
                return state
        except Exception as e:
            LOG.debug("No backup file available: %s", e)

        return {}

    def _save_state_to_file(self, state: Dict[str, Any]):
        """Atomically save state to file with backup.

        Two things here are load-bearing:

        * The temp file name is UNIQUE per writer. A shared name let concurrent
          writers clobber each other's temp file, so the second rename failed
          with ENOENT. The lock below already prevents that within one process;
          the unique name also covers a second process (a stray agent instance)
          touching the same state directory.
        * The backup is a COPY, not a move. The previous code did
          ``state_file.replace(backup)``, which removes the live state file — so
          if the following rename failed for any reason, the agent was left with
          NO state file and would re-scan every table from epoch on next start.
        """
        with self._write_lock:
            temp_file = self.state_file.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                with open(temp_file, "w") as f:
                    json.dump(state, f, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())

                if self.state_file.exists():
                    try:
                        shutil.copy2(self.state_file, self.state_file_backup)
                    except Exception as e:
                        LOG.debug("Could not create backup: %s", e)

                temp_file.replace(self.state_file)   # atomic on the same filesystem
                LOG.debug("State saved to file")

            except Exception as e:
                LOG.error("Failed to save state file: %s", e)
            finally:
                # Never leave a stray temp behind if the write or rename failed.
                try:
                    if temp_file.exists():
                        temp_file.unlink()
                except Exception:
                    pass

    def get(self, key: str, default: Any = None) -> Any:
        """Get value with triple redundancy: Redis → Cache → File → Default"""
        if self.use_redis:
            try:
                value = self.redis.get(f"cmms:{key}")
                if value is not None:
                    return value
            except Exception as e:
                LOG.debug("Redis read failed for %s: %s", key, e)

        if key in self._state_cache:
            return self._state_cache[key]

        self._state_cache = self._load_state_from_file()
        if key in self._state_cache:
            return self._state_cache[key]

        return default

    def set(self, key: str, value: Any):
        """Set value with dual write: Redis + File"""
        self._state_cache[key] = value

        if self.use_redis:
            try:
                self.redis.set(f"cmms:{key}", value)
            except Exception as e:
                LOG.debug("Redis write failed for %s: %s", key, e)

        # Snapshot before writing: json.dump iterates the mapping, and another
        # thread adding a key mid-dump raises "dictionary changed size during
        # iteration", which would abort the save and lose the checkpoint.
        self._save_state_to_file(dict(self._state_cache))

    def get_client_id(self) -> Optional[str]:
        """Get client ID from Redis or file"""
        if self.use_redis:
            try:
                client_id = self.redis.get("cmms:client_id")
                if client_id:
                    return client_id
            except Exception:
                pass

        if "client_id" in self._state_cache:
            return self._state_cache["client_id"]

        try:
            with open(self.client_id_file, "r") as f:
                return f.read().strip()
        except FileNotFoundError:
            return None

    def set_client_id(self, client_id: str):
        """Store client ID with triple redundancy"""
        self._state_cache["client_id"] = client_id

        if self.use_redis:
            try:
                self.redis.set("cmms:client_id", client_id)
            except Exception:
                pass

        self._save_state_to_file(self._state_cache)

        try:
            with open(self.client_id_file, "w") as f:
                f.write(client_id)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            LOG.warning("Failed to write client_id file: %s", e)


# ---------- Dependency Discovery & Sorting ----------
