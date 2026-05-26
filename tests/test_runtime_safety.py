"""Tests for the runtime-safety layer: heartbeat, watchdog, bounded teardown.

The motivating bug: a multi-agent LangGraph travel-planner target leaked
``httpx.AsyncClient`` instances per scenario; at high concurrency the
per-stage ``asyncio.run`` teardown deadlocked inside
``loop.shutdown_default_executor`` waiting for worker threads stuck in
finalizers. These tests verify the three defenses (Layers 1, 2, 4 from the
investigation):

* :func:`run_stage_coro` returns within ``cleanup_timeout_s`` even when a
  worker thread refuses to die.
* :class:`ManifestHeartbeat` writes the manifest periodically and merges
  progress payloads from concurrent callers.
* :class:`PipelineWatchdog` dumps stacks after the idle threshold and does
  not dump again until ``tick`` is called.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from p2m.core.runtime_safety import (
    ManifestHeartbeat,
    PipelineWatchdog,
    run_stage_coro,
)


# ---------------------------------------------------------------------------
# run_stage_coro / bounded teardown
# ---------------------------------------------------------------------------


def test_run_stage_coro_returns_coroutine_result() -> None:
    """Happy path: behaves like asyncio.run for a normal coroutine."""

    async def _coro() -> str:
        await asyncio.sleep(0)
        return "ok"

    assert run_stage_coro(_coro(), cleanup_timeout_s=10.0) == "ok"


def test_run_stage_coro_propagates_exceptions() -> None:
    """Exceptions raised inside the coroutine surface to the caller."""

    class _CustomError(RuntimeError):
        pass

    async def _coro() -> None:
        raise _CustomError("boom")

    with pytest.raises(_CustomError, match="boom"):
        run_stage_coro(_coro(), cleanup_timeout_s=10.0)


def test_run_stage_coro_bounded_teardown_returns_when_worker_hangs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The headline test: if a worker leaks a thread that refuses to die,
    teardown returns within the timeout and the pipeline can continue.

    Simulates the LangGraph leak pattern: coroutine dispatches sync work
    to the default executor via asyncio.to_thread; one of those workers
    is stuck in a syscall-style wait. Without the bounded teardown,
    executor.shutdown(wait=True) would block forever.

    Scope note: this test only verifies the in-process behavior of
    ``run_stage_coro`` (the function returns and the next stage can
    proceed). It releases the hung worker in ``finally`` so pytest can
    tear down cleanly, which means it does NOT exercise interpreter
    shutdown. The separate
    ``test_run_stage_coro_does_not_block_subprocess_exit_when_worker_leaked``
    regression test below covers that gap by spawning a fresh subprocess
    that leaks a worker and asserting the subprocess still exits.
    """
    release = threading.Event()
    started = threading.Event()

    def _hang_forever() -> str:
        # Mimic an unclosed httpx client whose finalizer is wedged on a
        # closed event loop: a blocking wait that won't naturally complete.
        started.set()
        release.wait(timeout=120.0)
        return "released-by-test-cleanup"

    async def _coro() -> str:
        # asyncio.to_thread fans this out to the loop's default executor
        # (which run_stage_coro replaces with one it owns). We do NOT
        # await the second call's result so the worker thread is still
        # active when the coroutine returns -- exactly the leak shape
        # we're defending against.
        asyncio.get_running_loop().run_in_executor(None, _hang_forever)
        # Wait until the worker thread is actually running so that
        # executor.shutdown(cancel_futures=True) cannot cancel a
        # not-yet-started future — which would let cleanup finish
        # instantly and skip the timeout warning we assert below.
        while not started.is_set():
            await asyncio.sleep(0.01)
        return "main-coro-done"

    caplog.set_level(logging.WARNING, logger="p2m.core.runtime_safety")
    start = time.monotonic()
    try:
        # 2s timeout so the test runs fast; in production it's 300s.
        result = run_stage_coro(_coro(), cleanup_timeout_s=2.0)
    finally:
        release.set()  # let the hung worker exit so pytest can clean up
    elapsed = time.monotonic() - start

    assert result == "main-coro-done"
    # Must return within ~timeout + a small buffer for the daemon-thread
    # join overhead. 10s is generous.
    assert elapsed < 10.0, f"teardown took {elapsed:.1f}s, expected <10s"
    # Verify the user-facing warning fired so a real user would see why
    # their pipeline is moving on past a hung target cleanup.
    assert any(
        "Stage cleanup exceeded" in r.message
        for r in caplog.records
        if r.levelno == logging.WARNING
    ), "expected cleanup-timeout warning to be logged"


def test_run_stage_coro_clean_shutdown_logs_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the coroutine and its executor work both finish cleanly,
    no cleanup warning should be emitted."""

    async def _coro() -> int:
        # Awaited to_thread call -> worker thread has fully returned by
        # the time the coroutine returns. Executor shutdown should be
        # near-instant.
        await asyncio.to_thread(lambda: time.sleep(0.01))
        return 42

    caplog.set_level(logging.WARNING, logger="p2m.core.runtime_safety")
    assert run_stage_coro(_coro(), cleanup_timeout_s=10.0) == 42
    assert not any(
        "Stage cleanup exceeded" in r.message for r in caplog.records
    ), "clean shutdown should not emit a cleanup warning"


def test_run_stage_coro_uses_daemon_workers_for_abandonable_executor() -> None:
    """Stage executor workers are daemon threads so CPython 3.13+ shutdown
    does not join them if bounded teardown intentionally abandons the executor.
    """

    async def _coro() -> bool:
        return await asyncio.to_thread(lambda: threading.current_thread().daemon)

    assert run_stage_coro(_coro(), cleanup_timeout_s=10.0) is True


def test_run_stage_coro_does_not_block_subprocess_exit_when_worker_leaked(
    tmp_path: Path,
) -> None:
    """Regression test for the actual deadlock class: even when a worker
    is leaked and never released, a Python subprocess that called
    ``run_stage_coro`` must still be able to exit.

    The in-process headline test above only asserts that
    ``run_stage_coro`` returns — it then sets ``release.set()`` in
    ``finally`` to let pytest tear down. That hides the more dangerous
    failure mode: Python's interpreter shutdown joins live non-daemon
    threads through ``concurrent.futures._python_exit`` and through
    ``threading._shutdown``. Python 3.11/3.12 expose the second join via
    ``_shutdown_locks``; Python 3.13+ moved it behind C-level thread
    handles. If the stage executor cannot escape both paths, shutdown
    still blocks waiting for the leaked worker forever, hanging the whole
    process.

    This test spawns a fresh ``sys.executable`` subprocess that leaks a
    worker via ``run_stage_coro``, prints a sentinel, and then exits.
    If the dual-detach is correct, the subprocess exits within the
    timeout. If not, ``subprocess.run`` raises ``TimeoutExpired``.
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        '''
        import asyncio
        import threading
        from p2m.core.runtime_safety import run_stage_coro

        never_released = threading.Event()

        def _hang_forever() -> None:
            # Mimic an unclosed httpx finalizer wedged on a closed loop:
            # a blocking wait that will never complete.
            never_released.wait()  # set() is never called

        async def _coro() -> str:
            # Fire-and-forget: don't await the executor future. This
            # leaks the worker thread by design.
            asyncio.get_running_loop().run_in_executor(None, _hang_forever)
            await asyncio.sleep(0.01)
            return "main-done"

        result = run_stage_coro(_coro(), cleanup_timeout_s=0.5)
        print("RESULT=" + result, flush=True)
        print("EXIT-SENTINEL", flush=True)
        '''
    )
    script_path = tmp_path / "leaked_worker_subprocess_probe.py"
    script_path.write_text(script, encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (
            exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        )
        stderr = exc.stderr if isinstance(exc.stderr, str) else (
            exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        )
        pytest.fail(
            f"Subprocess hung at interpreter shutdown (timeout={exc.timeout}s).\n"
            f"This means the worker-thread detach is incomplete: "
            f"threading._shutdown is still joining the leaked worker.\n"
            f"--- subprocess stdout ---\n{stdout}\n"
            f"--- subprocess stderr ---\n{stderr}"
        )

    assert "EXIT-SENTINEL" in proc.stdout, (
        f"subprocess did not reach the exit sentinel — it may have crashed "
        f"before run_stage_coro returned.\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
    assert proc.returncode == 0, (
        f"subprocess exited with rc={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# ManifestHeartbeat
# ---------------------------------------------------------------------------


@dataclass
class _StubManifest:
    """Minimal stand-in for RunManifest used by heartbeat tests."""

    heartbeat_at: str | None = None
    status: str = "running"
    progress: dict[str, Any] | None = None
    extra_writes: list[dict[str, Any]] = field(default_factory=list)


def test_heartbeat_writes_manifest_periodically(tmp_path: Path) -> None:
    """Heartbeat must call its write_fn at the configured interval."""
    manifest = _StubManifest()
    write_calls: list[float] = []

    def _write(m: _StubManifest, root: Path) -> None:
        write_calls.append(time.monotonic())

    hb = ManifestHeartbeat(manifest, tmp_path, _write, interval_s=0.05)
    hb.start()
    try:
        # Wait long enough to capture at least 3 ticks.
        time.sleep(0.25)
    finally:
        hb.stop()
    # The interval is 0.05s; in 0.25s we expect ~3-5 writes. Be lenient
    # on the upper bound (slow CI, scheduler jitter).
    assert len(write_calls) >= 2, f"expected >=2 heartbeat writes, got {len(write_calls)}"


def test_heartbeat_set_progress_merges_and_appears_on_next_write(
    tmp_path: Path,
) -> None:
    """set_progress() updates the manifest's progress field before next write."""
    manifest = _StubManifest()
    snapshots: list[dict[str, Any] | None] = []

    def _write(m: _StubManifest, root: Path) -> None:
        snapshots.append(dict(m.progress) if m.progress else None)

    hb = ManifestHeartbeat(manifest, tmp_path, _write, interval_s=0.05)
    hb.set_progress(stage="inference", completed=0, total=10)
    hb.start()
    try:
        time.sleep(0.1)
        hb.set_progress(completed=5, errors=1)  # merge, don't replace stage/total
        time.sleep(0.15)
    finally:
        hb.stop()
    # At least one snapshot should contain merged keys.
    merged = [
        s for s in snapshots
        if s is not None
        and s.get("stage") == "inference"
        and s.get("completed") == 5
        and s.get("total") == 10
        and s.get("errors") == 1
    ]
    assert merged, f"expected merged progress snapshot, got {snapshots!r}"


def test_heartbeat_clear_progress_drops_payload(tmp_path: Path) -> None:
    manifest = _StubManifest()
    snapshots: list[dict[str, Any] | None] = []

    def _write(m: _StubManifest, root: Path) -> None:
        snapshots.append(dict(m.progress) if m.progress else None)

    hb = ManifestHeartbeat(manifest, tmp_path, _write, interval_s=0.05)
    hb.set_progress(stage="inference", completed=10, total=10)
    hb.start()
    try:
        time.sleep(0.1)
        hb.clear_progress()
        time.sleep(0.15)
    finally:
        hb.stop()
    # The later snapshots (after clear) should be None.
    assert snapshots, "heartbeat should have written at least once"
    assert snapshots[-1] is None, f"expected last snapshot None after clear, got {snapshots[-1]!r}"


def test_heartbeat_swallows_write_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing write must never raise into the daemon thread or block
    subsequent ticks. (If a transient I/O error killed the heartbeat,
    the manifest would silently freeze again — the exact bug we're
    fixing.)"""
    manifest = _StubManifest()
    call_count = [0]

    def _flaky_write(m: _StubManifest, root: Path) -> None:
        call_count[0] += 1
        raise OSError("disk full (simulated)")

    caplog.set_level(logging.DEBUG, logger="p2m.core.runtime_safety")
    hb = ManifestHeartbeat(manifest, tmp_path, _flaky_write, interval_s=0.05)
    hb.start()
    try:
        time.sleep(0.2)
    finally:
        hb.stop()
    assert call_count[0] >= 2, "heartbeat should have retried despite errors"


# ---------------------------------------------------------------------------
# PipelineWatchdog
# ---------------------------------------------------------------------------


def test_watchdog_dumps_stacks_after_idle_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Watchdog must dump thread stacks when no tick fires for >threshold."""
    caplog.set_level(logging.WARNING, logger="p2m.core.runtime_safety")
    wd = PipelineWatchdog(idle_threshold_s=0.1, check_interval_s=0.05)
    wd.start()
    try:
        time.sleep(0.4)
    finally:
        wd.stop()
    dump_lines = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "Pipeline watchdog" in r.message
    ]
    assert dump_lines, "watchdog should have logged an idle-pipeline warning"


def test_watchdog_does_not_dump_twice_for_same_idle_period(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Single hang -> single dump, not one per check interval."""
    caplog.set_level(logging.WARNING, logger="p2m.core.runtime_safety")
    wd = PipelineWatchdog(idle_threshold_s=0.1, check_interval_s=0.05)
    wd.start()
    try:
        time.sleep(0.5)  # several check intervals beyond threshold
    finally:
        wd.stop()
    dump_lines = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "Pipeline watchdog" in r.message
    ]
    assert len(dump_lines) == 1, (
        f"expected exactly one watchdog dump per idle period, got {len(dump_lines)}"
    )


def test_watchdog_tick_resets_idle_clock(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tick before the threshold prevents (or resets) the dump."""
    caplog.set_level(logging.WARNING, logger="p2m.core.runtime_safety")
    wd = PipelineWatchdog(idle_threshold_s=0.2, check_interval_s=0.05)
    wd.start()
    try:
        # Tick at ~0.05s intervals so the threshold never elapses.
        for _ in range(8):
            time.sleep(0.05)
            wd.tick()
    finally:
        wd.stop()
    dump_lines = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "Pipeline watchdog" in r.message
    ]
    assert not dump_lines, "watchdog should not have dumped when ticks are regular"


def test_heartbeat_ticks_watchdog_when_attached(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Wiring: a healthy heartbeat keeps the watchdog quiet automatically."""
    manifest = _StubManifest()

    def _noop_write(m: _StubManifest, root: Path) -> None:
        pass

    hb = ManifestHeartbeat(manifest, tmp_path, _noop_write, interval_s=0.05)
    wd = PipelineWatchdog(idle_threshold_s=0.15, check_interval_s=0.05)
    hb.attach_watchdog(wd)
    caplog.set_level(logging.WARNING, logger="p2m.core.runtime_safety")
    wd.start()
    hb.start()
    try:
        time.sleep(0.4)
    finally:
        hb.stop()
        wd.stop()
    dump_lines = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "Pipeline watchdog" in r.message
    ]
    assert not dump_lines, "heartbeat ticks should keep the watchdog quiet"
