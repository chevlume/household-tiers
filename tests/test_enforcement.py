"""Holes that are enforced in-process: star tiers, provider blocks, compaction handoffs."""

import json

SECRET = "OWNER_SECRET_hunter2"
PROVIDER = "EXTERNAL_PROVIDER_NOTE_UNIQUE"
NEW_ENTRY = "SECOND_MEMORY_TOKEN_BBBB"


def _kid(monkeypatch, uid="222"):
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", uid)


def _tools():
    return [
        {"type": "function", "function": {"name": "terminal"}},
        {"type": "function", "function": {"name": "web_search"}},
        {"type": "function", "function": {"name": "memory"}},
    ]


def _names(payload):
    return [(tool.get("function") or {}).get("name") for tool in payload.get("tools") or []]


def test_star_tier_still_strips_memory_and_hides_the_memory_tool(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps({
            "preset": "family",
            "users": {"telegram:222": "kid", "telegram:111": "owner"},
            "tiers": {"kid": ["*"]},
        }),
        encoding="utf-8",
    )
    sep = "═" * 46
    system = f"You are Hermes.\n\n{sep}\nMEMORY (your personal notes) [1%]\n{sep}\n{SECRET}\n"
    request = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": "hi"}], "tools": _tools()}
    _kid(monkeypatch)
    out = ht._llm_request(request=request, session_id="star")["request"]
    blob = json.dumps(out)
    assert SECRET not in blob
    assert "homework" in blob
    assert "terminal" in _names(out)
    assert "memory" not in _names(out)
    assert "policy error" not in blob
    blocked = ht._pre_tool_call(tool_name="memory", args={"action": "read"}, session_id="star")
    assert blocked["action"] == "block"
    assert ht._pre_tool_call(tool_name="terminal", args={"command": "true"}, session_id="star") is None
    _kid(monkeypatch, "111")
    assert ht._llm_request(request=request, session_id="owner") is None
    assert SECRET in request["messages"][0]["content"]


def test_provider_block_after_builtin_boxes_is_removed(ht, home, monkeypatch):
    from tools.memory_tool_store import MemoryStore

    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}}),
        encoding="utf-8",
    )
    _kid(monkeypatch)
    store = MemoryStore(memory_char_limit=4000, user_char_limit=4000)
    assert store.add("memory", "PIN_LINE_ONE").get("success")
    assert store.add("user", "PROFILE_FIRST_LINE").get("success")
    store.load_from_disk()
    system = "\n\n".join([
        "You are Hermes.",
        store.format_for_system_prompt("memory"),
        store.format_for_system_prompt("user"),
        PROVIDER,
        "Conversation started: Thursday",
        "Active Hermes profile: house",
    ])
    request = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": "hi"}],
        "tools": _tools(),
    }
    out = ht._llm_request(request=request, session_id="prov")["request"]
    blob = json.dumps(out["messages"])
    assert PROVIDER not in blob
    assert "PIN_LINE_ONE" not in blob
    assert "Conversation started:" in blob
    assert "Active Hermes profile:" in blob
    assert "policy error" not in blob


def test_provider_block_without_a_builtin_box_is_not_removed(ht, home, monkeypatch):
    """No boxed MEMORY.md / USER.md means there is no boundary around the provider text."""
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}}),
        encoding="utf-8",
    )
    _kid(monkeypatch)
    system = f"You are Hermes.\n\n# ByteRover Memory\n{PROVIDER}\n\nConversation started: Thursday"
    request = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": "hi"}], "tools": _tools()}
    out = ht._llm_request(request=request, session_id="bare")["request"]
    blob = json.dumps(out["messages"])
    assert PROVIDER in blob
    assert "Conversation started:" in blob


def test_compaction_handoff_redacts_entries_and_a_quote_does_not(ht, home, monkeypatch):
    from agent.context_compressor import (
        LEGACY_SUMMARY_PREFIX,
        SUMMARY_PREFIX,
        _MERGED_SUMMARY_DELIMITER,
    )
    from tools.memory_tool_store import MemoryStore

    assert SUMMARY_PREFIX.startswith(ht._SUMMARY_PREFIXES[0])
    assert LEGACY_SUMMARY_PREFIX.startswith(ht._SUMMARY_PREFIXES[1])
    assert _MERGED_SUMMARY_DELIMITER == ht._SUMMARY_MERGE
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}}),
        encoding="utf-8",
    )
    _kid(monkeypatch)
    store = MemoryStore(memory_char_limit=4000, user_char_limit=4000)
    token = "GARAGE_CODE_4821_TOKEN"
    assert store.add("memory", token).get("success")
    tools = _tools()

    def turn(user_text):
        return ht._llm_request(
            request={"messages": [{"role": "system", "content": "base"}, {"role": "user", "content": user_text}], "tools": tools},
            session_id="sum",
        )["request"]

    quoted = turn(f"is the code {token}?")
    assert token in quoted["messages"][-1]["content"]
    assert "web_search" in _names(quoted)
    handoff = turn(SUMMARY_PREFIX + f"\nThe notes said {token}.\n<memory-provider-context>\n{token}\n</memory-provider-context>")
    assert token not in json.dumps(handoff["messages"])
    assert "web_search" in _names(handoff)
    assert "policy error" not in json.dumps(handoff)
    legacy = turn(f"{LEGACY_SUMMARY_PREFIX} still has {token}")
    assert token not in legacy["messages"][-1]["content"]
    merged_text = f"the page quotes {token}\n\n{_MERGED_SUMMARY_DELIMITER}\n{SUMMARY_PREFIX}\nnotes {token}"
    merged = turn(merged_text)
    body = merged["messages"][-1]["content"]
    assert body.startswith(f"the page quotes {token}")
    assert body.count(token) == 1


def test_rewritten_policy_and_memory_are_reread(ht, home, monkeypatch):
    from tools.memory_tool_store import MemoryStore

    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}, "tiers": {"kid": []}}),
        encoding="utf-8",
    )
    _kid(monkeypatch)
    denied = ht._pre_tool_call(tool_name="web_search", args={}, session_id="c")
    assert denied["action"] == "block"
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}}),
        encoding="utf-8",
    )
    assert ht._pre_tool_call(tool_name="web_search", args={}, session_id="c") is None
    store = MemoryStore(memory_char_limit=4000, user_char_limit=4000)
    assert store.add("memory", "FIRST_MEMORY_TOKEN_AAAA").get("success")
    warm = {"messages": [{"role": "system", "content": "FIRST_MEMORY_TOKEN_AAAA"}], "tools": _tools()}
    first = ht._llm_request(request=warm, session_id="c")["request"]
    assert "FIRST_MEMORY_TOKEN_AAAA" not in json.dumps(first)
    assert store.add("memory", NEW_ENTRY).get("success")
    second_req = {"messages": [{"role": "system", "content": f"see {NEW_ENTRY}"}], "tools": _tools()}
    second = ht._llm_request(request=second_req, session_id="c")["request"]
    assert NEW_ENTRY not in json.dumps(second)
