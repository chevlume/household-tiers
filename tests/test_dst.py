"""Wall-clock curfew across the Europe/Madrid spring change on 29 March 2026."""

import datetime as dt

import pytest
from zoneinfo import ZoneInfo


@pytest.mark.parametrize(
    ("utc", "inside"),
    [
        ("2026-03-28T19:30", True),
        ("2026-03-28T20:30", False),
        ("2026-03-29T18:30", True),
        ("2026-03-29T19:30", False),
    ],
)
def test_madrid_dst_window(ht, utc, inside):
    moment = dt.datetime.fromisoformat(utc).replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo("Europe/Madrid"))
    assert ht._inside("08:00-21:00", moment.time()) is inside
