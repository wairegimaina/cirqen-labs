#!/usr/bin/env python3
import multiprocessing
import sys

from bulider_tools.runtime import SKIP_GUI_IMPORTS


if __name__ == '__main__':
    multiprocessing.freeze_support()
    if multiprocessing.current_process().name == 'MainProcess' and not SKIP_GUI_IMPORTS:
        from bulider_tools.app import main
        sys.exit(main())
    elif SKIP_GUI_IMPORTS:
        from bulider_tools.runtime import logger
        logger.info('Subprocess mode - skipping main() execution')
