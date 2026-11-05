"""Fixtures for household-tiers. Nothing here runs the plugin at import time."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SESSION_ENV = (
    "HERMES_SESSION_PLATFORM",
    "HERMES_SESSION_USER_ID",
    "HERMES_SESSION_CHAT_ID",
    "HERMES_SESSION_CHAT_TYPE",
    "HERMES_SESSION_SOURCE",
    "HERMES_CRON_SESSION",
    "HERMES_PLUGIN_HOST_PROCESS",
    "HERMES_ALLOW_PRIVATE_URLS",
    "HOUSEHOLD_TIERS_HIDE_TOOLS",
)


def write_policy(home: Path, body: dict) -> None:
    (home / "household-tiers.json").write_text(json.dumps(body), encoding="utf-8")


@pytest.fixture
def home(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in SESSION_ENV:
        monkeypatch.delenv(key, raising=False)
    try:
        from gateway.session_context import reset_session_vars

        reset_session_vars()
    except Exception:
        pass
    yield hermes_home
    try:
        from gateway.session_context import reset_session_vars

        reset_session_vars()
    except Exception:
        pass


@pytest.fixture
def ht(home):
    """A fresh import of the plugin with HERMES_HOME pointed at ``home``."""
    name = f"household_tiers_under_test_{id(home)}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.HOME = home
    return module


@pytest.fixture
def family(home):
    write_policy(
        home,
        {
            "preset": "family",
            "users": {"telegram:111": "owner", "telegram:222": "kid", "telegram:333": "user"},
            "notify": {"platform": "telegram", "chat_id": "PARENTS"},
        },
    )
    return home


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    """Plugin installed into a fresh HERMES_HOME and loaded by the real manager."""
    hermes_home = tmp_path / "hermes"
    plugin_dir = hermes_home / "plugins" / "household-tiers"
    plugin_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ROOT,
        plugin_dir,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", "tests", "bench", ".github", "catalog", "docs"
        ),
    )
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - household-tiers\n", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in SESSION_ENV:
        monkeypatch.delenv(key, raising=False)
    from gateway.session_context import reset_session_vars
    from hermes_cli.plugins import _reset_plugin_managers_for_tests

    reset_session_vars()
    _reset_plugin_managers_for_tests()
    from hermes_cli import plugins

    plugins.get_plugin_manager().discover_and_load(force=True)
    loaded = [
        module
        for module_name, module in sys.modules.items()
        if module_name.startswith("hermes_plugins.") and hasattr(module, "_legacy_stem")
    ]
    assert len(loaded) == 1, [name for name in sys.modules if name.startswith("hermes_plugins.")]
    yield {"home": hermes_home, "mod": loaded[0], "plugins": plugins}
    _reset_plugin_managers_for_tests()
    reset_session_vars()
