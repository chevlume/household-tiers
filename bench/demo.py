#!/usr/bin/env python3
"""Write docs/demo.md from one real run of the plugin.

Uses docs/demo-config.json. No model call. Hermes must be importable
(the pinned checkout's virtualenv is enough):

    python bench/demo.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "docs" / "demo.md"
CONFIG = ROOT / "docs" / "demo-config.json"
HERMES_PIN = "ed1afb082eb3e46cf2edae9d8f68fb67b09ee4df"


def _hermes_commit() -> str:
    for candidate in (os.environ.get("HERMES_CHECKOUT", ""), "/tmp/hermes-agent"):
        if candidate and (Path(candidate) / ".git").exists():
            try:
                out = subprocess.run(
                    ["git", "-C", candidate, "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return out.stdout.strip()
            except (OSError, subprocess.CalledProcessError):
                continue
    return HERMES_PIN


def _load_plugin(home: Path):
    os.environ["HERMES_HOME"] = str(home)
    for key in ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_USER_ID", "HERMES_PLUGIN_HOST_PROCESS"):
        os.environ.pop(key, None)
    name = "household_tiers_demo"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _transcript(ht) -> str:
    home = Path(os.environ["HERMES_HOME"])
    policy = json.loads(CONFIG.read_text(encoding="utf-8"))
    (home / "household-tiers.json").write_text(json.dumps(policy), encoding="utf-8")

    from agent.system_prompt import build_system_prompt
    from tools.memory_tool_store import MemoryStore

    store = MemoryStore(memory_char_limit=8000, user_char_limit=8000)
    added = store.add("memory", "The garage code is 4821.\n\nSpare key is under the blue pot.")
    if not added.get("success"):
        raise SystemExit(f"MemoryStore.add failed: {added}")
    store.load_from_disk()
    agent = SimpleNamespace(
        load_soul_identity=False,
        skip_context_files=True,
        valid_tool_names=[],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _execution_guidance=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=store,
        _memory_manager=None,
        _memory_enabled=True,
        _user_profile_enabled=True,
        _bot_mode_protocol=False,
        model="gpt-4o",
        provider="openai",
        platform="telegram",
        pass_session_id=False,
        session_id="demo",
        _emit_diagnostic_status=lambda *_a, **_k: None,
    )
    prompt = build_system_prompt(agent)
    tools = [
        {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "terminal", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {}}}},
    ]

    def as_person(user_id: str) -> None:
        os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
        os.environ["HERMES_SESSION_USER_ID"] = user_id

    def turn(user_id: str, text: str) -> dict:
        as_person(user_id)
        request = {
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            "tools": tools,
        }
        result = ht._llm_request(request=request, session_id=f"demo-{user_id}")
        return request if result is None else result["request"]

    def tool_names(request: dict) -> list:
        return [(tool.get("function") or {}).get("name") for tool in request.get("tools") or []]

    lines = []
    owner = turn("100001", "what is the garage code?")
    lines.append("--- owner 100001 (middleware returns None) ---")
    lines.append(f"tools: {tool_names(owner)}")
    lines.append(f"system has 4821: {'4821' in owner['messages'][0]['content']}")
    lines.append(f"system has Conversation started: {'Conversation started:' in owner['messages'][0]['content']}")

    kid = turn("200002", "what is the garage code?")
    body = kid["messages"][0]["content"]
    lines.append("--- kid 200002 ---")
    lines.append(f"tools: {tool_names(kid)}")
    lines.append(f"system has 4821: {'4821' in body}")
    lines.append(f"system has blue pot: {'blue pot' in body}")
    lines.append(f"system has Conversation started: {'Conversation started:' in body}")
    lines.append(f"system has Active Hermes profile: {'Active Hermes profile:' in body}")
    lines.append(f"user text: {kid['messages'][-1]['content']}")
    lines.append(f"refused: {'policy error' in json.dumps(kid)}")

    guess = turn("200002", "is the garage code 4821?")
    follow = turn("200002", "help me with homework")
    lines.append("--- kid guesses, then a normal question ---")
    lines.append(f"guess tools: {tool_names(guess)} refused: {'policy error' in json.dumps(guess)}")
    lines.append(f"guess user text: {guess['messages'][-1]['content']}")
    lines.append(f"follow tools: {tool_names(follow)} refused: {'policy error' in json.dumps(follow)}")
    lines.append(f"follow user text: {follow['messages'][-1]['content']}")

    as_person("200002")
    blocked = ht._pre_tool_call(
        tool_name="read_file",
        args={"path": str(home / "memories" / "MEMORY.md")},
        session_id="demo-kid",
    )
    lines.append("--- kid tools ---")
    lines.append(f"read_file: {blocked['action']} {blocked['message']}")
    lines.append(
        "web_search: "
        + str(ht._pre_tool_call(tool_name="web_search", args={"query": "photosynthesis"}, session_id="demo-kid"))
    )

    as_person("300003")
    user_block = ht._pre_tool_call(
        tool_name="read_file",
        args={"path": str(home / "memories" / "MEMORY.md")},
        session_id="demo-user",
    )
    lines.append("--- user 300003 read of the memory file ---")
    lines.append(f"read_file: {user_block['action']} {user_block['message']}")
    lines.append(
        "read /tmp/notes.txt: "
        + str(ht._pre_tool_call(tool_name="read_file", args={"path": "/tmp/notes.txt"}, session_id="demo-user"))
    )

    ht._now = lambda tz: time(22, 0)
    admitted = ht._post_admission(
        session_key="agent:main:telegram:dm:200002",
        platform="telegram",
        source={"platform": "telegram", "user_id": "200002", "chat_id": "200002", "chat_type": "dm"},
        text="can I stay up?",
    )
    lines.append("--- kid DM at 22:00 UTC ---")
    lines.append(json.dumps(admitted, ensure_ascii=False))

    os.environ.pop("HERMES_SESSION_PLATFORM", None)
    os.environ.pop("HERMES_SESSION_USER_ID", None)
    lines.append("--- /family from the local CLI (owner) ---")
    lines.append(str(ht._family(raw_args="", source=None)).rstrip())
    return "\n".join(lines)


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="household-tiers-demo-"))
    ht = _load_plugin(home)
    transcript = _transcript(ht)
    commit = _hermes_commit()
    py = platform.python_version()
    text = f"""# Demo

Regenerate this file with:

```bash
python bench/demo.py
```

That command reads [`demo-config.json`](demo-config.json), stores one memory entry with Hermes `MemoryStore.add`, builds the system prompt with `agent.system_prompt.build_system_prompt`, and calls `_llm_request`, `_pre_tool_call`, `_post_admission`, and `_family`. No model is called.

This copy was generated against Hermes `{commit}`. `requires_hermes` is `>=0.22.7` because that commit reports base version 0.22.7. The `v0.22.7` tag itself was not re-run. Python was CPython {py}. Install is `git clone` into `~/.hermes/plugins/household-tiers`. `hermes plugins install household-tiers` does not resolve while this repository is private and the catalog draft is unpublished.

The policy is `telegram:100001` owner, `telegram:200002` kid, `telegram:300003` user, timezone UTC, quiet hours `08:00–21:00` for kid and guest.

```text
{transcript}
```

`None` from a hook means the plugin left the call alone. The owner request is unchanged, so the garage code stays in the system prompt. The kid's prompt loses both paragraphs and keeps the date line and the profile line. Guessing `4821` does not refuse the turn, and the next question is not refused either. The kid tier has no `read_file`, so that call is the tier block. The user tier has `read_file` and is blocked only for the household file. At 22:00 the gateway turn is answered by the plugin.

Schema token counts and the coexistence run are in the README. They come from `python bench/compare.py` and `python bench/coexistence.py`, not from this script.
"""
    DEMO.write_text(text, encoding="utf-8")
    sys.stdout.write(f"wrote {DEMO}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
