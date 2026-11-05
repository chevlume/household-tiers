"""A fresh HERMES_HOME loads the plugin and enforces a tier."""

import json
import os
from pathlib import Path


def test_fresh_home_loads_and_blocks(gateway, monkeypatch):
    (gateway["home"] / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    from gateway.session_context import set_session_vars

    set_session_vars(platform="telegram", user_id="222", chat_id="c-222", session_key="k")
    results = gateway["plugins"].invoke_hook(
        "pre_tool_call", tool_name="terminal", args={}, session_id="fresh", task_id="t"
    )
    assert any(isinstance(item, dict) and item.get("action") == "block" for item in results)
    hooks = gateway["plugins"].get_plugin_manager()._hooks
    for name in ("pre_tool_call", "subagent_start", "pre_gateway_dispatch", "post_gateway_admission"):
        assert hooks.get(name), name
    assert (gateway["home"] / "plugins" / "household-tiers" / "plugin.yaml").is_file()
    assert os.environ["HERMES_HOME"] == str(gateway["home"])
    assert Path(os.environ["HERMES_HOME"]).name == "hermes"
