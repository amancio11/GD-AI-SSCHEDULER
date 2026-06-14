"""Celery application configuration.

Import this module to get the configured Celery app:
    from celery_worker import celery_app
"""
from __future__ import annotations

import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "scheduler_mes",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "app.core.scheduler.reschedule_engine",
        "app.core.ai.proactive_analyzer",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,            # acknowledge after completion (idempotency)
    worker_prefetch_multiplier=1,   # one task at a time per worker
    task_max_retries=3,
    task_default_retry_delay=5,     # seconds between retries
)

if __name__ == "__main__":
    celery_app.start()
