"""The user tier can read files, but not the household directory."""

import json
import os


def test_user_cannot_read_household_files(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:333": "user"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "333")
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir()
    memory.write_text("SECRET_MEMORY_FILE", encoding="utf-8")
    sessions = home / "sessions"
    sessions.mkdir()
    audit = home / "household-tiers-audit.jsonl"
    for path in (memory, sessions, audit, home / "household-tiers.json"):
        blocked = ht._pre_tool_call(tool_name="read_file", args={"path": str(path)}, session_id="u")
        assert blocked["action"] == "block", path
        searched = ht._pre_tool_call(tool_name="search_files", args={"path": str(path)}, session_id="u")
        assert searched["action"] == "block", path
    outside = ht._pre_tool_call(tool_name="read_file", args={"path": "/tmp/notes.txt"}, session_id="u")
    assert outside is None
    parent = ht._pre_tool_call(tool_name="search_files", args={"path": str(home.parent)}, session_id="u")
    assert parent["action"] == "block"
    web = ht._pre_tool_call(tool_name="web_search", args={"query": "weather"}, session_id="u")
    assert web is None


def test_relative_paths_follow_the_session_base_not_the_process_cwd(ht, tmp_path, monkeypatch):
    session = tmp_path / "userhome"
    hermes = session / ".hermes"
    hermes.mkdir(parents=True)
    (hermes / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:333": "user"}}),
        encoding="utf-8",
    )
    (session / "projects").mkdir()
    (session / "projects" / "readme.md").write_text("ok", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setenv("TERMINAL_CWD", str(session))
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "333")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    blocked = ht._pre_tool_call(tool_name="read_file", args={"path": ".hermes/memories/MEMORY.md"}, session_id="u")
    assert blocked["action"] == "block"
    omitted = ht._pre_tool_call(tool_name="search_files", args={"query": "secret"}, session_id="u")
    assert omitted["action"] == "block"
    dotted = ht._pre_tool_call(tool_name="search_files", args={"path": ".", "query": "secret"}, session_id="u")
    assert dotted["action"] == "block"
    allowed = ht._pre_tool_call(tool_name="read_file", args={"path": "projects/readme.md"}, session_id="u")
    assert allowed is None


def test_path_compare_uses_normcase(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:333": "user"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "333")
    monkeypatch.setattr(os.path, "normcase", lambda value: value.lower() if isinstance(value, str) else value)
    folded = str(home).swapcase()
    blocked = ht._pre_tool_call(
        tool_name="read_file",
        args={"path": folded + "/memories/MEMORY.md"},
        session_id="u",
    )
    assert blocked["action"] == "block"


def test_memory_tiers_must_be_a_list(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps({"users": {"telegram:333": "user"}, "memory_tiers": "owner"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "111")
    blocked = ht._pre_tool_call(tool_name="web_search", args={}, session_id="bad")
    assert blocked["action"] == "block"
    assert "policy error" in blocked["message"]


def test_family_hours_default_to_utc_and_timezone_is_configurable(ht, home):
    (home / "household-tiers.json").write_text(json.dumps({"preset": "family"}), encoding="utf-8")
    assert ht._policy()["hours"]["kid"]["tz"] == "UTC"
    assert ht._policy()["timezone"] == "UTC"
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "timezone": "Europe/Madrid"}),
        encoding="utf-8",
    )
    assert ht._policy()["hours"]["kid"]["tz"] == "Europe/Madrid"
    assert ht._policy()["hours"]["guest"]["tz"] == "Europe/Madrid"
