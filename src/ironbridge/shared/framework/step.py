"""
@step - mark a function as a durable, retriable unit of work.

Framework primitive. No DBOS dependency. The execution backend
(DBOS, Temporal, plain async, Postgres job queue) is pluggable
via the derive layer.

    @step()
    async def send_email(grant_id: str, body: str):
        ...

    @step(retries=5, backoff=2.0, interval=120)
    async def call_external_api(url: str, payload: dict):
        ...

The derive layer reads _is_step and _step_config and wraps
with whatever backend is configured.
"""
from __future__ import annotations

from typing import Any, Callable


def step(
    retries: int = 0,
    backoff: float = 1.0,
    interval: int = 1,
) -> Callable:
    """
    Mark a function as a durable step.

    Without a backend: runs as a normal function call.
    With DBOS: wrapped in @DBOS.step() for exactly-once + retry.
    With Temporal: wrapped as an activity.

    Args:
        retries: max retry attempts on failure (0 = no retry)
        backoff: exponential backoff multiplier
        interval: initial interval between retries in seconds
    """
    def decorator(fn: Callable) -> Callable:
        fn._is_step = True
        fn._step_config = {
            "retries": retries,
            "backoff": backoff,
            "interval": interval,
        }
        return fn
    return decorator


def is_step_fn(fn: Any) -> bool:
    return getattr(fn, "_is_step", False)


def get_step_config(fn: Any) -> dict | None:
    return getattr(fn, "_step_config", None)
