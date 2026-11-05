# Demo

This is one run of the plugin. No model was called. Hermes was commit `ed1afb082eb3e46cf2edae9d8f68fb67b09ee4df` (193 commits after the `v0.22.7` tag; that tag was not re-run). Python was CPython 3.14.10. Install is still `git clone` into `~/.hermes/plugins/household-tiers`. `hermes plugins install household-tiers` does not resolve while this repository is private and the catalog draft is unpublished.

The policy is [`demo-config.json`](demo-config.json): `telegram:100001` owner, `telegram:200002` kid, `telegram:300003` user, timezone UTC, quiet hours `08:00–21:00` for kid and guest.

The driver wrote that file under a temporary `HERMES_HOME`, stored one memory entry with Hermes `MemoryStore.add` (`The garage code is 4821.` plus a second paragraph), built the system prompt with `agent.system_prompt.build_system_prompt`, and called `_llm_request`, `_pre_tool_call`, `_post_admission`, and `_family`.

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
read_file: block household-tiers: telegram:300003 (user) may not read household files.
read /tmp/notes.txt: None
--- kid DM at 22:00 UTC ---
{"action": "handled", "reply": "It is quiet hours. We can talk again later."}
--- /family from the local CLI (owner) ---
Household summary (7 days):
- telegram:200002: blocked read_file×1; outside hours 1
- telegram:300003: blocked read_file×1; outside hours 0
```

`None` from a hook means the plugin left the call alone. The owner request is unchanged, so the garage code stays in the system prompt. The kid's prompt loses both paragraphs and keeps the date line and the profile line. Guessing `4821` does not refuse the turn, and the next question is not refused either. The kid tier has no `read_file`, so that call is the tier block. The user tier has `read_file` and is blocked only for the household file. At 22:00 the gateway turn is answered by the plugin.

Same checkout, same Hermes commit:

```text
$ python -m pytest --collect-only -q
85 tests collected

$ python bench/compare.py   # two runs
unfiltered_schema_tokens 9301
filtered_schema_tokens 477
unfiltered_tools 20
filtered_tools 2  (web_extract, web_search)
pre_tool_call_p95_seconds 3.3e-05 and 3.4e-05
multiparagraph second_paragraph/later_entry/profile_second false on
  chat_completions, anthropic_messages, codex_responses, bedrock_converse,
  and chat after apply_anthropic_cache_control
refused false

$ python bench/coexistence.py
memory_shield_only_owner_markers 2
memory_shield_plus_household_tiers_owner_markers 0
readonly_guard_only_write_file_blocked false
both_terminal_blocked true
```
