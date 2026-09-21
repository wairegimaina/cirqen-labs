"""Caching for expensive dashboard and report aggregates (IMPROVEMENT_PLAN.md 5.2).

Only aggregates are cached: small JSON-serialisable dicts of counts. Pages are
never cached, since they carry per-user state and CSRF tokens.

Keys are grouped into namespaces. ``invalidate(namespace)`` bumps a generation
number stored in the cache, which orphans every key written under the previous
generation at once. That works on any cache backend (no key-pattern deletes)
and is what the save/delete signals in ``core.apps`` call. The TTLs bound
staleness for writes that bypass signals, such as ``QuerySet.update()`` and the
sync applier's bulk writes.

When the cache is unavailable (Redis down) every lookup is a miss and the
aggregate is computed directly, so callers never need a fallback of their own.
"""
import json
import logging

from django.core.cache import caches
from django.core.serializers.json import DjangoJSONEncoder

logger = logging.getLogger("cirqen.cache")

# namespace -> seconds. One place for the policy.
TTL = {
    "dash": 5 * 60,  # workshop dashboard and HOD overview counters
    "ppm": 10 * 60,  # PPM status distribution
    "cal": 10 * 60,  # calibration schedule snapshot
    "rpt": 15 * 60,  # report hub tables
    "inv": 30 * 60,  # equipment category charts
}


def _cache():
    return caches["default"]


def _generation(namespace):
    try:
        return _cache().get(f"agg:{namespace}:gen") or 0
    except Exception:
        return 0


def make_key(namespace, *parts):
    suffix = ":".join(str(p) for p in parts)
    return f"agg:{namespace}:g{_generation(namespace)}:{suffix}"


def get_or_compute(namespace, parts, compute):
    """Return the cached aggregate for (namespace, parts), computing it on a miss."""
    key = make_key(namespace, *parts)
    try:
        value = _cache().get(key)
    except Exception:
        value = None
    if value is not None:
        return value
    # Round-trip through JSON: the production cache serialises to JSON, so this
    # makes a miss return exactly what a later hit will (string keys, UUIDs and
    # dates as strings) on every backend, including the pickling test cache.
    value = json.loads(json.dumps(compute(), cls=DjangoJSONEncoder))
    try:
        _cache().set(key, value, TTL[namespace])
    except Exception:
        logger.debug("could not cache %s", key, exc_info=True)
    return value


def invalidate(*namespaces):
    """Drop every cached aggregate in the given namespaces."""
    cache = _cache()
    for namespace in namespaces:
        key = f"agg:{namespace}:gen"
        try:
            try:
                cache.incr(key)
            except ValueError:  # generation counter not set yet
                cache.set(key, 1, None)
        except Exception:
            logger.debug("could not invalidate %s", namespace, exc_info=True)


# model label -> namespaces whose aggregates read that model
INVALIDATES = {
    "Inventory.Equipment": ("dash", "ppm", "cal", "inv"),
    "Inventory.Department": ("dash", "ppm", "cal", "inv"),
    "workshop.Workshop": ("dash", "ppm", "cal", "rpt", "inv"),
    "jobcard.jobcard": ("dash", "rpt", "inv"),
    "ppms.PPMSchedule": ("dash", "ppm"),
    "parts_tools.Accessories": ("dash",),
    "parts_tools.Tools": ("dash",),
    "reporthub.Report": ("dash", "rpt"),
    "calSchedules.CalibrationSchedule": ("cal",),
    "CalSoft.CalibrationSession": ("cal",),
}


def connect_invalidation_signals():
    from django.apps import apps
    from django.db.models.signals import post_delete, post_save

    for label, namespaces in INVALIDATES.items():
        model = apps.get_model(label)

        def handler(sender, _namespaces=namespaces, **kwargs):
            invalidate(*_namespaces)

        uid = f"core.aggregate_cache:{label}"
        post_save.connect(handler, sender=model, weak=False, dispatch_uid=uid + ":save")
        post_delete.connect(handler, sender=model, weak=False, dispatch_uid=uid + ":delete")
