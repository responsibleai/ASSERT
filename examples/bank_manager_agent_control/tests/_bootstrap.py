"""Shared test bootstrap: put the example dir on sys.path and point the KB at
the local corpus. Imported (not run) by every test module so the suite works
under `python3 -m unittest discover -s tests` from the example directory.
"""

import os
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = EXAMPLE_DIR / "runtime"
for _d in (RUNTIME_DIR, EXAMPLE_DIR):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

os.environ.setdefault("KB_BACKEND", "mock")
os.environ["KB_CORPUS_DIR"] = str(RUNTIME_DIR / "knowledge")
