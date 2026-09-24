"""
East Africa Time for everything a person reads: PDFs, exports, messages.

The database stores and returns timestamps in UTC (USE_TZ = True). Django's
template filters convert them for display, but any Python that calls
.strftime() on a model timestamp prints UTC -- three hours behind -- and
timezone.now().date() is the UTC calendar day, which is yesterday between
midnight and 03:00 in Nairobi. Route display code through these helpers.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

EAT = ZoneInfo("Africa/Nairobi")


def now_eat() -> datetime:
    """Current time in EAT, independent of the machine's clock zone."""
    return timezone.now().astimezone(EAT)


def today_eat() -> date:
    """Today's date in Nairobi."""
    return now_eat().date()


def to_eat(value):
    """Convert an aware datetime to EAT.

    Naive datetimes are taken to be EAT already (that is what TIME_ZONE says
    they mean); dates and times pass through unchanged.
    """
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return value.replace(tzinfo=EAT)
        return value.astimezone(EAT)
    return value


def fmt_eat(value, fmt="%Y-%m-%d %H:%M", default="N/A") -> str:
    """strftime in EAT; `default` when value is empty."""
    if not value:
        return default
    return to_eat(value).strftime(fmt)
