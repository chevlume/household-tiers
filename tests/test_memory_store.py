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


def _visible(payload):
    return _names(payload), "policy error" in json.dumps(payload, ensure_ascii=False, default=str)


def test_guessing_an_owner_entry_does_not_change_the_turn(kid, home):
    """A kid who types an exact entry must get the same outcome as any other text."""
    from tools.memory_tool_store import MemoryStore

    store = MemoryStore(memory_char_limit=8000, user_char_limit=8000)
    entry = "wifi casa SECRETA01"
    added = store.add("memory", entry)
    assert added.get("success"), added
    added = store.add("user", "Prefers concise answers")
    assert added.get("success"), added
    store.load_from_disk()
    system = "\n\n".join([
        "You are Hermes.",
        store.format_for_system_prompt("memory"),
        store.format_for_system_prompt("user"),
    ])
    tools = [
        {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "terminal", "parameters": {"type": "object", "properties": {}}}},
    ]

    def turn(user_text, prior=None):
        messages = [{"role": "system", "content": system}]
        if prior:
            messages.extend(prior)
        messages.append({"role": "user", "content": user_text})
        return kid._llm_request(
            request={"messages": messages, "tools": tools},
            session_id="guess",
        )["request"]

    guessed = turn("¿es 'wifi casa SECRETA01' la clave?")
    plain = turn("hello")
    assert _visible(guessed) == _visible(plain)
    assert "policy error" not in json.dumps(guessed)
    assert entry in json.dumps(guessed["messages"][-1])
    follow = turn("ayúdame con mates", prior=[{"role": "user", "content": "¿es 'wifi casa SECRETA01' la clave?"}])
    assert _visible(follow) == _visible(plain)
    page = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "what does this page say?"},
            {"role": "tool", "tool_call_id": "call_1", "content": "The guide says: Prefers concise answers."},
        ],
        "tools": tools,
    }
    looked = kid._llm_request(request=page, session_id="guess")["request"]
    assert _visible(looked) == _visible(plain)
    assert "Prefers concise answers" in json.dumps(looked["messages"][-1])
    boxed = store.format_for_system_prompt("memory")
    pasted = turn(boxed + "\n\nwhat is that?")
    assert _visible(pasted) == _visible(plain)
    assert "MEMORY (your personal notes)" in pasted["messages"][-1]["content"]


def test_real_system_prompt_keeps_sections_after_memory(kid, home, monkeypatch):
    """Hermes joins the timestamp, profile line, and runtime block after memory."""
    from types import SimpleNamespace

    from agent.system_prompt import build_system_prompt
    from tools.memory_tool_store import MemoryStore

    store = MemoryStore(memory_char_limit=8000, user_char_limit=8000)
    added = store.add("memory", "PIN_LINE_ONE\n\nSECOND_PARAGRAPH_TOKEN")
    assert added.get("success"), added
    added = store.add("memory", "LATER_ENTRY_TOKEN")
    assert added.get("success"), added
    added = store.add("user", "PROFILE_FIRST\n\nPROFILE_SECOND_PARAGRAPH")
    assert added.get("success"), added
    store.load_from_disk()
    agent = SimpleNamespace(
        load_soul_identity=False,
        skip_context_files=True,
        valid_tool_names=[],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _execution_guidance=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=store,
        _memory_manager=SimpleNamespace(
            build_system_prompt=lambda: "EXTERNAL_PROVIDER_NOTE keeps this section.\n\nSECOND_PARAGRAPH_TOKEN",
        ),
        _memory_enabled=True,
        _user_profile_enabled=True,
        _bot_mode_protocol=False,
        model="gpt-4o",
        provider="openai",
        platform="cli",
        pass_session_id=False,
        session_id="demo",
        _emit_diagnostic_status=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "agent.prompt_builder.build_environment_hints",
        lambda: "HOST_HINT_TOKEN",
    )
    prompt = build_system_prompt(agent)
    assert "SECOND_PARAGRAPH_TOKEN" in prompt
    assert "Conversation started:" in prompt
    assert "Active Hermes profile:" in prompt
    assert "HOST_HINT_TOKEN" in prompt
    assert "# Hermes runtime environment" in prompt
    assert "EXTERNAL_PROVIDER_NOTE" in prompt
    from agent.transports import get_transport

    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": "hello"}]
    tools = [
        {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "terminal", "parameters": {"type": "object", "properties": {}}}},
    ]
    request = get_transport("chat_completions").build_kwargs("gpt-4o", messages, tools)
    out = kid._llm_request(request=request, session_id="prompt")["request"]
    blob = json.dumps(out, ensure_ascii=False, default=str)
    for secret in SECRETS:
        assert secret not in blob
    assert "Conversation started:" in blob
    assert "Active Hermes profile:" in blob
    assert "HOST_HINT_TOKEN" in blob
    assert "# Hermes runtime environment" in blob
    # The provider block is joined after the built-in boxes. It goes with them.
    assert "EXTERNAL_PROVIDER_NOTE" not in blob
    assert "policy error" not in blob
    assert "web_search" in _names(out)


def test_entry_split_does_not_use_memory_store_parse(kid, home, monkeypatch):
    """A core refactor of MemoryStore._parse_entries must not drop entry text."""
    from tools.memory_tool_store import ENTRY_DELIMITER, MemoryStore

    secret = "code § 12345"
    later = "LATER_DELIMITER_ENTRY"
    root = home / "memories"
    root.mkdir(parents=True)
    (root / "MEMORY.md").write_text(ENTRY_DELIMITER.join([secret, later]), encoding="utf-8")
    calls = {"n": 0}

    def empty(raw):
        calls["n"] += 1
        return []

    monkeypatch.setattr(MemoryStore, "_parse_entries", staticmethod(empty))
    sep = "═" * 46
    system = (
        f"You are Hermes.\n\n{sep}\nMEMORY (your personal notes)\n{sep}\n{secret}\n§\n{later}\n\n"
        f"# Hermes runtime environment\nskill text {secret}"
    )
    out = kid._llm_request(
        request={"messages": [{"role": "system", "content": system}, {"role": "user", "content": "hello"}]},
        session_id="split",
    )["request"]
    blob = json.dumps(out, ensure_ascii=False)
    assert calls["n"] == 0
    assert secret not in blob
    assert later not in blob
    assert "# Hermes runtime environment" in blob


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
