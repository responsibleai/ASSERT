# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import sys
from pathlib import Path


EVALUATION_DIR = Path(__file__).resolve().parents[1]
if str(EVALUATION_DIR) not in sys.path:
    sys.path.insert(0, str(EVALUATION_DIR))
