#!/usr/bin/env python3
"""Write docs/demo.md from one real run of the plugin.

Uses docs/demo-config.json. No model call. Hermes must be importable
(the pinned checkout's virtualenv is enough):

    python bench/demo.py

The body is ``tests/bench_demo.py``. ``hermes plugins validate`` scans
``bench/*.py`` and would treat those measurement imports as plugin code.
"""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tests" / "bench_demo.py"),
        run_name="__main__",
    )
