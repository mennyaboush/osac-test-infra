"""Make the ``bcm_simulator`` package importable when running the simulator's
own unit tests, without installing it. pytest imports this conftest from the
simulator directory before collecting ``tests/``.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
