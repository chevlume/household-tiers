"""Owner commands: summary, tier changes, and the /familia alias."""

import json
import os
import types


def _src(user_id):
    return types.SimpleNamespace(platform=types.SimpleNamespace(value="telegram"), user_id=user_id)


def test_owner_sees_summary_and_kid_does_not(ht, family):
    path = family / "household-tiers-audit.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"t": 1, "decision": "block", "who": "telegram:222", "tool": "terminal"},
                {"t": 1, "decision": "block", "who": "telegram:222", "tool": "terminal"},
                {"t": 1, "decision": "block", "who": "telegram:222", "tool": "send_message"},
                {"t": 1, "decision": "curfew", "who": "telegram:222"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    # Those stamps are ancient; retention would drop them. Disable the window
    # for this fixture by writing a long retention before the command reads it.
    policy = json.loads((family / "household-tiers.json").read_text(encoding="utf-8"))
    policy["audit"] = {"enabled": True, "retention_days": 36500}
    (family / "household-tiers.json").write_text(json.dumps(policy), encoding="utf-8")
    # Rewrite stamps to now so they survive even a short window.
    import time

    now = time.time()
    path.write_text(
        "\n".join(
            json.dumps({"t": now, **row})
            for row in (
                {"decision": "block", "who": "telegram:222", "tool": "terminal"},
                {"decision": "block", "who": "telegram:222", "tool": "terminal"},
                {"decision": "block", "who": "telegram:222", "tool": "send_message"},
                {"decision": "curfew", "who": "telegram:222"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    owner = ht._family("", source=_src("111"))
    kid = ht._family("", source=_src("222"))
    assert "terminal×2" in owner
    assert "send_message×1" in owner
    assert "outside hours 1" in owner
    assert "Only the owner" in kid


def test_owner_changes_tier_and_it_applies(ht, family, monkeypatch):
    reply = ht._family("tier telegram:444 kid", source=_src("111"))
    assert reply == "Done: telegram:444 is now kid."
    saved = json.loads((family / "household-tiers.json").read_text(encoding="utf-8"))
    assert saved["users"]["telegram:444"] == "kid"
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "444")
    blocked = ht._pre_tool_call(tool_name="terminal", args={}, session_id="s444")
    assert blocked["action"] == "block"


def test_kid_cannot_change_a_tier(ht, family):
    reply = ht._family("tier telegram:222 owner", source=_src("222"))
    assert "Only the owner" in reply
    saved = json.loads((family / "household-tiers.json").read_text(encoding="utf-8"))
    assert saved["users"]["telegram:222"] == "kid"


def test_owner_cannot_change_own_tier(ht, family):
    reply = ht._family("tier telegram:111 kid", source=_src("111"))
    assert "own tier" in reply.lower()
    saved = json.loads((family / "household-tiers.json").read_text(encoding="utf-8"))
    assert saved["users"]["telegram:111"] == "owner"


def test_unknown_tier_is_rejected(ht, family):
    reply = ht._family("tier telegram:333 superadmin", source=_src("111"))
    assert "Unknown tier" in reply
    saved = json.loads((family / "household-tiers.json").read_text(encoding="utf-8"))
    assert saved["users"]["telegram:333"] == "user"


def test_familia_is_an_alias(ht, family):
    assert ht._family("tier telegram:555 user", source=_src("111")).startswith("Done:")
    # The same handler is registered under both names.
    class Ctx:
        def __init__(self):
            self.commands = {}

        def register_command(self, name, handler, **kwargs):
            self.commands[name] = handler

        def register_hook(self, *args, **kwargs):
            pass

        def register_middleware(self, *args, **kwargs):
            pass

    ctx = Ctx()
    ht.register(ctx)
    assert ctx.commands["family"] is ctx.commands["familia"]
    assert ctx.commands["familia"]("forget telegram:555", source=_src("111")).startswith("Forgot")


def test_local_cli_is_treated_as_owner(ht, family):
    assert "No events" in ht._family("")


def test_bad_command_returns_usage(ht, family):
    assert ht._family("nope", source=_src("111")).startswith("Usage:")
