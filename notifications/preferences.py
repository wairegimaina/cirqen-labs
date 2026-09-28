"""Which emails a person wants (My email page). Bell notifications always come.

Each outbox ``kind`` belongs to a category a person can switch off, except
the compulsory ones: mail that asks this person to do something only they
can do.
"""

# (key, label, what it covers, kinds, can be switched off)
CATEGORIES = [
    ("stock", "Stock running low",
     "A part in your workshop at or below its lower limit.", ("stock_low",), True),
    ("accessories", "Accessory requests",
     "Requests to approve (HOD), decisions on your requests, and parts received.",
     ("accessory_requested", "accessory_decided", "accessory_received"), True),
    ("reports", "Reports",
     "Weekly reports submitted (HOD) and the monthly maintenance report.",
     ("report_submitted", "monthly_report"), True),
    ("digest", "Daily to-do list",
     "The morning summary of what is due or overdue.", ("daily_digest",), True),
    ("alerts", "Other alerts",
     "Reference standards due for calibration, service contracts ending.", ("notify",), True),
    ("work_orders", "Work orders",
     "Only if the site has switched work-order email on.", ("work_order_submitted", "work_order_decided"), True),
    ("reminders", "Reminders to act",
     "Your workshop's weekly report is late. Cannot be switched off.", ("report_reminder",), False),
]

KIND_TO_CATEGORY = {kind: key for key, _, _, kinds, _ in CATEGORIES for kind in kinds}
MUTABLE = {key for key, _, _, _, mutable in CATEGORIES if mutable}


def category(kind):
    return KIND_TO_CATEGORY.get(kind)


def wants(user, kind):
    """Whether ``user`` gets email of this kind (False only if they switched it off)."""
    key = category(kind)
    if key is None or key not in MUTABLE:
        return True
    muted = getattr(getattr(user, "userprofile", None), "email_muted", None) or []
    return key not in muted


def rows(user):
    """The My email page: each category with whether it is on."""
    muted = set(getattr(getattr(user, "userprofile", None), "email_muted", None) or [])
    return [{"key": key, "label": label, "help": help_text, "mutable": mutable, "on": key not in muted}
            for key, label, help_text, _, mutable in CATEGORIES]
