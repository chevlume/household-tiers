"""Hook overhead on a small in-memory request. Warm up, then 200 calls."""

import json
import math
import time


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def test_pre_tool_call_p95_under_2ms(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}, "audit": {"enabled": True, "retention_days": 30}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    for _ in range(20):
        ht._pre_tool_call(tool_name="web_search", args={}, session_id="warm")
    samples = []
    for _ in range(200):
        start = time.perf_counter()
        result = ht._pre_tool_call(tool_name="web_search", args={}, session_id="warm")
        samples.append(time.perf_counter() - start)
        assert result is None
    assert _p95(samples) <= 0.002


def test_llm_request_p95_under_2ms(ht, home, monkeypatch):
    (home / "household-tiers.json").write_text(
        json.dumps({"preset": "family", "users": {"telegram:222": "kid"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "222")
    request = {
        "messages": [{"role": "system", "content": "base " * 40}, {"role": "user", "content": "hi"}],
        "tools": [
            {"type": "function", "function": {"name": name}}
            for name in ("terminal", "web_search", "web_extract", "memory", "todo_list")
        ],
    }
    for _ in range(20):
        ht._llm_request(request=request, session_id="warm")
    samples = []
    for _ in range(200):
        start = time.perf_counter()
        result = ht._llm_request(request=request, session_id="warm")
        samples.append(time.perf_counter() - start)
        assert result is not None
    assert _p95(samples) <= 0.002
