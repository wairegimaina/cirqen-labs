"""Outgoing email, queued locally.

Email is queued here and sent by the ``notifications.tasks.flush_outbox``
beat task, so a request never waits on SMTP and a message written while the
clinic's internet is down goes out when it comes back.

Deliberately NOT a synced table (it is not in config.py ``sync_tables``):
every desktop keeps its own queue, which is what stops 500 machines each
sending the same message.
"""
from django.db import models


class EmailOutbox(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]
    MAX_ATTEMPTS = 8

    kind = models.CharField(max_length=50)
    # Same key = same message; queueing it twice is a no-op.
    dedupe_key = models.CharField(max_length=200, unique=True)
    to = models.TextField(help_text="Comma-separated addresses")
    cc = models.TextField(blank=True, help_text="Comma-separated addresses")
    subject = models.CharField(max_length=255)
    body_text = models.TextField()
    body_html = models.TextField(blank=True)

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
