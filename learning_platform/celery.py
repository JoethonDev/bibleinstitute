import logging
import os
import threading

from celery import Celery
from celery.signals import worker_ready, worker_shutting_down

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")

app = Celery("learning_platform")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

logger = logging.getLogger(__name__)

MEDIA_STARTUP_RECOVERY_KEY = "media:startup-recovery"
LIVENESS_REFRESH_SECONDS = 15

_liveness_stop = threading.Event()
_liveness_thread: threading.Thread | None = None


@worker_ready.connect
def recover_quiz_jobs_on_start(**kwargs):
    app.send_task("management_system.tasks.recover_quiz_operations")
    app.send_task("management_system.tasks.reconcile_closed_quiz_snapshots")
    start_worker_liveness()
    _recover_media_jobs_on_start()


def start_worker_liveness() -> None:
    """Publish this worker's liveness mark and keep refreshing it."""
    global _liveness_thread
    from management_system.media_tasks import mark_media_worker_alive

    mark_media_worker_alive()
    if _liveness_thread is not None and _liveness_thread.is_alive():
        return
    _liveness_stop.clear()

    def refresh() -> None:
        while not _liveness_stop.wait(LIVENESS_REFRESH_SECONDS):
            mark_media_worker_alive()

    _liveness_thread = threading.Thread(
        target=refresh,
        name="media-worker-liveness",
        daemon=True,
    )
    _liveness_thread.start()


def stop_worker_liveness() -> None:
    """Stop refreshing this worker's liveness mark."""
    _liveness_stop.set()


def _recover_media_jobs_on_start() -> None:
    """Dispatch queued and orphaned media jobs as soon as a worker is ready.

    After a redeploy the released jobs are queued with no dispatch timestamp,
    and upload acknowledgements may also have expired. The guard key keeps the
    worker containers that start in one deploy from each enqueueing the same
    recovery pass; the pass itself is idempotent and bounded.
    """
    try:
        import redis
        from django.conf import settings

        guard = redis.Redis.from_url(
            getattr(settings, "CELERY_BROKER_URL", "redis://redis:6379/0"),
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        if not guard.set(MEDIA_STARTUP_RECOVERY_KEY, "1", nx=True, ex=20):
            return
    except Exception:
        logger.warning("media startup recovery guard unavailable", exc_info=True)
    try:
        app.send_task(
            "management_system.media_tasks.recover_pending_media_jobs",
            kwargs={"limit": 100},
            queue="media",
        )
    except Exception:
        logger.warning("media startup recovery dispatch failed", exc_info=True)


@worker_shutting_down.connect
def release_media_jobs_on_shutdown(**kwargs):
    """Release this worker's in-flight media jobs before the container exits.

    Docker sends SIGTERM on redeploy; the released rows become immediately
    dispatchable again and the next worker's startup recovery picks them up.
    The working tasks notice the status change through their lease check and
    stop their FFmpeg child, so no output is attached twice.
    """
    try:
        from management_system.media_processing import media_worker_id, release_worker_jobs
        from management_system.media_tasks import clear_media_progress, forget_media_worker

        stop_worker_liveness()
        released = release_worker_jobs(media_worker_id())
        forget_media_worker(media_worker_id())
        if not released:
            return
        clear_media_progress(released)
        logger.info(
            "released media jobs on worker shutdown",
            extra={
                "event": "media_jobs_released_on_shutdown",
                "count": len(released),
            },
        )
    except Exception:
        logger.warning("media job release on worker shutdown failed", exc_info=True)
