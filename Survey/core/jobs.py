"""Queue model work for the independent worker; the web process never calls Tripo."""
from __future__ import annotations

from .model_queue import enqueue


def dispatch_model_job(session_id: str) -> str:
    return enqueue(session_id)


def retry(session_id: str) -> str:
    # Saved external task IDs and downloaded artifacts are preserved.
    return enqueue(session_id)
