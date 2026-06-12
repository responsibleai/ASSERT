# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Runtime safety: heartbeat + watchdog daemons + bounded stage teardown.

These primitives defend the harness against well-meaning user targets that
leave background resources open (unclosed ``httpx`` clients, OpenTelemetry
exporters, etc.). The motivating bug: a multi-agent LangGraph travel-planner
target leaked an ``AzureChatOpenAI`` (and its inner ``httpx.AsyncClient``) per
node per scenario; at high concurrency the per-stage ``asyncio.run`` teardown
deadlocked inside ``loop.shutdown_default_executor`` waiting for worker
threads stuck in cleanup finalizers, freezing the pipeline between inference
and judge.

The three layers exposed here:

* :class:`ManifestHeartbeat` — daemon thread that rewrites
  ``manifest.heartbeat_at`` (and an optional progress payload) every
  ``interval_s`` seconds so external observers (``assert-ai results status``,
  benchmark dashboards) get an honest liveness signal during long stages.
* :class:`PipelineWatchdog` — daemon thread that dumps every Python thread's
  current stack to the log if the pipeline goes silent (no
  :meth:`tick` call) for longer than ``idle_threshold_s`` seconds, so a hang
  is self-diagnosing on its next occurrence.
* :func:`run_stage_coro` — drop-in replacement for ``asyncio.run`` whose
  teardown is bounded by ``cleanup_timeout_s``; if it exceeds the bound, the
  custom default executor is detached from the interpreter's atexit join
  hook and abandoned so the pipeline can move on to the next stage.

All three accept the runtime cost of being slightly defensive: bounded
cleanup may leave dangling worker threads (detached from both interpreter
shutdown joins so they do not block process exit, but still consuming
memory until the process terminates), the watchdog may emit a stack dump
during a legitimately slow stage, and the heartbeat adds one tiny atomic
write every 30s. We accept those costs in exchange for "the benchmark
always finishes" as the harness contract.
"""

from __future__ import annotations

import asyncio
import concurrent.futures.thread as _cft
import logging
import sys
import threading
import time
import traceback
import weakref
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Manifest heartbeat
# ---------------------------------------------------------------------------


class ManifestHeartbeat:
    """Periodically rewrite the run manifest from a daemon thread.

    The runner only rewrites ``manifest.json`` at stage boundaries, which
    means during a 90-minute inference stage there is no liveness signal at all —
    ``manifest.heartbeat_at`` can be stale by 1.5 hours while the run is
    perfectly healthy. This heartbeat fixes that by rewriting the manifest
    every ``interval_s`` seconds with the current timestamp and an optional
    progress payload that stages can update (e.g. inference reports
    ``{stage: "inference", completed: 423, total: 1000}``).

    Thread safety:

    * The manifest dataclass is mutated under ``_lock`` so concurrent
      ``set_progress`` calls don't race with the heartbeat's read.
    * ``write_json`` performs a temp-file + rename atomic write, so the
      main runner thread and the heartbeat thread can both call
      ``write_manifest`` safely (last-write-wins on the bytes; no
      corruption).
    """

    def __init__(
        self,
        manifest: Any,
        run_root: Path,
        write_fn: Callable[[Any, Path], None],
        *,
        interval_s: float = 30.0,
    ) -> None:
        self._manifest = manifest
        self._run_root = Path(run_root)
        self._write_fn = write_fn
        self._interval_s = max(0.01, float(interval_s))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._watchdog: PipelineWatchdog | None = None

    def attach_watchdog(self, watchdog: "PipelineWatchdog") -> None:
        """Have each successful heartbeat tick double as a watchdog tick.

        This means: if the heartbeat thread is alive and writing, the
        watchdog stays quiet. If the heartbeat thread dies or stops
        writing (e.g. the manifest write itself wedges), the watchdog
        eventually fires. Stages can still tick the watchdog directly
        for finer-grained signals.
        """
        self._watchdog = watchdog

    def set_progress(self, **fields: Any) -> None:
        """Update the progress payload that will be written on the next tick.

        Pass any JSON-serializable keys (``stage``, ``completed``, ``total``,
        anything else useful). Existing keys are merged; pass ``None`` to
        leave them. Safe to call from any thread.
        """
        if not fields:
            return
        with self._lock:
            current: dict[str, Any] = dict(getattr(self._manifest, "progress", None) or {})
            for k, v in fields.items():
                current[k] = v
            try:
                setattr(self._manifest, "progress", current)
            except AttributeError:
                pass

    def clear_progress(self) -> None:
        """Reset the progress payload (e.g. between stages)."""
        with self._lock:
            try:
                setattr(self._manifest, "progress", None)
            except AttributeError:
                pass

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="assert-ai-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, write_final: bool = False) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=5.0)
        self._thread = None
        if write_final:
            self._safe_write()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            wrote = self._safe_write()
            if wrote and self._watchdog is not None:
                self._watchdog.tick()

    def _safe_write(self) -> bool:
        try:
            with self._lock:
                # _write_manifest itself stamps heartbeat_at on every call
                # when manifest.status == "running"; we just trigger it.
                self._write_fn(self._manifest, self._run_root)
            return True
        except Exception:  # noqa: BLE001 — heartbeat must never raise
            log.debug("Heartbeat write failed", exc_info=True)
            return False


# ---------------------------------------------------------------------------
# Pipeline watchdog
# ---------------------------------------------------------------------------


class PipelineWatchdog:
    """Dump every thread's stack if the pipeline goes silent for too long.

    Purpose: make the *next* hang self-diagnosing. Past hangs forced us to
    install ``py-spy`` after the fact; with this watchdog wired in, the
    bench log itself will contain a stack trace of every live thread
    pointing at the offending wait/lock/syscall.

    Mechanics:

    * Caller ticks via :meth:`tick` to signal "still alive". Stages can
      tick directly; the :class:`ManifestHeartbeat` is also wired to tick
      on each successful manifest write.
    * Every ``check_interval_s`` seconds the watchdog measures wall time
      since the last tick. If it exceeds ``idle_threshold_s`` and we have
      not already dumped for this idle period, dump every thread's stack
      via :func:`sys._current_frames` and emit a warning.
    * Dumping is idempotent per idle period — a single hang produces one
      dump, not one per check.
    """

    def __init__(
        self,
        *,
        idle_threshold_s: float = 600.0,
        check_interval_s: float = 60.0,
    ) -> None:
        self._idle_threshold_s = max(0.01, float(idle_threshold_s))
        self._check_interval_s = max(0.01, float(check_interval_s))
        self._last_tick = time.monotonic()
        self._dumped_for_period = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        """Signal liveness. Resets the idle clock."""
        self._last_tick = time.monotonic()
        self._dumped_for_period = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="assert-ai-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=5.0)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._check_interval_s):
            idle = time.monotonic() - self._last_tick
            if idle > self._idle_threshold_s and not self._dumped_for_period:
                try:
                    self._dump_stacks(idle)
                except Exception:  # noqa: BLE001
                    log.debug("Watchdog stack dump failed", exc_info=True)
                self._dumped_for_period = True

    def _dump_stacks(self, idle_seconds: float) -> None:
        log.warning(
            "Pipeline watchdog: no liveness tick for %.0fs (threshold %.0fs). "
            "Dumping all thread stacks to help diagnose where the pipeline is stuck.",
            idle_seconds, self._idle_threshold_s,
        )
        name_by_id = {t.ident: t.name for t in threading.enumerate() if t.ident is not None}
        frames = sys._current_frames()
        for thread_id, frame in frames.items():
            name = name_by_id.get(thread_id, "?")
            log.warning("--- Thread %s (id=%d) ---", name, thread_id)
            stack = traceback.format_stack(frame)
            for line in stack:
                for sub in line.rstrip().splitlines():
                    log.warning("    %s", sub)


# ---------------------------------------------------------------------------
# Bounded stage teardown
# ---------------------------------------------------------------------------


class _AbandonableThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor variant for intentionally abandonable stage work.

    CPython 3.13+ moved the non-daemon thread join from Python-level
    ``threading._shutdown_locks`` to a C-level shutdown-handle list. That
    handle list is not removable from Python, so workers that may need to be
    abandoned must be daemon threads from the start. Normal teardown still
    waits for them via ``executor.shutdown(wait=True)``; daemon status only
    matters if bounded teardown times out and we intentionally abandon the
    executor.
    """

    def _adjust_thread_count(self) -> None:
        # This mirrors CPython's private ThreadPoolExecutor implementation,
        # except workers are daemon=True so interpreter shutdown does not join
        # an intentionally abandoned stage worker on Python 3.13+.
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_: weakref.ReferenceType[object], q: Any = self._work_queue) -> None:
            q.put(None)

        num_threads = len(self._threads)
        if num_threads >= self._max_workers:
            return

        thread_name = "%s_%d" % (self._thread_name_prefix or self, num_threads)
        t = threading.Thread(
            name=thread_name,
            target=_cft._worker,
            args=self._worker_args(weakref.ref(self, weakref_cb)),
            daemon=True,
        )
        t.start()
        self._threads.add(t)
        _cft._threads_queues[t] = self._work_queue

    def _worker_args(self, executor_ref: weakref.ReferenceType[object]) -> tuple[Any, ...]:
        # Python 3.14 changed concurrent.futures.thread._worker from
        # (executor_ref, work_queue, initializer, initargs) to
        # (executor_ref, worker_context, work_queue).
        create_context = getattr(self, "_create_worker_context", None)
        if create_context is not None:
            return (executor_ref, create_context(), self._work_queue)
        return (executor_ref, self._work_queue, self._initializer, self._initargs)


def _detach_executor_from_atexit(executor: ThreadPoolExecutor) -> None:
    """Detach worker threads from interpreter shutdown joins we can reach.

    Python 3.11 and 3.12 wait for non-daemon threads at process exit in two
    distinct places, and abandoning an executor cleanly requires detaching
    from both:

    1. ``concurrent.futures.thread._python_exit`` (atexit handler) joins
       every worker registered in ``_threads_queues``.
    2. ``threading._shutdown`` joins every non-daemon thread whose
       ``_tstate_lock`` is in ``threading._shutdown_locks``.

    Removing only ``_threads_queues`` is the common pitfall because the
    atexit handler is more obvious — but on Python 3.9+ (where TPE
    workers are non-daemon by default, per CPython issue 39812) the
    actually-blocking join is ``threading._shutdown``. Python 3.13+ moved
    that second join behind C-level thread handles, which cannot be removed
    from Python; :class:`_AbandonableThreadPoolExecutor` avoids that newer
    join path by creating daemon workers up front.

    The threads themselves continue to live (and may print "Event loop
    is closed" tracebacks as their resources GC against the closed
    loop) until the OS reaps them at process exit; we cannot stop them
    cleanly from Python.
    """
    try:
        threads_queues = getattr(_cft, "_threads_queues", None)
        if threads_queues is not None:
            for t in list(executor._threads):
                threads_queues.pop(t, None)
    except Exception:  # noqa: BLE001
        log.debug(
            "Failed to detach executor from concurrent.futures atexit",
            exc_info=True,
        )
    try:
        shutdown_locks = getattr(threading, "_shutdown_locks", None)
        shutdown_locks_lock = getattr(threading, "_shutdown_locks_lock", None)
        if shutdown_locks is None:
            return
        for t in list(executor._threads):
            lock = getattr(t, "_tstate_lock", None)
            if lock is None:
                continue
            if shutdown_locks_lock is not None:
                with shutdown_locks_lock:
                    shutdown_locks.discard(lock)
            else:
                shutdown_locks.discard(lock)
    except Exception:  # noqa: BLE001
        log.debug(
            "Failed to detach executor from threading._shutdown",
            exc_info=True,
        )


def run_stage_coro(
    coro: Coroutine[Any, Any, T],
    *,
    cleanup_timeout_s: float = 300.0,
    max_workers: int | None = None,
) -> T:
    """Run ``coro`` in a fresh event loop with bounded teardown.

    Drop-in replacement for ``asyncio.run(coro)`` that defends against the
    most common cause of pipeline hangs: a target callable that leaves
    background resources open (httpx clients, OTel exporters, etc.). When
    the coroutine returns, we shut down async generators and the loop's
    custom default executor inside a daemon thread with a bounded join.
    If the join exceeds ``cleanup_timeout_s``, the executor is detached
    from the interpreter's atexit join (so process exit isn't blocked
    either) and we proceed, logging a clear warning.

    Why a custom executor: by replacing the loop's default executor with
    one we own, we can detach it on timeout without affecting any other
    asyncio loop that may run later (the runner runs one of these per
    stage). The default ``asyncio`` executor is shared across all loops
    in the process, which makes it impossible to abandon safely.

    Tradeoff: on timeout, worker threads continue running until the
    process exits. They are daemons-by-effect (detached from BOTH the
    ``concurrent.futures`` atexit join AND ``threading._shutdown_locks``;
    see :func:`_detach_executor_from_atexit` for why both are required),
    so they cannot block process exit. They may print "Event loop is
    closed" tracebacks as their resources are GC'd against the
    now-defunct loop; the runner's stderr filter already suppresses
    these.
    """
    loop = asyncio.new_event_loop()
    if max_workers is None:
        # asyncio's default executor sizing (min(32, cpu_count + 4) since
        # Python 3.8). We mirror that, capped to a sane upper bound so a
        # 96-vCPU box doesn't spin up 100 worker threads for a tiny stage.
        try:
            import os as _os
            max_workers = min(64, (_os.cpu_count() or 4) + 4)
        except Exception:  # noqa: BLE001
            max_workers = 32

    executor = _AbandonableThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="assert-ai-stage-worker",
    )
    loop.set_default_executor(executor)
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        _bounded_loop_teardown(loop, executor, cleanup_timeout_s)
        try:
            asyncio.set_event_loop(None)
        except Exception:  # noqa: BLE001
            pass


def _bounded_loop_teardown(
    loop: asyncio.AbstractEventLoop,
    executor: ThreadPoolExecutor,
    timeout_s: float,
) -> None:
    """Run async-gen shutdown, executor shutdown, loop close in a daemon thread.

    If the daemon thread doesn't finish within ``timeout_s`` seconds we
    abandon it: detach the executor so atexit won't block, log a warning,
    and return. The pipeline continues to the next stage.
    """
    done = threading.Event()
    err_holder: list[BaseException | None] = [None]

    def _teardown() -> None:
        try:
            try:
                if not loop.is_closed():
                    loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception as exc:  # noqa: BLE001
                err_holder[0] = exc
            # Cancel all pending tasks (e.g. litellm's LoggingWorker)
            # before closing the loop. Without this, pending coroutines
            # get garbage-collected against a closed loop and produce
            # noisy "Task was destroyed but it is pending!" errors.
            try:
                if not loop.is_closed():
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
            except Exception as exc:  # noqa: BLE001
                err_holder[0] = err_holder[0] or exc
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except Exception as exc:  # noqa: BLE001
                err_holder[0] = err_holder[0] or exc
            try:
                if not loop.is_closed():
                    loop.close()
            except Exception as exc:  # noqa: BLE001
                err_holder[0] = err_holder[0] or exc
        finally:
            done.set()

    teardown_thread = threading.Thread(
        target=_teardown,
        name="assert-ai-stage-teardown",
        daemon=True,
    )
    teardown_thread.start()

    if not done.wait(timeout=timeout_s):
        # Teardown exceeded its budget. Almost certainly a user-callable
        # left background work (unclosed httpx.AsyncClient, OTel
        # BatchSpanProcessor, etc.) that's now wedged in a finalizer
        # against the closed loop. Detach so process exit isn't blocked
        # by these threads, log loudly so the user knows their target
        # is leaking, and move on.
        _detach_executor_from_atexit(executor)
        log.warning(
            "Stage cleanup exceeded %.0fs and was abandoned. "
            "Likely cause: the target callable left background resources open "
            "(unclosed httpx clients, OpenTelemetry exporters, or similar) that "
            "hang in finalizers when the event loop closes. The pipeline will "
            "continue to the next stage. The leaked worker threads have been "
            "detached from interpreter shutdown so they will not block process "
            "exit, but they continue to consume memory and may print noisy "
            "tracebacks until the process terminates. To "
            "eliminate the leak, explicitly close clients in your target (e.g. "
            "await client.aclose()) or use a singleton LLM client.",
            timeout_s,
        )
        return

    if err_holder[0] is not None:
        log.debug("Stage teardown raised: %r", err_holder[0])


__all__ = [
    "ManifestHeartbeat",
    "PipelineWatchdog",
    "run_stage_coro",
]
