# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Read replay cassettes without letting names or symlinks escape their root."""
from __future__ import annotations

import errno
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class CassettePathError(ValueError):
    """Raised when a cassette name or filesystem entry leaves its configured root."""


def _cassette_filename(name: str) -> str:
    stem = str(name).strip()
    if (
        not stem
        or ".." in stem
        or "/" in stem
        or "\\" in stem
        or "\x00" in stem
    ):
        raise CassettePathError(
            f"cassette name must be a single safe filename stem, got {stem!r}"
        )
    return f"{stem}.json"


def cassette_path(cassette_dir: str | Path, name: str) -> Path:
    """Resolve a present, regular cassette and reject every symlink."""
    root = Path(cassette_dir).resolve(strict=True)
    if not root.is_dir():
        raise CassettePathError(f"cassette root is not a directory: {root}")
    candidate = root / _cassette_filename(name)
    if candidate.is_symlink():
        raise CassettePathError(
            f"cassette {candidate.name!r} is a symlink; replay cassettes must be regular files"
        )
    resolved = candidate.resolve(strict=True)
    if resolved.parent != root:
        raise CassettePathError(
            f"cassette {candidate.name!r} escapes the cassette directory {root}"
        )
    if not resolved.is_file():
        raise CassettePathError(f"cassette is not a regular file: {resolved}")
    return resolved


def _open_cassette_fd(cassette_dir: str | Path, name: str) -> int:
    """Open one cassette atomically relative to its root when the OS supports it."""
    root = Path(cassette_dir).resolve(strict=True)
    filename = _cassette_filename(name)
    supports_openat = (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in getattr(os, "supports_dir_fd", set())
    )
    if supports_openat:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                fd = os.open(
                    filename,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=root_fd,
                )
            except OSError as exc:
                if exc.errno == errno.ELOOP or (root / filename).is_symlink():
                    raise CassettePathError(
                        f"cassette {filename!r} is a symlink; replay cassettes must be regular files"
                    ) from exc
                raise
        finally:
            os.close(root_fd)
    else:
        # Windows lacks portable openat/O_NOFOLLOW support in Python. Reject a
        # symlink before opening, then compare the opened handle with that exact
        # directory entry before reading. A swap to another file or reparse point
        # is detected by the identity mismatch.
        path = cassette_path(root, name)
        before = os.stat(path, follow_symlinks=False)
        fd = os.open(path, os.O_RDONLY)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            os.close(fd)
            raise CassettePathError(
                f"cassette {filename!r} changed while it was being opened"
            )

    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise CassettePathError(f"cassette is not a regular file: {filename}")
    return fd


def cassette_exists(cassette_dir: str | Path, name: str) -> bool:
    """Check readability through the same no-follow path used by replay."""
    try:
        fd = _open_cassette_fd(cassette_dir, name)
    except FileNotFoundError:
        return False
    os.close(fd)
    return True


def read_cassette_bytes(cassette_dir: str | Path, name: str) -> bytes:
    """Read one cassette as bytes through the replay no-follow boundary."""
    fd = _open_cassette_fd(cassette_dir, name)
    with os.fdopen(fd, "rb") as stream:
        return stream.read()


def iter_cassette_bytes(cassette_dir: str | Path) -> Iterator[tuple[str, bytes]]:
    """Yield replayable root-level cassette files without following symlinks.

    Discovery itself considers only regular directory entries. Each discovered
    file is then reopened through ``_open_cassette_fd`` so a symlink swap between
    discovery and reading cannot escape the configured cassette root.
    """
    root = Path(cassette_dir).resolve(strict=True)
    if not root.is_dir():
        raise CassettePathError(f"cassette root is not a directory: {root}")

    filenames: list[str] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                continue
            stem = entry.name[:-5]
            try:
                if _cassette_filename(stem) != entry.name:
                    continue
            except CassettePathError:
                continue
            filenames.append(entry.name)

    for filename in sorted(filenames):
        stem = filename[:-5]
        yield filename, read_cassette_bytes(root, stem)


def read_cassette_json(cassette_dir: str | Path, name: str) -> Any:
    """Read JSON from a securely opened cassette file descriptor."""
    fd = _open_cassette_fd(cassette_dir, name)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        return json.load(stream)
