"""Every scheduled Celery job names a task that exists.

Celery beat enqueues by name and a worker silently drops unknown names, so a
typo means a job that never runs. Five PPM jobs were scheduled as
"PPM.tasks.*" (the app label is "ppms") and never ran.
"""
from django.test import SimpleTestCase

from Equiper.celery import app


class BeatScheduleTests(SimpleTestCase):
    def test_every_scheduled_task_is_registered(self):
        app.loader.import_default_modules()
        missing = {name: entry["task"] for name, entry in app.conf.beat_schedule.items()
                   if entry["task"] not in app.tasks}
        self.assertEqual(missing, {})
