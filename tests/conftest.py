import logging
import pytest


class _SpyHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
    def emit(self, record):
        self.records.append(record)


_spy = None

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    global _spy
    if "bounded_teardown" in item.name:
        _spy = _SpyHandler()
        _spy.setLevel(logging.DEBUG)
        safety_logger = logging.getLogger("p2m.core.runtime_safety")
        safety_logger.addHandler(_spy)
        # Patch the filter on caplog's handler to trace calls
        caplog = item.funcargs.get("caplog")
        if caplog and caplog.handler.filters:
            orig_filter = caplog.handler.filters[0].filter
            call_log = []
            def traced_filter(record, _orig=orig_filter):
                result = _orig(record)
                call_log.append((record.getMessage()[:80], result))
                return result
            caplog.handler.filters[0].filter = traced_filter
            item._traced_filter_log = call_log

    outcome = yield

    if "bounded_teardown" in item.name and _spy is not None:
        try:
            lines = []
            caplog = item.funcargs.get("caplog")
            if caplog:
                lines.append(f"caplog.records count = {len(caplog.records)}")
                for r in caplog.records:
                    lines.append(f"  level={r.levelno} logger={r.name} msg={r.message[:120]}")
            lines.append(f"spy.records count = {len(_spy.records)}")
            for r in _spy.records:
                lines.append(f"  spy: level={r.levelno} msg={r.getMessage()[:200]}")
            # Check which handler is caplog's
            caplog_handler = caplog.handler if caplog else None
            for i, h in enumerate(logging.root.handlers):
                is_caplog = " [CAPLOG]" if h is caplog_handler else ""
                lines.append(f"  Root handler[{i}] {type(h).__name__}({h.level}){is_caplog}: filters={[type(f).__name__ for f in h.filters]}, records={len(h.records) if hasattr(h, 'records') else 'N/A'}")
            traced = getattr(item, '_traced_filter_log', [])
            lines.append(f"traced filter calls: {len(traced)}")
            for msg, result in traced:
                lines.append(f"  filter({msg!r}) -> {result}")
            with open("/tmp/debug_caplog.txt", "w") as f:
                f.write("\n".join(lines))
        finally:
            logging.getLogger("p2m.core.runtime_safety").removeHandler(_spy)
            _spy = None
