# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Cross-process locks for short managed-file updates."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from assert_ai.services.errors import ServiceError, ServiceErrorCode


@contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    timeout_s: float,
    conflict_message: str,
) -> Iterator[None]:
    """Hold an advisory lock on one workspace-managed lock file."""
    deadline = time.monotonic() + timeout_s
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ServiceError(
                        ServiceErrorCode.CONFLICT,
                        conflict_message,
                    ) from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
