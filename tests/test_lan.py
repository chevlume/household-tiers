"""Local-network block for non-owner tiers. No DNS and no HTTP."""

import pytest

POLICY = {
    "preset": "family",
    "users": {"telegram:222": "kid", "telegram:111": "owner"},
}


def _block(ht, monkeypatch, uid, url):
    monkeypatch.setenv("HERMES_ALLOW_PRIVATE_URLS", "1")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", uid)
    result = ht._pre_tool_call(tool_name="web_extract", args={"urls": [url]}, session_id="s" + uid)
    return isinstance(result, dict) and result.get("action") == "block"


@pytest.mark.parametrize(
    ("uid", "url", "blocked"),
    [
        ("222", "http://192.168.1.1/", True),
        ("222", "http://127.0.0.1:8123/", True),
        ("222", "http://93.184.215.14/", False),
        ("111", "http://192.168.1.1/", False),
        ("111", "http://127.0.0.1:8123/", False),
        ("222", "http://2130706433/", True),
        ("222", "http://0177.0.0.1/", True),
        ("222", "http://[::1]/", True),
        ("222", "http://localhost/", True),
        ("222", "http://192.168.1.1.nip.io/", True),
        ("222", "http://93.184.215.14.nip.io/", False),
        ("222", "http://unparsed.nip.io/", True),
        ("222", "http://example.com/", False),
    ],
)
def test_local_address_classification(ht, home, monkeypatch, uid, url, blocked):
    import json

    (home / "household-tiers.json").write_text(json.dumps(POLICY), encoding="utf-8")
    assert _block(ht, monkeypatch, uid, url) is blocked


def test_lan_block_is_skipped_when_private_urls_stay_closed(ht, home, monkeypatch):
    import json

    (home / "household-tiers.json").write_text(json.dumps(POLICY), encoding="utf-8")
    monkeypatch.setenv("HERMES_ALLOW_PRIVATE_URLS", "0")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    assert ht._pre_tool_call(tool_name="web_extract", args={"urls": ["http://192.168.1.1/"]}, session_id="s") is None
