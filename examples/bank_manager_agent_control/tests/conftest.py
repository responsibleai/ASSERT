"""Make the sibling ``_bootstrap`` module importable under pytest.

``tests/`` is a package (it has ``__init__.py``), so pytest inserts the example
root on ``sys.path`` and NOT this directory. The test modules were written for
``python -m unittest discover -s tests``, where the plain ``import _bootstrap``
resolves. Putting this directory on ``sys.path`` here makes both runners work.
"""

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import _bootstrap  # noqa: E402,F401  (side effects: sys.path + KB env)
