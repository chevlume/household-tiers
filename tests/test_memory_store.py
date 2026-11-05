"""Multi-paragraph owner memory, built with Hermes MemoryStore and real transports."""

import json

import pytest

SECRETS = ("SECOND_PARAGRAPH_TOKEN", "LATER_ENTRY_TOKEN", "PROFILE_SECOND_PARAGRAPH")


def _policy(home):
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}}),
        encoding="utf-8",
    )


def _system_prompt(home):
    from tools.memory_tool_store import MemoryStore

    store = MemoryStore(memory_char_limit=8000, user_char_limit=8000)
    added = store.add("memory", "PIN_LINE_ONE\n\nSECOND_PARAGRAPH_TOKEN")
    assert added.get("success"), added
    added = store.add("memory", "LATER_ENTRY_TOKEN")
    assert added.get("success"), added
    added = store.add("user", "PROFILE_FIRST\n\nPROFILE_SECOND_PARAGRAPH")
    assert added.get("success"), added
    store.load_from_disk()
    memory = store.format_for_system_prompt("memory")
    profile = store.format_for_system_prompt("user")
    assert memory and "SECOND_PARAGRAPH_TOKEN" in memory and "LATER_ENTRY_TOKEN" in memory
    assert profile and "PROFILE_SECOND_PARAGRAPH" in profile
    return "\n\n".join(["You are Hermes.", memory, profile, "Conversation started: Thursday"])


def _tools():
    from model_tools import get_tool_definitions
    from tools.registry import discover_builtin_tools

    discover_builtin_tools()
    return get_tool_definitions(enabled_toolsets=["hermes-telegram"], quiet_mode=True)


def _names(payload):
    found = []

    def walk(node):
        if isinstance(node, dict):
            spec = node.get("toolSpec")
            if isinstance(spec, dict) and spec.get("name"):
                found.append(spec["name"])
            function = node.get("function")
            if isinstance(function, dict) and function.get("name"):
                found.append(function["name"])
            elif isinstance(node.get("name"), str) and "input_schema" in node or node.get("type") == "function":
                if node.get("name"):
                    found.append(node["name"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload.get("tools"))
    walk(payload.get("toolConfig"))
    # de-dupe preserving order
    seen = []
    for name in found:
        if name not in seen:
            seen.append(name)
    return seen


@pytest.fixture
def kid(ht, home, monkeypatch):
    _policy(home)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    return ht


def _assert_private(payload):
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    for secret in SECRETS:
        assert secret not in blob
    names = _names(payload)
    assert "web_search" in names
    assert "terminal" not in names
    assert "browser_exec" not in names
    assert "policy error" not in blob


def test_multiparagraph_memory_is_removed_on_real_transports(kid, home):
    from agent.prompt_caching import apply_anthropic_cache_control
    from agent.transports import get_transport

    system = _system_prompt(home)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "hello"}]
    tools = _tools()
    cached = apply_anthropic_cache_control(messages)
    cases = {
        "chat_completions": get_transport("chat_completions").build_kwargs("gpt-4o", messages, tools),
        "anthropic_messages": get_transport("anthropic_messages").build_kwargs("claude-sonnet-4-5", messages, tools),
        "codex_responses": get_transport("codex_responses").build_kwargs("gpt-5-codex", messages, tools),
        "bedrock_converse": get_transport("bedrock_converse").build_kwargs(
            "anthropic.claude-sonnet-4-5-20250929-v1:0", messages, tools
        ),
        "openrouter_cache": get_transport("chat_completions").build_kwargs(
            "anthropic/claude-sonnet-4.5", cached, tools, is_openrouter=True
        ),
    }
    for mode, request in cases.items():
        out = kid._llm_request(request=request, session_id="mem")["request"]
        try:
            _assert_private(out)
        except AssertionError as exc:
            raise AssertionError(f"{mode}: {exc}") from exc


def test_quoted_memory_header_does_not_refuse_the_turn(kid, home):
    request = {
        "messages": [
            {"role": "system", "content": "You are Hermes."},
            {"role": "user", "content": "look this up"},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [
                    {
                        "type": "text",
                        "text": "The page title is MEMORY (your personal notes) and nothing else.",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        ],
        "tools": [
            {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "terminal", "parameters": {"type": "object", "properties": {}}}},
        ],
    }
    out = kid._llm_request(request=request, session_id="quote")["request"]
    blob = json.dumps(out, ensure_ascii=False)
    assert "policy error" not in blob
    assert "MEMORY (your personal notes)" in blob
    assert _names(out) == ["web_search"]
