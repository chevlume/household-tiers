"""Curfew through the real post-admission hook."""

import asyncio
import datetime as dt

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.run_inbound_consumer import run_post_admission_hook
from gateway.session import SessionSource, build_session_key


class _Adapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def _runner():
    runner = object.__new__(GatewayRunner)
    adapter = _Adapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._resolve_profile_home_for_source = lambda source: None
    return runner, adapter


async def _turn(gateway, runner, uid, now, chat_type="dm"):
    gateway["mod"]._now = lambda tz, now=now: dt.time(*map(int, now.split(":")))
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=("FAMILY" if chat_type == "group" else "c-" + uid),
        user_id=uid,
        chat_type=chat_type,
    )
    event = MessageEvent(text="hello from the test", source=source, message_id="m1")
    pre = await runner._hm_pre_gateway_dispatch_hook(event, source)
    handled, reply = await run_post_admission_hook(runner, event, source, build_session_key(source))
    await asyncio.sleep(0.05)
    return pre, handled, reply


def test_dispatch_hook_does_not_drop(gateway, family):
    runner, _adapter = _runner()

    async def _go():
        return await _turn(gateway, runner, "222", "15:00")

    pre, handled, reply = asyncio.run(_go())
    assert pre is not None
    assert handled is False
    assert reply is None


@pytest.mark.parametrize(
    ("uid", "now", "chat_type", "blocked"),
    [
        ("222", "15:00", "dm", False),
        ("222", "22:30", "dm", True),
        ("222", "07:59", "dm", True),
        ("999", "22:30", "dm", True),
        ("111", "23:30", "dm", False),
        ("111", "22:30", "group", False),
    ],
)
def test_curfew_matrix(gateway, family, uid, now, chat_type, blocked):
    runner, adapter = _runner()
    _pre, handled, reply = asyncio.run(_turn(gateway, runner, uid, now, chat_type))
    assert handled is blocked
    if uid == "222" and now == "22:30" and chat_type == "dm":
        assert reply
        assert "quiet hours" in reply.lower()
        assert adapter.sent and adapter.sent[0][0] == "PARENTS"
        assert "hello from the test" not in adapter.sent[0][1]


def test_group_curfew_is_silent_but_alerts(gateway, family):
    runner, adapter = _runner()
    _pre, handled, reply = asyncio.run(_turn(gateway, runner, "222", "22:30", "group"))
    assert handled is True
    assert reply is None
    assert len(adapter.sent) == 1
    assert adapter.sent[0][0] == "PARENTS"
