"""Audit log: no message text, retention, forget, and the legacy filename."""

import json
import time


SECRET = "SECRET_MESSAGE_BODY_9f3a2c"


def _policy(home, **extra):
    body = {
        "preset": "family",
        "users": {"telegram:111": "owner", "telegram:222": "kid"},
        "audit": {"enabled": True, "retention_days": 30},
    }
    body.update(extra)
    (home / "household-tiers.json").write_text(json.dumps(body), encoding="utf-8")


def test_audit_never_stores_message_text_or_tool_args(ht, home, monkeypatch):
    _policy(home)
    monkeypatch.setattr(ht, "_now", lambda tz: __import__("datetime").time(22, 30))
    ht._post_admission(
        session_key="k",
        platform="telegram",
        source={"user_id": "222", "chat_type": "dm"},
        text=SECRET,
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    ht._pre_tool_call(tool_name="terminal", args={"command": SECRET, "url": SECRET}, session_id="s")
    ht._pre_tool_call(
        tool_name="web_extract",
        args={"urls": [f"http://192.168.1.1/{SECRET}"]},
        session_id="s",
    )
    blob = (home / "household-tiers-audit.jsonl").read_text(encoding="utf-8")
    assert SECRET not in blob
    assert "command" not in blob
    for line in blob.splitlines():
        rec = json.loads(line)
        assert set(rec) <= {"t", "decision", "who", "tier", "tool", "why", "session", "window", "target", "count"}


def test_retention_purges_on_family_and_on_register(ht, home):
    _policy(home)
    now = time.time()
    rows = [
        {"t": now - 40 * 86400, "decision": "block", "who": "telegram:222", "tool": "terminal", "why": "tool_denied"},
        {"t": now - 86400, "decision": "curfew", "who": "telegram:222", "why": "curfew"},
    ]
    (home / "household-tiers-audit.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    class Ctx:
        def register_command(self, *args, **kwargs):
            pass

        def register_hook(self, *args, **kwargs):
            pass

        def register_middleware(self, *args, **kwargs):
            pass

    ht.register(Ctx())
    kept = [
        json.loads(line)
        for line in (home / "household-tiers-audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(kept) == 1
    assert kept[0]["decision"] == "curfew"
    # A second stale line written after register is removed by /family.
    with open(home / "household-tiers-audit.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"t": now - 31 * 86400, "decision": "block", "who": "telegram:222", "tool": "terminal", "why": "tool_denied"}) + "\n")
    ht._family("", source=None)
    kept = [json.loads(line) for line in (home / "household-tiers-audit.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert all(row["t"] > now - 30 * 86400 for row in kept)


def test_forget_deletes_one_person(ht, home):
    _policy(home, audit={"enabled": True, "retention_days": 365})
    now = time.time()
    rows = [
        {"t": now, "decision": "block", "who": "telegram:222", "tool": "terminal", "why": "tool_denied"},
        {"t": now, "decision": "curfew", "who": "telegram:333", "why": "curfew"},
        {"t": now, "decision": "tier_change", "who": "telegram:111", "target": "telegram:222", "tier": "kid", "why": "tier_change"},
    ]
    (home / "household-tiers-audit.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    import types

    source = types.SimpleNamespace(platform=types.SimpleNamespace(value="telegram"), user_id="111")
    reply = ht._family("forget telegram:222", source=source)
    assert reply.startswith("Forgot")
    blob = (home / "household-tiers-audit.jsonl").read_text(encoding="utf-8")
    records = [json.loads(line) for line in blob.splitlines() if line.strip()]
    assert all(row.get("who") != "telegram:222" and row.get("target") != "telegram:222" for row in records)
    assert any(row.get("who") == "telegram:333" for row in records)


def test_audit_can_be_disabled(ht, home, monkeypatch):
    _policy(home, audit={"enabled": False, "retention_days": 30})
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    blocked = ht._pre_tool_call(tool_name="terminal", args={}, session_id="s")
    assert blocked["action"] == "block"
    assert not (home / "household-tiers-audit.jsonl").exists()


def test_legacy_state_files_are_a_fallback(ht, home, monkeypatch):
    legacy_policy = home / f"{ht._legacy_stem()}.json"
    legacy_audit = home / f"{ht._legacy_stem()}-audit.jsonl"
    legacy_policy.write_text(
        json.dumps({"preset": "family", "users": {"telegram:111": "owner", "telegram:222": "kid"}, "audit": {"retention_days": 365}}),
        encoding="utf-8",
    )
    now = time.time()
    legacy_audit.write_text(
        json.dumps({"t": now, "decision": "block", "who": "telegram:222", "tool": "terminal", "why": "tool_denied"}) + "\n",
        encoding="utf-8",
    )
    import types

    source = types.SimpleNamespace(platform=types.SimpleNamespace(value="telegram"), user_id="111")
    summary = ht._family("", source=source)
    assert "terminal×1" in summary
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    assert ht._pre_tool_call(tool_name="terminal", args={}, session_id="leg")["action"] == "block"
    ht._family("tier telegram:222 user", source=source)
    assert (home / "household-tiers.json").exists()
    assert json.loads((home / "household-tiers.json").read_text(encoding="utf-8"))["users"]["telegram:222"] == "user"
