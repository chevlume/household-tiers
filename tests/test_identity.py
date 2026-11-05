"""Host isolation, subagents, cron, and a lost sender after messaging traffic."""

import json


def _family(home):
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid", "telegram:111": "owner"}}),
        encoding="utf-8",
    )


def test_host_process_is_guest(ht, home, monkeypatch):
    _family(home)
    monkeypatch.setenv("HERMES_PLUGIN_HOST_PROCESS", "1")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "111")
    assert ht._identity("s") == ("guest", "host-unknown")
    assert ht._pre_tool_call(tool_name="terminal", args={}, session_id="s")["action"] == "block"


def test_subagent_inherits_the_parent_tier(ht, home, monkeypatch):
    _family(home)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    ht._identity("parent")
    monkeypatch.delenv("HERMES_SESSION_PLATFORM")
    monkeypatch.delenv("HERMES_SESSION_USER_ID")
    ht._subagent_start(parent_session_id="parent", child_session_id="child")
    blocked = ht._pre_tool_call(tool_name="terminal", args={}, session_id="child")
    assert blocked["action"] == "block"
    assert "telegram:222" in blocked["message"]


def test_unknown_sender_after_messaging_is_guest(ht, home, monkeypatch):
    _family(home)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    ht._identity("parent")
    monkeypatch.delenv("HERMES_SESSION_PLATFORM")
    monkeypatch.delenv("HERMES_SESSION_USER_ID")
    tier, who = ht._identity("never-seen")
    assert (tier, who) == ("guest", "unknown")


def test_desktop_and_tui_without_a_sender_are_guest_after_messaging(ht, home, monkeypatch):
    """A local Desktop or TUI turn has no messaging sender once chat has been served."""
    _family(home)
    assert ht._identity("desktop-before") == ("owner", "local")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    ht._identity("chat")
    monkeypatch.delenv("HERMES_SESSION_PLATFORM")
    monkeypatch.delenv("HERMES_SESSION_USER_ID")
    assert ht._identity("desktop-after") == ("guest", "unknown")
    assert ht._identity("tui-after") == ("guest", "unknown")


def test_cron_defaults_to_owner(ht, home, monkeypatch):
    _family(home)
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    assert ht._identity("cron-1")[0] == "owner"


def test_local_without_messaging_is_owner(ht, home):
    _family(home)
    assert ht._identity("") == ("owner", "local")
    assert ht._pre_tool_call(tool_name="terminal", args={}, session_id="local") is None
