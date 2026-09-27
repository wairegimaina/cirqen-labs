"""Who receives, and who is copied on, each email.

The copy rule (requested by the hospital):
  * mail to anyone in a workshop or department (Tech, In-Charge) → copy the HOD
  * mail to the HOD → copy the Deputy HOD (UserProfile.is_deputy_hod)
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


def deputy_hods():
    return [p.user for p in _reachable(UserProfile.objects.filter(is_deputy_hod=True))]


def department_in_charges(department):
    return [p.user for p in _reachable(UserProfile.objects.filter(role='NIC', department=department))]


def cc_for(user):
    """Users to copy on mail addressed to ``user``."""
    profile = getattr(user, 'userprofile', None)
    if profile is None:
        return []
    copies = deputy_hods() if profile.role == 'HOD' else hods()
    return [u for u in copies if u.pk != user.pk]


def addresses(to_users):
    """(to, cc) address lists for one message to ``to_users``, copy rule applied.

    Nobody appears twice, and nobody is copied on mail they already receive.
    """
    to, seen = [], set()
    for user in to_users:
        addr = email_of(user)
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            to.append(addr)
    cc = []
    for user in to_users:
        for copy in cc_for(user):
            addr = email_of(copy)
            if addr and addr.lower() not in seen:
                seen.add(addr.lower())
                cc.append(addr)
    return to, cc
