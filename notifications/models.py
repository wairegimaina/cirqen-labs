"""Outgoing email, queued locally, and how this PC sends it.

Email is queued here and sent by the ``notifications.tasks.flush_outbox``
beat task, so a request never waits on SMTP and a message written while the
clinic's internet is down goes out when it comes back (for up to
OUTBOX_KEEP_DAYS).

Neither table is synced (they are not in config.py ``sync_tables``): every
desktop keeps its own queue, which is what stops 500 machines each sending
the same message, and the mail password never leaves the PC it was typed on.
"""
from django.conf import settings
from django.db import models


class EmailOutbox(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Waiting to send'),
        ('sent', 'Sent'),
        ('failed', 'Refused'),
        ('expired', 'Expired unsent'),
        ('cancelled', 'Discarded'),
    ]
    # Attempts the mail server actually refused. Being offline is not counted.
    MAX_ATTEMPTS = 8

    kind = models.CharField(max_length=50)
    # Same key = same message; queueing it twice is a no-op.
    dedupe_key = models.CharField(max_length=200, unique=True)
    to = models.TextField(help_text="Comma-separated addresses")
    cc = models.TextField(blank=True, help_text="Comma-separated addresses")
    subject = models.CharField(max_length=255)
    body_text = models.TextField()
    body_html = models.TextField(blank=True)
    # One optional file (a report PDF), kept with the message until it is sent.
    attachment = models.BinaryField(null=True, blank=True)
    attachment_name = models.CharField(max_length=150, blank=True)
    attachment_type = models.CharField(max_length=100, blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    def to_list(self):
        return [a for a in self.to.split(',') if a]

    def cc_list(self):
        return [a for a in self.cc.split(',') if a]

    def __str__(self):
        return f"{self.kind} → {self.to} ({self.status})"

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Email outbox'


class EmailSettings(models.Model):
    """How this PC sends email, set by the HOD on Settings > Email.

    One row per PC. When ``username`` is empty the values from config.json /
    the environment (Django's EMAIL_* settings) are used instead, so existing
    installs keep working unchanged.
    """
    host = models.CharField(max_length=200, default='smtp.gmail.com')
    port = models.PositiveIntegerField(default=587)
    security = models.CharField(max_length=8, default='starttls',
                                choices=[('starttls', 'STARTTLS (port 587)'), ('ssl', 'SSL/TLS (port 465)'),
                                         ('none', 'None')])
    username = models.CharField(max_length=254, blank=True)
    password = models.CharField(max_length=255, blank=True)
    from_email = models.EmailField(blank=True, help_text="Sender address; the username when blank.")
    # One PC per site sends the scheduled mail (digest, reminders, monthly
    # report); with it on everywhere every person would get one copy per PC.
    is_site_sender = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    def __str__(self):
        return "Email settings"
