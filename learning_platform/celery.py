import os
from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")

app = Celery("learning_platform")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()



@worker_ready.connect
def recover_quiz_jobs_on_start(**kwargs):
    app.send_task("management_system.tasks.recover_quiz_operations")
    app.send_task("management_system.tasks.reconcile_closed_quiz_snapshots")
