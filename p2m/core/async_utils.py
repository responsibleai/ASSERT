"""Small asyncio helpers for bounded workflow fan-out."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

log = logging.getLogger(__name__)

ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


async def invoke_callable(
    fn: Any,
    *args: Any,
    timeout_s: float | None = None,
    **kwargs: Any,
) -> Any:
    """Invoke a callable uniformly whether it is sync or async."""

    async def _call() -> Any:
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    if timeout_s is None:
        return await _call()
    return await asyncio.wait_for(_call(), timeout=timeout_s)


async def gather_limited(
    items: Sequence[ItemT],
    *,
    limit: int,
    worker: Callable[[ItemT], Awaitable[ResultT]],
) -> list[ResultT]:
    """Run async work with bounded concurrency while preserving input order."""
    if not items:
        return []

    semaphore = asyncio.Semaphore(max(1, min(limit, len(items))))

    async def _guard(item: ItemT) -> ResultT:
        async with semaphore:
            return await worker(item)

    return await asyncio.gather(*(_guard(item) for item in items))


@contextlib.asynccontextmanager
async def log_heartbeat(message: str, *, interval: float = 15.0):
    """Async context manager that logs a heartbeat while a long operation runs.

    Usage::

        async with log_heartbeat("Converting policy"):
            result = await slow_llm_call(...)

    Logs "{message} (still working, {elapsed}s elapsed)" every *interval*
    seconds until the block exits.
    """
    start = time.monotonic()

    async def _beat():
        while True:
            await asyncio.sleep(interval)
            elapsed = time.monotonic() - start
            log.info(f"{message} (still working, {elapsed:.0f}s elapsed)")

    task = asyncio.create_task(_beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
