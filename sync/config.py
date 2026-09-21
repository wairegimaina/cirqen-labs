"""Compatibility shim. The one implementation is the top-level ``config.py``.

This file used to be a second, drifting copy of the same class. It now loads
the top-level module by path (never by name, so it cannot import itself when
``sync/`` is on ``sys.path``) and re-exports it, so ``from sync.config import
CirqenConfig`` keeps working.
"""
import importlib.util
import sys
from pathlib import Path

_ROOT_CONFIG = Path(__file__).resolve().parent.parent / "config.py"


def _load():
    existing = sys.modules.get("config")
    if existing is not None and getattr(existing, "__file__", None):
        try:
            if Path(existing.__file__).resolve() == _ROOT_CONFIG.resolve():
                return existing
        except OSError:
            pass
    if not _ROOT_CONFIG.is_file():
        # Packaged layouts differ; the top-level module is importable by name there.
        import config as module  # noqa: PLC0415

        if Path(getattr(module, "__file__", "") or "").resolve() != Path(__file__).resolve():
            return module
        raise ImportError(f"top-level config.py not found at {_ROOT_CONFIG}")
    spec = importlib.util.spec_from_file_location("config", _ROOT_CONFIG)
    module = importlib.util.module_from_spec(spec)
    sys.modules["config"] = module
    spec.loader.exec_module(module)
    return module


_impl = _load()
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
