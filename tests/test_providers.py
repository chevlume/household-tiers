"""Owner memory is stripped from the request shapes Hermes actually sends.

Shapes come from the pinned adapters: Anthropic ``system`` (string and
cache_control blocks) plus top-level tool names, and Codex ``instructions`` /
``input`` plus Responses tool names.
"""

import json

SECRET = "OWNER_SECRET_hunter2"
RECALL = "RECALL_SECRET_hunter2"


def _openai_messages():
    sep = "═" * 46
    system = (
        f"You are Hermes.\n\n{sep}\nMEMORY (your personal notes) [1%]\n{sep}\n{SECRET}\n\n"
        f"{sep}\nUSER PROFILE (who the user is) [1%]\n{sep}\nPROFILE_{SECRET}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"hello\n<memory-context>{RECALL}</memory-context>"},
    ]


def _openai_tools():
    return [
        {"type": "function", "function": {"name": "terminal", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "web_extract", "parameters": {"type": "object"}}},
    ]


def _policy(home):
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid", "telegram:111": "owner"}}),
        encoding="utf-8",
    )


def _as_kid(monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")


def _assert_clean(payload, secret=SECRET):
    blob = json.dumps(payload, ensure_ascii=False)
    assert secret not in blob
    assert RECALL not in blob
    assert "MEMORY (your personal notes)" not in blob
    assert "USER PROFILE (who the user is)" not in blob
    names = []
    for tool in payload.get("tools") or []:
        names.append(tool.get("name") or (tool.get("function") or {}).get("name"))
    assert "web_search" in names
    assert "terminal" not in names


def test_chat_completions_shape(ht, home, monkeypatch):
    _policy(home)
    _as_kid(monkeypatch)
    original = {"messages": _openai_messages(), "tools": _openai_tools()}
    out = ht._llm_request(request=original, session_id="chat")["request"]
    _assert_clean(out)
    assert original["messages"][0]["content"].count(SECRET) == 2


def test_anthropic_system_string_and_tool_names(ht, home, monkeypatch):
    from agent.anthropic_message_convert import convert_messages_to_anthropic, convert_tools_to_anthropic

    _policy(home)
    _as_kid(monkeypatch)
    system, messages = convert_messages_to_anthropic(_openai_messages())
    assert isinstance(system, str) and SECRET in system
    request = {"model": "claude-sonnet-4-5", "max_tokens": 256, "system": system, "messages": messages, "tools": convert_tools_to_anthropic(_openai_tools())}
    assert request["tools"][0]["name"] == "terminal"
    out = ht._llm_request(request=request, session_id="anth")["request"]
    _assert_clean(out)
    assert "homework" in out["system"]


def test_anthropic_system_blocks_keep_cache_control(ht, home, monkeypatch):
    from agent.anthropic_message_convert import convert_messages_to_anthropic, convert_tools_to_anthropic

    _policy(home)
    _as_kid(monkeypatch)
    sep = "═" * 46
    boxed = f"You are Hermes.\n\n{sep}\nMEMORY (your personal notes) [1%]\n{sep}\n{SECRET}\n\n"
    messages = [
        {"role": "system", "content": [{"type": "text", "text": boxed, "cache_control": {"type": "ephemeral"}}]},
        {"role": "user", "content": [{"type": "text", "text": f"hello\n<memory-context>{RECALL}</memory-context>"}]},
    ]
    system, converted = convert_messages_to_anthropic(messages)
    assert isinstance(system, list) and system[0]["cache_control"]["type"] == "ephemeral"
    assert SECRET in system[0]["text"]
    request = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 256,
        "system": system,
        "messages": converted,
        "tools": convert_tools_to_anthropic(_openai_tools()),
    }
    out = ht._llm_request(request=request, session_id="blocks")["request"]
    _assert_clean(out)
    assert out["system"][0]["cache_control"]["type"] == "ephemeral"
    assert "homework" in out["system"][0]["text"]


def test_codex_instructions_and_responses_tools(ht, home, monkeypatch):
    from agent.codex_responses_adapter import _chat_messages_to_responses_input, _responses_tools

    _policy(home)
    _as_kid(monkeypatch)
    chat = _openai_messages()
    instructions = chat[0]["content"]
    converted = _chat_messages_to_responses_input(chat[1:])
    tools = _responses_tools(_openai_tools())
    assert tools[0]["name"] == "terminal" and "function" not in tools[0]
    request = {"model": "gpt-5-codex", "instructions": instructions, "input": converted, "tools": tools, "store": False}
    out = ht._llm_request(request=request, session_id="codex")["request"]
    _assert_clean(out)
    assert "homework" in out["instructions"]
    assert out["store"] is False


def test_leftover_memory_header_refuses_the_request(ht, home, monkeypatch):
    _policy(home)
    _as_kid(monkeypatch)
    request = {
        "messages": [{"role": "user", "content": "see MEMORY (your personal notes) still here"}],
        "tools": _openai_tools(),
    }
    out = ht._llm_request(request=request, session_id="left")["request"]
    blob = json.dumps(out)
    assert "MEMORY (your personal notes)" not in blob
    assert "policy error" in blob
    assert "tools" not in out


def test_hide_tools_env_cannot_disable_memory_filtering(ht, home, monkeypatch):
    _policy(home)
    _as_kid(monkeypatch)
    monkeypatch.setenv("HOUSEHOLD_TIERS_HIDE_TOOLS", "0")

    class Ctx:
        def __init__(self):
            self.middleware = {}

        def register_command(self, *args, **kwargs):
            pass

        def register_hook(self, *args, **kwargs):
            pass

        def register_middleware(self, kind, callback):
            self.middleware[kind] = callback

    ctx = Ctx()
    ht.register(ctx)
    assert "llm_request" in ctx.middleware
    out = ctx.middleware["llm_request"](request={"messages": _openai_messages(), "tools": _openai_tools()}, session_id="env")
    _assert_clean(out["request"])
