"""Who receives, and who is copied on, each email.

The copy rule (requested by the hospital): mail to anyone in a workshop or
department (Tech, In-Charge) copies the HOD. Mail to the HOD copies nobody.
"""
from django.contrib.auth import get_user_model

from users.models import UserProfile

User = get_user_model()


def _reachable(profiles):
    return (
        profiles.filter(active_status=True, user__is_active=True)
        .exclude(user__email__isnull=True).exclude(user__email='')
        .select_related('user')
    )


def email_of(user):
    return (user.email or '').strip() if user and user.is_active else ''


def hods():
    return [p.user for p in _reachable(UserProfile.objects.filter(role='HOD'))]


def workshop_staff(workshop):
    """Everyone working in ``workshop`` (with an email address)."""
    return [p.user for p in _reachable(UserProfile.objects.filter(workshop=workshop).exclude(role='HOD'))]


def department_in_charges(department):
    return [p.user for p in _reachable(UserProfile.objects.filter(role='NIC', department=department))]


def cc_for(user):
    """Users to copy on mail addressed to ``user``."""
    profile = getattr(user, 'userprofile', None)
    if profile is None or profile.role == 'HOD':
        return []
    return [u for u in hods() if u.pk != user.pk]


def addresses(to_users, copy_rule=True, exclude=()):
    """(to, cc) address lists for one message to ``to_users``, copy rule applied.

    Nobody appears twice, nobody is copied on mail they already receive, and
    users whose pk is in ``exclude`` (whoever did the thing) are left out.
    """
    to, seen = [], set()
    for user in to_users:
        addr = email_of(user)
        if addr and addr.lower() not in seen and user.pk not in exclude:
            seen.add(addr.lower())
            to.append(addr)
    cc = []
    if not copy_rule:
        return to, cc
    for user in to_users:
        for copy in cc_for(user):
            if copy.pk in exclude:
                continue
            addr = email_of(copy)
            if addr and addr.lower() not in seen:
                seen.add(addr.lower())
                cc.append(addr)
    return to, cc
