"""Error monitoring (IMPROVEMENT_PLAN.md 4.5).

Sends unhandled exceptions and ERROR log records to Sentry, from both the Django
process and the sync agent, tagged with the application version so a failure
can be tied to the update that introduced it.

Off unless SENTRY_DSN is set and ``sentry-sdk`` is installed, so development,
tests and offline sites are unaffected. Deliberately has no Django import: the
sync agent calls it outside Django.

    SENTRY_DSN          project DSN (self-hosted or SaaS); unset = disabled
    SENTRY_ENVIRONMENT  e.g. "production", "staging" (default "production")
    SENTRY_TRACES_RATE  performance sampling 0.0-1.0 (default 0)
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_initialised = False


def app_version():
    try:
        return (Path(__file__).resolve().parents[1] / "version.txt").read_text().strip()
    except OSError:
        return "unknown"


def init_sentry(component, client_name=None, with_django=False):
    """Initialise Sentry once per process; return True when monitoring is active."""
    global _initialised
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if _initialised or not dsn:
        return _initialised
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; monitoring is off")
        return False

    integrations = [LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)]
    if with_django:
        from sentry_sdk.integrations.django import DjangoIntegration

        integrations.append(DjangoIntegration())

    sentry_sdk.init(
        dsn=dsn,
        release=f"equiper@{app_version()}",
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0") or 0),
        integrations=integrations,
        # Equipment, signatures and user records are clinical/HR data: never
        # attach request bodies, cookies or user identity by default.
        send_default_pii=False,
        max_request_body_size="never",
    )
    sentry_sdk.set_tag("component", component)
    if client_name:
        sentry_sdk.set_tag("client", client_name)
    _initialised = True
    return True
