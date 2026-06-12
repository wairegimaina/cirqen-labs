#!/usr/bin/env python3
"""
Django Runner for Cirqen
Standalone script to run Django without the GUI
CRITICAL: This must be run with Python, NOT the Cirqen executable
"""
import os
import sys
from pathlib import Path

# ============================
# CRITICAL: Setup paths FIRST
# ============================
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller temp directory
        BASE_DIR = Path(sys._MEIPASS)
    else:
        # Bundle directory
        BASE_DIR = Path(sys.executable).parent
else:
    # Running as script
    BASE_DIR = Path(__file__).parent.absolute()

# Add to Python path if not already there
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ============================
# Django Configuration
# ============================
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Equiper.settings')

# ============================
# Logging Setup
# ============================
import logging

logging.basicConfig(
    level=logging.INFO,
    format='[DJANGO_RUNNER] %(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger('DjangoRunner')

# ============================
# Main Entry Point
# ============================
if __name__ == '__main__':
    logger.info("="*70)
    logger.info("DJANGO RUNNER STARTED")
    logger.info("="*70)
    logger.info(f"PID:         {os.getpid()}")
    logger.info(f"Parent PID:  {os.getppid()}")
    logger.info(f"Python:      {sys.executable}")
    logger.info(f"Base Dir:    {BASE_DIR}")
    logger.info(f"Working Dir: {os.getcwd()}")
    logger.info(f"Command:     {' '.join(sys.argv)}")
    logger.info("="*70)

    try:
        # Import Django
        logger.info("Importing Django...")
        from django.core.management import execute_from_command_line

        logger.info("Executing Django command...")
        logger.info("="*70)

        # Run Django
        execute_from_command_line(sys.argv)

    except ImportError as e:
        logger.error("="*70)
        logger.error("DJANGO IMPORT ERROR")
        logger.error("="*70)
        logger.error(f"Error: {e}")
        logger.error("Django is not installed or not in Python path")
        logger.error(f"Python path: {sys.path[:3]}")
        sys.exit(1)

    except Exception as e:
        logger.error("="*70)
        logger.error("DJANGO RUNTIME ERROR")
        logger.error("="*70)
        logger.error(f"Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
