"""Cirqen Control tests (the update server and its admin panel).

    cd hq_server && pip install -r requirements.txt pytest && pytest tests
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # hq_server modules first (its main.py, not the desktop's)
sys.path.append(str(HERE.parent.parent))      # last: only for the desktop's hq_handshake
