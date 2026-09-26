from django.db import models


class SiteProfile(models.Model):
    """This hospital's details, printed on pages, reports and certificates.

    One row per site, edited by the head of department on the Site details
    page. Blank fields fall back to config.json (client.name / client.email /
    client.phone), so an install that never visits the page still shows the
    name it was set up with. Read through core.branding.
    """
    name = models.CharField(max_length=150, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=60, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to="branding/", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1  # one row per site
        super().save(*args, **kwargs)
        from core import branding

        branding.clear_cache()

    @classmethod
    def load(cls):
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    def __str__(self):
        return self.name or "Site profile"
