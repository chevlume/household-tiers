"""External memory-context blocks are removed for non-owner tiers."""

import json


def test_recall_block_is_removed(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    request = {
        "messages": [
            {"role": "system", "content": "x"},
            {"role": "user", "content": "hello\n<memory-context>parent said: SECRET_RECALL</memory-context>"},
        ],
        "tools": [{"type": "function", "function": {"name": "terminal"}}, {"type": "function", "function": {"name": "web_search"}}],
    }
    out = ht._llm_request(request=request, session_id="s")
    blob = json.dumps(out["request"]["messages"])
    assert "SECRET_RECALL" not in blob
    assert [tool["function"]["name"] for tool in out["request"]["tools"]] == ["web_search"]
    assert request["messages"][1]["content"].count("SECRET_RECALL") == 1
