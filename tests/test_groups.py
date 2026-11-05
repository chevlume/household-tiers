"""Shared group sessions refuse non-owner tiers unless the key is per user."""

import asyncio

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.run_inbound_consumer import run_post_admission_hook
from gateway.session import SessionSource, build_session_key


@pytest.mark.parametrize(
    ("uid", "alt", "per_user", "blocked"),
    [
        ("K", None, False, True),
        ("K", None, True, False),
        ("K", "K-alt", False, True),
        ("K", "K-alt", True, False),
        ("P", None, False, False),
        ("P", None, True, False),
    ],
)
def test_shared_group(gateway, family, monkeypatch, uid, alt, per_user, blocked):
    # family fixture writes telegram:111/222. This case uses its own map.
    (gateway["home"] / "household-tiers.json").write_text(
        '{"preset":"family","users":{"telegram:K":"kid","telegram:P":"owner"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway["mod"], "_now", lambda tz: __import__("datetime").time(15, 0))
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._resolve_profile_home_for_source = lambda source: None

    async def _go():
        source = SessionSource(
            platform=Platform.TELEGRAM, chat_id="FAM", user_id=uid, user_id_alt=alt, chat_type="group"
        )
        key = build_session_key(source, group_sessions_per_user=per_user)
        handled, reply = await run_post_admission_hook(
            runner, MessageEvent(text="SECRET_GROUP_TEXT", source=source, message_id="m"), source, key
        )
        return handled, reply

    handled, reply = asyncio.run(_go())
    assert handled is blocked
    if reply:
        assert "SECRET_GROUP_TEXT" not in reply
