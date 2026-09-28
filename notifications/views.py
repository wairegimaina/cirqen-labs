"""Settings > Email: how this PC sends mail, a test, and the outbox."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from users.control import hod_required

from . import mailer
from .models import EmailOutbox, EmailSettings


class EmailSettingsForm(forms.ModelForm):
    password = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the saved password. For Gmail, an app password.")

    class Meta:
        model = EmailSettings
        fields = ["host", "port", "security", "username", "password", "from_email", "is_site_sender"]
        labels = {"host": "Mail server", "username": "Account (email address)", "from_email": "Send as",
                  "is_site_sender": "This PC sends the site's scheduled email"}

    def clean_password(self):
        # A blank box keeps the saved password: it is never shown back.
        return self.cleaned_data.get("password") or self.instance.password


@login_required
@hod_required
def email_settings(request):
    row = EmailSettings.load()
    form = EmailSettingsForm(instance=row)
    if request.method == "POST":
        form = EmailSettingsForm(request.POST, instance=row)
        if form.is_valid():
            saved = form.save(commit=False)
            saved.updated_by = request.user
            saved.save()
            messages.success(request, "Email settings saved.")
            return redirect("notifications:email_settings")
    server = mailer.smtp()
    counts = dict(EmailOutbox.objects.values_list("status").annotate(n=Count("id")))
    return render(request, "notifications/pages/email_settings.html", {
        "form": form, "server": server, "row": row, "counts": counts,
        "problems": counts.get("failed", 0) + counts.get("expired", 0),
        "last_sent": EmailOutbox.objects.filter(status="sent").aggregate(t=Max("sent_at"))["t"],
        "last_error": (EmailOutbox.objects.filter(status="pending").exclude(last_error="")
                       .order_by("-created_at").values_list("last_error", flat=True).first()),
        "is_sender": mailer.is_site_sender(),
        "keep_days": mailer.keep_days(),
    })


@login_required
@hod_required
@require_POST
def send_test_email(request):
    to = (request.POST.get("to") or request.user.email or "").strip()
    if not to:
        messages.error(request, "Give an address to send the test to (your account has none).")
        return redirect("notifications:email_settings")
    server = mailer.smtp()
    if not server.configured:
        messages.error(request, "Save the mail server and account first.")
        return redirect("notifications:email_settings")
    try:
        mailer.send_test(to, server)
    except Exception as exc:
        messages.error(request, f"The test email was not sent: {exc}")
    else:
        messages.success(request, f"Test email sent to {to}. Check the inbox (and the spam folder).")
    return redirect("notifications:email_settings")


@login_required
@hod_required
def outbox(request):
    status = request.GET.get("status") or ""
    q = (request.GET.get("q") or "").strip()
    rows = EmailOutbox.objects.defer("body_text", "body_html", "attachment")
    if status in dict(EmailOutbox.STATUS_CHOICES):
        rows = rows.filter(status=status)
    if q:
        # Find the emails about something: a part, a request, a workshop, a person.
        rows = rows.filter(Q(subject__icontains=q) | Q(to__icontains=q) | Q(cc__icontains=q))
    return render(request, "notifications/pages/outbox.html", {
        "page": Paginator(rows.order_by("-created_at"), 50).get_page(request.GET.get("page")),
        "status": status, "statuses": EmailOutbox.STATUS_CHOICES, "q": q,
        "counts": dict(EmailOutbox.objects.values_list("status").annotate(n=Count("id"))),
    })


@login_required
@hod_required
@require_POST
def outbox_action(request, pk):
    msg = EmailOutbox.objects.filter(pk=pk).first()
    action = request.POST.get("action")
    if msg is None:
        messages.error(request, "That message no longer exists.")
    elif action == "retry" and msg.status in ("failed", "expired", "cancelled", "pending"):
        # A fresh start, including the keep-for-N-days clock, or an expired
        # message would expire again on the next run.
        EmailOutbox.objects.filter(pk=msg.pk).update(status="pending", attempts=0, next_attempt_at=None,
                                                     created_at=timezone.now())
        messages.success(request, "It will be sent within a minute when the PC is online.")
    elif action == "discard" and msg.status in ("pending", "failed", "expired"):
        msg.status = "cancelled"
        msg.save(update_fields=["status"])
        messages.success(request, "Message discarded.")
    else:
        messages.error(request, "That cannot be done to this message.")
    target = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = reverse("notifications:outbox")
    return redirect(target)


@login_required
def my_email(request):
    """Which emails I get. Everyone; the bell notifications always come."""
    from users.models import UserProfile

    from . import preferences

    profile = getattr(request.user, "userprofile", None)
    if profile is None:
        messages.error(request, "Your account has no profile.")
        return redirect("dashboard:dashboard-main")
    if request.method == "POST":
        wanted = set(request.POST.getlist("on"))
        muted = sorted(k for k in preferences.MUTABLE if k not in wanted)
        # update(), not save(): the profile's full_clean has nothing to do with
        # this, and updated_at moves so the choice syncs to every PC.
        UserProfile.objects.filter(pk=profile.pk).update(email_muted=muted, updated_at=timezone.now(),
                                                         needs_sync=True)
        messages.success(request, "Your email choices are saved. Notifications in Cirqen still come.")
        return redirect("notifications:my_email")
    return render(request, "notifications/pages/my_email.html", {
        "rows": preferences.rows(request.user), "address": request.user.email,
    })
