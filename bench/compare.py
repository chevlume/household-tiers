#!/usr/bin/env python3
"""Measure household-tiers against the Hermes checkout that is importing this script.

Run with Hermes on PYTHONPATH (or from the Hermes virtualenv) and this repo as
the working directory:

    python bench/compare.py

Prints one JSON object. Figures in the README are copied from that object.
No model calls and no network calls.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENTINEL_MEMORY = "OWNER_MEMORY_SENTINEL"
SENTINEL_USER = "OWNER_PROFILE_SENTINEL"
SENTINEL_RECALL = "RECALL_SENTINEL"
SENTINEL_TEXT = "INBOUND_TEXT_SENTINEL"


def _load_plugin(home: Path):
    os.environ["HERMES_HOME"] = str(home)
    name = "household_tiers_bench"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _policy(home: Path, body: dict) -> None:
    (home / "household-tiers.json").write_text(json.dumps(body), encoding="utf-8")


def _memory_system() -> str:
    sep = "═" * 46
    return (
        f"You are Hermes.\n\n{sep}\nMEMORY (your personal notes) [1%]\n{sep}\n{SENTINEL_MEMORY}\n\n"
        f"{sep}\nUSER PROFILE (who the user is) [1%]\n{sep}\n{SENTINEL_USER}"
    )


def _count(blob: str) -> int:
    return int(SENTINEL_MEMORY in blob) + int(SENTINEL_USER in blob)


def _tokens(tools: list) -> tuple[int, str]:
    payload = [json.dumps(tool, ensure_ascii=False) for tool in tools]
    try:
        import tiktoken

        encoder = tiktoken.get_encoding("cl100k_base")
        return sum(len(encoder.encode(item)) for item in payload), "tiktoken cl100k_base (Hermes tool-token estimator)"
    except Exception:
        return sum(len(item) // 4 for item in payload), "chars/4 fallback (tiktoken unavailable)"


def _tool_names(tools: list) -> list[str]:
    return [(tool.get("function") or {}).get("name", "") for tool in tools]


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, int(__import__("math").ceil(0.95 * len(ordered))) - 1)
    return ordered[index]


def main() -> int:
    from gateway.session_context import reset_session_vars, set_session_vars

    reset_session_vars()
    os.environ.pop("HERMES_PLUGIN_HOST_PROCESS", None)
    os.environ.pop("HERMES_ALLOW_PRIVATE_URLS", None)
    home = Path(tempfile.mkdtemp(prefix="household-tiers-bench-"))
    try:
        plugin = _load_plugin(home)
        _policy(home, {"preset": "family", "users": {"telegram:111": "owner", "telegram:222": "kid"}})
        os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
        os.environ["HERMES_SESSION_USER_ID"] = "222"
        from tools.registry import discover_builtin_tools

        discover_builtin_tools()
        from model_tools import get_tool_definitions

        unfiltered = get_tool_definitions(enabled_toolsets=["hermes-telegram"], quiet_mode=True)
        request = {
            "messages": [{"role": "system", "content": "base"}, {"role": "user", "content": "hi"}],
            "tools": unfiltered,
        }
        filtered = plugin._llm_request(request=request, session_id="bench")["request"]["tools"]
        unfiltered_tokens, tokenizer = _tokens(unfiltered)
        filtered_tokens, _same = _tokens(filtered)

        secret_request = {
            "messages": [
                {"role": "system", "content": _memory_system()},
                {"role": "user", "content": f"hello\n<memory-context>{SENTINEL_RECALL}</memory-context>"},
            ],
            "tools": unfiltered[:3],
        }
        # The recall sentinel is the same string as the memory block on purpose:
        # _count only looks at the two owner markers. Count them in the two
        # system blocks before and after. The recall copy is extra and must
        # also be gone; checked separately.
        before = _count(secret_request["messages"][0]["content"])
        after_req = plugin._llm_request(request=secret_request, session_id="secrets")["request"]
        after_blob = json.dumps(after_req)
        restricted_leaks = _count(after_blob)
        recall_leaks = after_blob.count(SENTINEL_RECALL)

        (home / "household-tiers.json").write_text("{ broken", encoding="utf-8")
        broken = plugin._llm_request(request=secret_request, session_id="broken")
        broken_blob = json.dumps(broken["request"])
        broken_leaks = _count(broken_blob)
        broken_tools = broken["request"].get("tools")

        _policy(home, {"preset": "family", "users": {"telegram:111": "owner", "telegram:222": "kid"}})
        os.environ["HERMES_PLUGIN_HOST_PROCESS"] = "1"
        host = plugin._llm_request(request=secret_request, session_id="host")
        host_leaks = _count(json.dumps(host["request"])) if host else _count(json.dumps(secret_request))
        host_tier = plugin._identity("host")[0]
        os.environ.pop("HERMES_PLUGIN_HOST_PROCESS", None)

        # /btw and /bg do not get a live model here. The fork still passes
        # through llm_request and pre_tool_call; that is what is counted.
        os.environ["HERMES_SESSION_USER_ID"] = "222"
        plugin._identity("parent-session")
        plugin._subagent_start(parent_session_id="parent-session", child_session_id="bg-child")
        os.environ.pop("HERMES_SESSION_PLATFORM", None)
        os.environ.pop("HERMES_SESSION_USER_ID", None)
        bg = plugin._llm_request(request=secret_request, session_id="bg-child")
        bg_leaks = _count(json.dumps(bg["request"]))
        bg_terminal = plugin._pre_tool_call(tool_name="terminal", args={"command": "echo hi"}, session_id="bg-child")
        bg_memory = plugin._pre_tool_call(
            tool_name="memory", args={"action": "add", "content": SENTINEL_MEMORY}, session_id="bg-child"
        )
        btw = plugin._llm_request(request=secret_request, session_id="parent-session")
        btw_leaks = _count(json.dumps(btw["request"]))
        review_blocked = plugin._pre_tool_call(
            tool_name="memory",
            args={"action": "add", "target": "memory", "content": SENTINEL_MEMORY},
            session_id="parent-session",
        )

        os.environ["HERMES_ALLOW_PRIVATE_URLS"] = "1"
        os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
        os.environ["HERMES_SESSION_USER_ID"] = "222"
        lan_urls = [
            "http://192.168.1.1/",
            "http://127.0.0.1/",
            "http://2130706433/",
            "http://192.168.1.1.nip.io/",
            "http://93.184.215.14/",
        ]
        lan_blocked = []
        for url in lan_urls:
            result = plugin._pre_tool_call(tool_name="web_extract", args={"urls": [url]}, session_id="lan")
            lan_blocked.append(bool(result and result.get("action") == "block"))

        # Shared group, real admission hook.
        from gateway.config import Platform
        from gateway.platforms.base import MessageEvent
        from gateway.run import GatewayRunner
        from gateway.run_inbound_consumer import run_post_admission_hook
        from gateway.session import SessionSource, build_session_key
        from hermes_cli.plugins import _reset_plugin_managers_for_tests

        installed = home / "plugins" / "household-tiers"
        installed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            ROOT,
            installed,
            ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", "tests", "bench", ".github"),
        )
        (home / "config.yaml").write_text("plugins:\n  enabled:\n    - household-tiers\n", encoding="utf-8")
        reset_session_vars()
        _reset_plugin_managers_for_tests()
        from hermes_cli import plugins

        plugins.get_plugin_manager().discover_and_load(force=True)
        loaded = next(
            module
            for module_name, module in sys.modules.items()
            if module_name.startswith("hermes_plugins.") and hasattr(module, "_legacy_stem")
        )
        loaded._now = lambda tz: __import__("datetime").time(15, 0)
        runner = object.__new__(GatewayRunner)
        runner.adapters = {}
        runner._resolve_profile_home_for_source = lambda source: None
        source = SessionSource(platform=Platform.TELEGRAM, chat_id="FAM", user_id="222", chat_type="group")
        key = build_session_key(source, group_sessions_per_user=False)

        async def _group():
            return await run_post_admission_hook(
                runner, MessageEvent(text=SENTINEL_TEXT, source=source, message_id="m"), source, key
            )

        handled, reply = asyncio.run(_group())
        group_leaks = 0 if not reply or SENTINEL_TEXT not in reply else 1

        samples = []
        os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
        os.environ["HERMES_SESSION_USER_ID"] = "222"
        _policy(home, {"preset": "family", "users": {"telegram:222": "kid"}})
        # The direct module still reads the policy file. Time the allow path.
        for _ in range(20):
            plugin._pre_tool_call(tool_name="web_search", args={}, session_id="warm")
        for _ in range(200):
            start = time.perf_counter()
            plugin._pre_tool_call(tool_name="web_search", args={}, session_id="warm")
            samples.append(time.perf_counter() - start)

        report = {
            "tokenizer": tokenizer,
            "toolset": "hermes-telegram",
            "unfiltered_tools": len(unfiltered),
            "unfiltered_tool_names": _tool_names(unfiltered),
            "filtered_tools": len(filtered),
            "filtered_tool_names": _tool_names(filtered),
            "unfiltered_schema_tokens": unfiltered_tokens,
            "filtered_schema_tokens": filtered_tokens,
            "restricted_prompt_owner_markers_before": before,
            "restricted_prompt_owner_markers_after": restricted_leaks,
            "restricted_prompt_memory_context_tags_after": recall_leaks,
            "broken_config_owner_markers": broken_leaks,
            "broken_config_tools": broken_tools,
            "host_isolation_tier": host_tier,
            "host_isolation_owner_markers": host_leaks,
            "btw_prepared_request_owner_markers": btw_leaks,
            "background_child_owner_markers": bg_leaks,
            "background_child_terminal_blocked": bool(bg_terminal and bg_terminal.get("action") == "block"),
            "background_child_memory_blocked": bool(bg_memory and bg_memory.get("action") == "block"),
            "background_review_memory_blocked": bool(review_blocked and review_blocked.get("action") == "block"),
            "background_review_live_model": "not_run",
            "shared_group_handled": handled,
            "shared_group_reply_contains_message_text": group_leaks,
            "lan_urls": lan_urls,
            "lan_blocked": lan_blocked,
            "pre_tool_call_p95_seconds": round(_p95(samples), 6),
            "live_model_turns": "not_run",
        }
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
