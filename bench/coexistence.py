#!/usr/bin/env python3
"""Optional coexistence probes. Not part of CI.

Clones the pins in bench/pins.json (git required, network required) into
bench/.cache and loads them beside household-tiers. Prints JSON.

    python bench/coexistence.py

The body is ``tests/bench_coexistence.py``. ``hermes plugins validate`` scans
``bench/*.py`` and would treat those measurement imports as plugin code.
"""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tests" / "bench_coexistence.py"),
        run_name="__main__",
    )
