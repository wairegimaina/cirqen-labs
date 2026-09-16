import argparse
import logging
import signal
import time
from pathlib import Path

from .agent_prelude import LOG, setup_logging

# ── Legacy engine, split across sync_agent_1..9 by responsibility ────────────
# (Same code as before; the 9 files previously all declared an identically named
#  `class SyncAgent`, which made the MRO impossible to read. Each now has a
#  descriptive role name. Order below is preserved 1→9 so the MRO is identical.)
from .sync_agent_1 import AgentInitMixin
from .sync_agent_2 import SchemaAndChangeDetectionMixin
from .sync_agent_3 import UploadMixin
from .sync_agent_4 import NetworkLoopsMixin
from .sync_agent_5 import ParentRecoveryMixin
from .sync_agent_6 import ApplyRemoteUpdateMixin
from .sync_agent_7 import DownloadCertHeartbeatMixin
from .sync_agent_8 import LifecycleMixin
from .sync_agent_9 import StatusReportingMixin

# ── Phase 1–2 capability mixins (highest precedence) ─────────────────────────
from .conflict_quarantine import ConflictQuarantineMixin
from .conflict_resolver import ConflictResolverMixin
from .schema_guard import SchemaGuardMixin
from .cert_conflict_guard import CertConflictGuardMixin
from .drift_reconciler import DriftReconcilerMixin


class SyncAgent(
    # New capabilities first (override legacy where names overlap).
    ConflictQuarantineMixin, ConflictResolverMixin, SchemaGuardMixin,
    CertConflictGuardMixin, DriftReconcilerMixin,
    # Legacy engine, in dependency order (unchanged MRO).
    AgentInitMixin, SchemaAndChangeDetectionMixin, UploadMixin, NetworkLoopsMixin,
    ParentRecoveryMixin, ApplyRemoteUpdateMixin, DownloadCertHeartbeatMixin,
    LifecycleMixin, StatusReportingMixin,
):
    pass


def main():
    """CLI entry point for the sync agent.

    Parses --data-dir / --log-level, wires file logging to
    <data-dir>/logs/sync_agent.log, then runs the SyncAgent until
    interrupted (SIGINT/SIGTERM) or stop_event is set.
    """
    parser = argparse.ArgumentParser(description="Cirqen Sync Agent")
    parser.add_argument(
        "--data-dir",
        default=str(Path.home() / ".cmms"),
        help="Path to application data directory (default: ~/.cmms)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        help="Logging level for sync_agent.log (default: WARNING)",
    )
    args = parser.parse_args()

    data_path = Path(args.data_dir).expanduser()
    log_file = str(data_path / "logs" / "sync_agent.log")
    log_level = getattr(logging, args.log_level.upper(), logging.WARNING)

    # Wire every sync-subsystem logger to the resolved log file before
    # the agent (and its threads) start producing output.
    setup_logging(log_file=log_file, level=log_level)

    try:
        from core.monitoring import init_sentry

        init_sentry("sync_agent")
    except ImportError:  # running from a checkout without the Django apps
        pass

    agent = SyncAgent(data_path=data_path)

    def _handle_signal(signum, frame):
        LOG.info("Received signal %s, stopping SyncAgent...", signum)
        agent.stop_event.set()

    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except ValueError:
        # main() is running inside a worker thread (e.g. SyncAgentThread
        # spawned by services.py), where signal handlers can't be
        # registered. Shutdown will rely on stop_event being set
        # externally (e.g. agent.stop() called by the host process).
        LOG.debug("Not in main thread — skipping signal handler registration")

    agent.start()

    try:
        while not agent.stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        agent.stop_event.set()
    finally:
        agent.stop()


if __name__ == "__main__":
    main()


__all__ = ["SyncAgent", "main"]
