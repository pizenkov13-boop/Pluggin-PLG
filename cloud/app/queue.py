"""LLM generation queue — per-process semaphore or Redis slots across replicas."""

from __future__ import annotations

import threading
from contextlib import contextmanager

from fastapi import HTTPException

from cloud.app.config import LLM_QUEUE_SLOTS, LLM_QUEUE_TIMEOUT_SEC, LLM_REDIS_SLOT_KEY
from cloud.app import redis_store

_local_slots = threading.Semaphore(LLM_QUEUE_SLOTS)


@contextmanager
def llm_slot():
    if redis_store.redis_available():
        if not redis_store.incr_slot(LLM_REDIS_SLOT_KEY, LLM_QUEUE_SLOTS):
            raise HTTPException(
                503,
                "Server is busy generating beats. Please wait a moment and try again.",
            )
        try:
            yield
        finally:
            redis_store.decr_slot(LLM_REDIS_SLOT_KEY)
        return

    acquired = _local_slots.acquire(timeout=LLM_QUEUE_TIMEOUT_SEC)
    if not acquired:
        raise HTTPException(
            503,
            "Server is busy generating beats. Please wait a moment and try again.",
        )
    try:
        yield
    finally:
        _local_slots.release()
