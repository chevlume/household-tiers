#!/usr/bin/env python3
"""Measure household-tiers against the Hermes checkout that is importing this script.

Run with Hermes on PYTHONPATH (or from the Hermes virtualenv) and this repo as
the working directory:

    python bench/compare.py

Prints one JSON object. Figures in the README are copied from that object.
No model calls. The LAN samples in this script are IP literals and embedded
nip.io names, classified without a resolver call. It does not fetch URLs.

The body is ``tests/bench_compare.py``. ``hermes plugins validate`` scans
``bench/*.py`` and would treat those measurement imports as plugin code.
"""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tests" / "bench_compare.py"),
        run_name="__main__",
    )
