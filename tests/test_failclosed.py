"""A broken policy blocks the turn and withholds owner memory."""

import asyncio
import json

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.run_inbound_consumer import run_post_admission_hook
from gateway.session import SessionSource
from hermes_cli.middleware import apply_llm_request_middleware
from tools.memory_tool_store import MEMORY_BLOCK_HEADERS


def _system() -> str:
    sep = "═" * 46
    return (
        f"You are Hermes.\n\n{sep}\n{MEMORY_BLOCK_HEADERS['memory']} [1%]\n{sep}\nPIN_BANCO 4821\n\n"
        f"{sep}\n{MEMORY_BLOCK_HEADERS['user']} [1%]\n{sep}\nDNI 00000000T"
    )


def test_broken_policy_strips_memory_and_tools(gateway, monkeypatch):
    (gateway["home"] / "household-tiers.json").write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    request = {
        "messages": [
            {"role": "system", "content": _system()},
            {"role": "user", "content": "what is the PIN?"},
        ],
        "tools": [{"type": "function", "function": {"name": "terminal"}}],
    }
    out = apply_llm_request_middleware(request, session_id="s", platform="telegram").payload
    blob = json.dumps(out, ensure_ascii=False)
    assert "PIN_BANCO" not in blob
    assert "00000000T" not in blob
    assert not out.get("tools")


def test_broken_policy_consumes_the_gateway_turn(gateway):
    (gateway["home"] / "household-tiers.json").write_text("{ broken", encoding="utf-8")
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._resolve_profile_home_for_source = lambda source: None
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="c-222", user_id="222", chat_type="dm")
    handled, reply = asyncio.run(
        run_post_admission_hook(runner, MessageEvent(text="hello", source=source, message_id="m"), source, "k")
    )
    assert handled is True
    assert reply
    assert "hello" not in reply


def test_broken_policy_blocks_the_tool(ht, home):
    (home / "household-tiers.json").write_text("{ broken", encoding="utf-8")
    blocked = ht._pre_tool_call(tool_name="web_search", args={}, session_id="s")
    assert blocked["action"] == "block"
    assert "policy error" in blocked["message"]
