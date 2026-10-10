"""Celery application definition.

Redis is used both as broker and as result backend. Task modules are imported
lazily via ``imports`` to avoid a circular import with ``worker.tasks``.
"""

import threading

import redis
from celery import Celery
from celery.signals import worker_ready, worker_shutdown

from app.config import get_settings
from app.logging import get_logger

settings = get_settings()

celery_app = Celery(
    "pdf2md",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    imports=["app.worker.tasks"],
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,  # re-deliver the task if the worker crashes mid-run
    worker_prefetch_multiplier=1,  # fair dispatch across workers
)


# Celery's main worker process refreshes a shared heartbeat independently of jobs.
# The metrics API reports unhealthy if all workers stop for more than 30 seconds.
_heartbeat_stop = threading.Event()
_heartbeat_thread: threading.Thread | None = None
_heartbeat_log = get_logger("worker.heartbeat")


@worker_ready.connect
def start_heartbeat(**kwargs: object) -> None:
    global _heartbeat_thread
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        return
    _heartbeat_stop.clear()

    def beat() -> None:
        client = redis.from_url(get_settings().redis_url)
        try:
            while not _heartbeat_stop.is_set():
                try:
                    client.set("pdf2md:metrics:worker:heartbeat", "1", ex=30)
                except Exception:
                    _heartbeat_log.warning("worker_heartbeat_failed")
                _heartbeat_stop.wait(10)
        finally:
            client.close()

    _heartbeat_thread = threading.Thread(target=beat, daemon=True, name="worker-heartbeat")
    _heartbeat_thread.start()


@worker_shutdown.connect
def stop_heartbeat(**kwargs: object) -> None:
    _heartbeat_stop.set()
    if _heartbeat_thread:
        _heartbeat_thread.join(timeout=2)
