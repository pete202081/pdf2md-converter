"""Celery application definition.

Redis is used both as broker and as result backend. Task modules are imported
lazily via ``imports`` to avoid a circular import with ``worker.tasks``.
"""

from celery import Celery

from app.config import get_settings

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
