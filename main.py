#!/usr/bin/env python3
import multiprocessing
import sys

from bulider_tools.runtime import SKIP_GUI_IMPORTS


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
        from bulider_tools.app import main
        sys.exit(main())
    elif SKIP_GUI_IMPORTS:
        from bulider_tools.runtime import logger
        logger.info('Subprocess mode - skipping main() execution')
