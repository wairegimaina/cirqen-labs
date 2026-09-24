#!/usr/bin/env python3
import multiprocessing
import sys

from bulider_tools.runtime import SKIP_GUI_IMPORTS


def _quiet_terminal():
    """Send all console output of the packaged app -- ours, Django's,
    Qt/Chromium's and the child services' -- to logs/launcher.log instead of
    the terminal that launched it. CIRQEN_DEBUG=1 keeps it on the terminal.

    Redirects the file descriptors, not just sys.stdout, so native output
    (Chromium) and inherited child output are caught too. Only the GUI
    process does this: management-command subprocesses must keep writing to
    the pipes their caller reads errors from.
    """
    import os
    if os.environ.get('CIRQEN_DEBUG') == '1' or not getattr(sys, 'frozen', False):
        return
    from bulider_tools.runtime import DATA_PATH, rotate_log_if_large
    log_path = DATA_PATH / 'logs' / 'launcher.log'
    rotate_log_if_large(log_path)
    try:
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError:
        return
    for stream in (sys.stdout, sys.stderr):
        if stream:
            stream.flush()
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    for stream in (sys.stdout, sys.stderr):
        if stream:
            stream.reconfigure(line_buffering=True)


if __name__ == '__main__':
    multiprocessing.freeze_support()

    # ── Django management-command dispatch ────────────────────────────────────
    # The frozen entry point is main.py, NOT manage.py. Several places (the
    # updater's _run_migrations, bulider_tools/database.py) apply migrations via
    #     subprocess.run([sys.executable, "manage.py", "migrate", ...])
    # In the frozen app sys.executable is this binary, so without this branch
    # those calls fall through to the GUI (spawning a rogue second instance or
    # silently no-op'ing) and the migration never runs. Intercept and hand off
    # to Django's command runner, then exit.
    import os
    if len(sys.argv) > 1 and os.path.basename(sys.argv[1]) == 'manage.py':
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Equiper.settings')
        from django.core.management import execute_from_command_line
        # Drop the manage.py path (argv[1]); keep program name + command + flags.
        execute_from_command_line([sys.argv[0], *sys.argv[2:]])
        sys.exit(0)

    if multiprocessing.current_process().name == 'MainProcess' and not SKIP_GUI_IMPORTS:
        _quiet_terminal()
        from bulider_tools.app import main
        sys.exit(main())
    elif SKIP_GUI_IMPORTS:
        from bulider_tools.runtime import logger
        logger.info('Subprocess mode - skipping main() execution')
