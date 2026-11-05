"""Shared Slack threads refuse a kid unless the thread key is per user."""

import asyncio

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.run_inbound_consumer import run_post_admission_hook
from gateway.session import SessionSource, build_session_key


@pytest.mark.parametrize(
    ("uid", "per_user", "blocked"),
    [("K", False, True), ("K", True, False), ("P", False, False), ("P", True, False)],
)
def test_shared_thread(gateway, monkeypatch, uid, per_user, blocked):
    (gateway["home"] / "household-tiers.json").write_text(
        '{"preset":"family","users":{"slack:K":"kid","slack:P":"owner"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway["mod"], "_now", lambda tz: __import__("datetime").time(15, 0))
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._resolve_profile_home_for_source = lambda source: None

    async def _go():
        source = SessionSource(
            platform=Platform.SLACK, chat_id="C1", user_id=uid, chat_type="group", thread_id="T1"
        )
        key = build_session_key(source, thread_sessions_per_user=per_user)
        handled, _reply = await run_post_admission_hook(
            runner, MessageEvent(text="hi", source=source, message_id="m"), source, key
        )
        return handled

    assert asyncio.run(_go()) is blocked
