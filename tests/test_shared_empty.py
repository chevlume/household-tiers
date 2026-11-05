"""A shared session with no sender id is refused for a non-owner tier."""

import asyncio
import json

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.run_inbound_consumer import run_post_admission_hook
from gateway.session import SessionSource, build_session_key


def test_empty_sender_in_a_shared_group_is_refused(gateway, monkeypatch):
    (gateway["home"] / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:111": "owner"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway["mod"], "_now", lambda tz: __import__("datetime").time(15, 0))
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._resolve_profile_home_for_source = lambda source: None
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="FAM", user_id="", chat_type="group")
    key = build_session_key(source, group_sessions_per_user=False)

    async def _go():
        return await run_post_admission_hook(
            runner, MessageEvent(text="hi", source=source, message_id="m"), source, key
        )

    handled, reply = asyncio.run(_go())
    assert handled is True
    assert reply
