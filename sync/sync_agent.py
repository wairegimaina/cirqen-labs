import argparse
import logging
import signal
import time
from pathlib import Path

from .agent_prelude import LOG, setup_logging
from .sync_agent_1 import SyncAgent as _A1
from .sync_agent_2 import SyncAgent as _A2
from .sync_agent_3 import SyncAgent as _A3
from .sync_agent_4 import SyncAgent as _A4
from .sync_agent_5 import SyncAgent as _A5
from .sync_agent_6 import SyncAgent as _A6
from .sync_agent_7 import SyncAgent as _A7
from .sync_agent_8 import SyncAgent as _A8
from .sync_agent_9 import SyncAgent as _A9


class SyncAgent(_A1, _A2, _A3, _A4, _A5, _A6, _A7, _A8, _A9):
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
