#!/usr/bin/env python3
"""Optional coexistence probes. Not part of CI.

Clones the pins in bench/pins.json (git required, network required) into
bench/.cache and loads them beside household-tiers. Prints JSON.

    python bench/coexistence.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "bench" / ".cache"
SENTINEL_MEMORY = "OWNER_MEMORY_SENTINEL"
SENTINEL_USER = "OWNER_PROFILE_SENTINEL"


def _checkout(name: str, repo: str, sha: str) -> Path:
    dest = CACHE / name
    if not (dest / ".git").is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        subprocess.run(["git", "clone", "--filter=blob:none", repo, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "fetch", "--depth", "1", "origin", sha], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--detach", sha], check=True)
    return dest


def _install(home: Path, name: str, source: Path) -> None:
    dest = home / "plugins" / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        source,
        dest,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "tests", "bench"),
    )


def _system() -> str:
    sep = "═" * 46
    return (
        f"base\n\n{sep}\nMEMORY (your personal notes) [1%]\n{sep}\n{SENTINEL_MEMORY}\n\n"
        f"{sep}\nUSER PROFILE (who the user is) [1%]\n{sep}\n{SENTINEL_USER}"
    )


def _markers(payload: dict) -> int:
    blob = json.dumps(payload)
    return blob.count(SENTINEL_MEMORY) + blob.count(SENTINEL_USER)


def _enable(home: Path, names: list[str]) -> None:
    lines = ["plugins:", "  enabled:"]
    lines.extend(f"    - {name}" for name in names)
    (home / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load(home: Path, names: list[str]):
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HOME"] = str(home.parent)
    from gateway.session_context import reset_session_vars
    from hermes_cli.plugins import _reset_plugin_managers_for_tests

    reset_session_vars()
    _reset_plugin_managers_for_tests()
    _enable(home, names)
    from hermes_cli import plugins
    from hermes_cli.middleware import apply_llm_request_middleware

    plugins.get_plugin_manager().discover_and_load(force=True)
    return plugins, apply_llm_request_middleware


def _request():
    return {
        "messages": [{"role": "system", "content": _system()}, {"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "terminal"}}],
    }


def main() -> int:
    pins = json.loads((ROOT / "bench" / "pins.json").read_text(encoding="utf-8"))
    sources = {name: _checkout(name, spec["repo"], spec["sha"]) for name, spec in pins.items()}
    report = {"pins": pins}
    base = Path(tempfile.mkdtemp(prefix="household-coex-"))
    try:
        # memory-shield alone does not strip the prompt.
        home = base / "shield"
        home.mkdir()
        _install(home, "memory-shield", sources["memory-shield"])
        plugins, apply = _load(home, ["memory-shield"])
        os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
        os.environ["HERMES_SESSION_USER_ID"] = "222"
        shield_only = apply(_request(), session_id="s").payload
        report["memory_shield_only_owner_markers"] = _markers(shield_only)

        home = base / "both"
        home.mkdir()
        _install(home, "memory-shield", sources["memory-shield"])
        _install(home, "household-tiers", ROOT)
        (home / "household-tiers.json").write_text(
            json.dumps({"preset": "family", "users": {"telegram:222": "kid", "telegram:111": "owner"}}),
            encoding="utf-8",
        )
        plugins, apply = _load(home, ["memory-shield", "household-tiers"])
        both = apply(_request(), session_id="s").payload
        report["memory_shield_plus_household_tiers_owner_markers"] = _markers(both)

        # readonly-guard: /readonly writable opens writes for that session.
        # A kid tier still cannot call terminal.
        from gateway.session_context import set_session_vars

        home = base / "ro"
        home.mkdir()
        _install(home, "readonly-guard", sources["readonly-guard"])
        (home / "plugins" / "readonly-guard").mkdir(parents=True, exist_ok=True)
        plugins, _apply = _load(home, ["readonly-guard"])
        set_session_vars(platform="telegram", user_id="222", session_id="sess-ro", session_key="k")
        handler = plugins.get_plugin_command_handler("readonly")
        report["readonly_guard_writable_reply"] = handler("writable") if handler else None
        alone = plugins.invoke_hook(
            "pre_tool_call", tool_name="write_file", args={"path": "notes.txt", "content": "x"}, session_id="sess-ro"
        )
        report["readonly_guard_only_write_file_blocked"] = any(
            isinstance(item, dict) and item.get("action") == "block" for item in (alone or [])
        )

        home = base / "ro-both"
        home.mkdir()
        _install(home, "readonly-guard", sources["readonly-guard"])
        _install(home, "household-tiers", ROOT)
        (home / "household-tiers.json").write_text(
            json.dumps({"preset": "family", "users": {"telegram:222": "kid"}}),
            encoding="utf-8",
        )
        plugins, _apply = _load(home, ["readonly-guard", "household-tiers"])
        set_session_vars(platform="telegram", user_id="222", session_id="sess-both", session_key="k")
        os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
        os.environ["HERMES_SESSION_USER_ID"] = "222"
        os.environ["HERMES_SESSION_ID"] = "sess-both"
        handler = plugins.get_plugin_command_handler("readonly")
        report["both_writable_reply"] = handler("writable") if handler else None
        together = plugins.invoke_hook(
            "pre_tool_call",
            tool_name="terminal",
            args={"command": "echo hi"},
            session_id="sess-both",
        )
        report["both_terminal_blocked"] = any(
            isinstance(item, dict) and item.get("action") == "block" for item in (together or [])
        )
        blocks = [item for item in (together or []) if isinstance(item, dict) and item.get("action") == "block"]
        report["both_block_messages"] = [item.get("message", "")[:80] for item in blocks]
    finally:
        shutil.rmtree(base, ignore_errors=True)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
