"""Homework note is stable for the kid tier and absent for the others."""

import json


def test_homework_note_is_stable_and_tier_specific(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid", "telegram:333": "user", "telegram:111": "owner"}}),
        encoding="utf-8",
    )
    request = {
        "messages": [{"role": "system", "content": "base"}, {"role": "user", "content": "2+2?"}],
        "tools": [{"type": "function", "function": {"name": "web_search"}}],
    }

    def system_for(uid):
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
        monkeypatch.setenv("HERMES_SESSION_USER_ID", uid)
        result = ht._llm_request(request=request, session_id="s" + uid)
        return (result or {"request": request})["request"]["messages"][0]["content"]

    first, second = system_for("222"), system_for("222")
    assert "homework" in first
    assert first == second
    assert request["messages"][0]["content"] == "base"
    assert "homework" not in system_for("333")
    assert "homework" not in system_for("111")


def test_messages_template_is_configurable(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps(
            {
                "preset": "family",
                "users": {"telegram:222": "kid"},
                "messages": {"tool_blocked": "bloqueado {tool} para {who}"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    blocked = ht._pre_tool_call(tool_name="terminal", args={}, session_id="s")
    assert blocked["message"] == "bloqueado terminal para telegram:222"
