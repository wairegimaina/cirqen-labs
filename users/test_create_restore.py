"""Creating a user emails them through the HOD's Email settings; an email
that belongs to a deleted user offers to restore that account."""
import json

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from notifications.models import EmailOutbox, EmailSettings
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CreateAndRestoreUserTests(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Lab1")
        self.hod = User.objects.create_user(username="hod", password="x", email="hod@test.com")
        UserProfile.objects.update_or_create(user=self.hod, defaults={'role': 'HOD', 'must_change_password': False})
        self.client.force_login(self.hod)

    def _post(self, url, **extra):
        body = {'firstName': 'Jane', 'lastName': 'Doe', 'email': 'jane@test.com', 'role': 'Tech',
                'workshop': str(self.workshop.id), 'level': 'Engineer', **extra}
        return self.client.post(url, json.dumps(body), content_type='application/json')

    def test_welcome_email_uses_the_hod_email_settings(self):
        EmailSettings(host='smtp.example.com', port=2525, username='sender@example.com', password='pw',
                      from_email='cirqen@example.com').save()
        sent = []

        def fake_connection(server):
            sent.append(server)
            from django.core.mail import get_connection
            return get_connection('django.core.mail.backends.locmem.EmailBackend')

        from unittest import mock
        with mock.patch('notifications.mailer.connection_for', side_effect=fake_connection):
            resp = self._post(reverse('api_create_user'))
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.json()['emailSent'])
        self.assertEqual(sent[0].host, 'smtp.example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, 'cirqen@example.com')
        self.assertEqual(mail.outbox[0].to, ['jane@test.com'])

    @override_settings(EMAIL_HOST_USER='')
    def test_unsent_welcome_email_waits_in_the_outbox(self):
        resp = self._post(reverse('api_create_user'))
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(resp.json()['emailSent'])
        self.assertEqual(EmailOutbox.objects.filter(kind='welcome', to='jane@test.com').count(), 1)

    def test_deleted_users_email_offers_restore(self):
        old = User.objects.create_user(username="jane1234", password="x", email="Jane@Test.com",
                                       active_status=False)
        UserProfile.objects.update_or_create(user=old, defaults={'role': 'HOD'})

        resp = self._post(reverse('api_create_user'))
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.json()['canRestore'])
        self.assertEqual(resp.json()['deletedUser']['id'], str(old.id))

        resp = self._post(reverse('api_restore_user', args=[old.id]))
        self.assertTrue(resp.json()['success'])
        old.refresh_from_db()
        self.assertTrue(old.active_status)
        self.assertEqual(old.userprofile.role, 'Tech')
        self.assertEqual(old.userprofile.workshop, self.workshop)
        self.assertTrue(old.userprofile.must_change_password)
        self.assertTrue(old.check_password(resp.json()['user']['temporaryPassword']))
        self.assertEqual(User.objects.filter(email__iexact='jane@test.com').count(), 1)

    def test_active_users_email_is_still_refused(self):
        User.objects.create_user(username="jane1", password="x", email="jane@test.com")
        resp = self._post(reverse('api_create_user'))
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn('canRestore', resp.json())
