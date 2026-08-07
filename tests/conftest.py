import logging

import pytest


@pytest.fixture
def symlink_or_skip():
    """Create a symlink or skip when the host lacks permission/support."""
    def create(link, target):
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlink creation is unavailable on this host: {exc}")

    return create


class _SpyHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
    def emit(self, record):
        self.records.append(record)
