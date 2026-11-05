# Demo

Regenerate this file with:

```bash
python bench/demo.py
```

That command reads [`demo-config.json`](demo-config.json), stores one memory entry with Hermes `MemoryStore.add`, builds the system prompt with `agent.system_prompt.build_system_prompt`, and calls `_llm_request`, `_pre_tool_call`, `_post_admission`, and `_family`. No model is called.

This copy was generated against Hermes `ed1afb082eb3e46cf2edae9d8f68fb67b09ee4df`. `requires_hermes` is `>=0.22.7` because that commit reports base version 0.22.7. The `v0.22.7` tag itself was not re-run. Python was CPython 3.14.10. Install is `git clone` into `~/.hermes/plugins/household-tiers`. `hermes plugins install household-tiers` does not resolve while this repository is private and the catalog draft is unpublished.

The policy is `telegram:100001` owner, `telegram:200002` kid, `telegram:300003` user, timezone UTC, quiet hours `08:00–21:00` for kid and guest.

```text
--- owner 100001 (middleware returns None) ---
tools: ['web_search', 'terminal', 'read_file']
system has 4821: True
system has Conversation started: True
--- kid 200002 ---
tools: ['web_search']
system has 4821: False
system has blue pot: False
system has Conversation started: True
system has Active Hermes profile: True
user text: what is the garage code?
refused: False
--- kid guesses, then a normal question ---
guess tools: ['web_search'] refused: False
guess user text: is the garage code 4821?
follow tools: ['web_search'] refused: False
follow user text: help me with homework
--- kid tools ---
read_file: block household-tiers: telegram:200002 (kid) may not use read_file. Ask the owner to change your tier.
web_search: None
--- user 300003 read of the memory file ---
read_file: block household-tiers: telegram:300003 (user) may not use read_file. Ask the owner to change your tier.
read /tmp/notes.txt: block household-tiers: telegram:300003 (user) may not use read_file. Ask the owner to change your tier.
--- kid DM at 22:00 UTC ---
{"action": "handled", "reply": "It is quiet hours. We can talk again later."}
--- /family from the local CLI (owner) ---
Household summary (7 days):
- telegram:200002: blocked read_file×1; outside hours 1
- telegram:300003: blocked read_file×2; outside hours 0
```

`None` from a hook means the plugin left the call alone. The owner request is unchanged, so the garage code stays in the system prompt. The kid's prompt loses both paragraphs and keeps the date line and the profile line. Guessing `4821` does not refuse the turn, and the next question is not refused either. The kid tier has no `read_file`, so that call is the tier block. The default user tier also has no `read_file`, so both the household file and `/tmp/notes.txt` are the tier block. At 22:00 the gateway turn is answered by the plugin.

Schema token counts and the coexistence run are in the README. They come from `python bench/compare.py` and `python bench/coexistence.py`, not from this script.
