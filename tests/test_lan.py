"""Local-network block for non-owner tiers. DNS is stubbed; no HTTP."""

import socket

import pytest

POLICY = {
    "preset": "family",
    "users": {"telegram:222": "kid", "telegram:111": "owner"},
}

_DNS = {
    "example.com": ["93.184.215.14"],
    "router.home.test": ["192.168.1.50"],
    "dual.example": ["1.1.1.1", "10.1.2.3"],
}


def _install_dns(monkeypatch):
    def fake(host, port, *args, **kwargs):
        ips = _DNS.get(host)
        if ips is None:
            raise socket.gaierror(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


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
        ("222", "http://router.home.test/", True),
        ("222", "http://dual.example/", True),
        ("222", "http://missing.example/", True),
    ],
)
def test_local_address_classification(ht, home, monkeypatch, uid, url, blocked):
    import json

    _install_dns(monkeypatch)
    (home / "household-tiers.json").write_text(json.dumps(POLICY), encoding="utf-8")
    assert _block(ht, monkeypatch, uid, url) is blocked


def test_star_tier_is_still_blocked_from_local_urls(ht, home, monkeypatch):
    import json

    _install_dns(monkeypatch)
    (home / "household-tiers.json").write_text(
        json.dumps({
            "preset": "family",
            "users": {"telegram:333": "user", "telegram:111": "owner"},
            "tiers": {"user": ["*"]},
        }),
        encoding="utf-8",
    )
    assert _block(ht, monkeypatch, "333", "http://127.0.0.1/") is True
    assert _block(ht, monkeypatch, "333", "http://93.184.215.14/") is False
    assert _block(ht, monkeypatch, "111", "http://127.0.0.1/") is False


def test_lan_block_is_skipped_when_private_urls_stay_closed(ht, home, monkeypatch):
    import json

    (home / "household-tiers.json").write_text(json.dumps(POLICY), encoding="utf-8")
    monkeypatch.setenv("HERMES_ALLOW_PRIVATE_URLS", "0")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    assert ht._pre_tool_call(tool_name="web_extract", args={"urls": ["http://192.168.1.1/"]}, session_id="s") is None
