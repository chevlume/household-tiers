# household-tiers

Per-person tool tiers for one shared [Hermes](https://github.com/NousResearch/hermes-agent) bot. People are `owner`, `user`, or `guest`. The `family` preset adds `kid`: a curfew, a parent alert, owner memory kept out of other prompts, and a fixed homework note.

The gateway enforces this with `post_gateway_admission`, `pre_gateway_dispatch`, `pre_tool_call`, `subagent_start`, `llm_request` middleware, and the `/family` command (`/familia` is the same command registered again; `register_command` has no alias field). A broken policy fails closed.

This release was executed against Hermes commit `ed1afb082eb3e46cf2edae9d8f68fb67b09ee4df`. That commit reports base version **0.22.7** (193 commits after the `v0.22.7` tag). `requires_hermes` is `>=0.22.7` because catalog rule 14 requires a SemVer floor no newer than the current release, and a floor above `0.22.7` would not load on this commit. The `v0.22.7` tag itself was not re-tested.

## Install

Python 3.14 and Hermes at the commit above. Copy this repository into the plugins directory and enable it:

```bash
mkdir -p ~/.hermes/plugins
git clone https://github.com/chevlume/household-tiers.git ~/.hermes/plugins/household-tiers
```

In `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - household-tiers
```

Restart the gateway. The plugin is standard-library Python only. It declares no Python dependencies.

## Quick start

Save this as `~/.hermes/household-tiers.json` (the same file is in [`docs/demo-config.json`](docs/demo-config.json), with fictitious ids):

```json
{"preset": "family",
 "users": {"telegram:100001": "owner", "telegram:200002": "kid"},
 "notify": {"platform": "telegram", "chat_id": "900009"}}
```

Add someone later, from the owner's chat, in one line:

```text
/family tier telegram:200002 kid
```

An id that is not in `users` is `guest`: no tools, no owner memory, and the same curfew as `kid` under the family preset. The change is read from disk on the next call.

## Configuration

| Field | Meaning |
| --- | --- |
| `preset` | `family` adds the `kid` tier, the homework note, and hours `08:00–21:00` Europe/Madrid for `kid` and `guest`. |
| `users` | Map of `platform:id` to a tier name. |
| `tiers` | Tool names, or `["*"]`. Overrides the preset. `todo` and `todo_list` are both listed for `kid` and `user` because the Hermes tool is `todo_list`. |
| `hours` | Per tier: `{"tz", "allow": "HH:MM-HH:MM", "reply"?, "reply_in_groups"?}`. A window that crosses midnight is allowed. `reply` replaces the default quiet-hours text for that tier. |
| `notes` | Text appended to the system prompt for that tier. The same string every turn. |
| `memory_tiers` | Tiers that still receive owner memory. Default `["owner"]`. |
| `shared_threads` | Default `refuse`. Non-owner tiers are refused in a shared group or thread session. |
| `cron_tier` | Tier for cron sessions. Default `owner`. |
| `default` | Tier for an unmapped sender. Default `guest`. |
| `notify` | Where the parent alert goes. See below. |
| `audit` | `enabled` (default `true`) and `retention_days` (default `30`). |
| `messages` | Templates for every user-facing string. Missing keys keep the English defaults. |

`notify` is `{"platform": "<adapter name>", "chat_id": "<chat>"}`. On a curfew block the plugin asks the gateway adapter observed at `pre_gateway_dispatch` to send `messages.curfew_alert` to that chat. The plugin does not open a connection. If no runner was observed, the alert is skipped and the turn is still blocked. In a group the member gets no reply unless that tier sets `reply_in_groups`.

`messages` values are `str.format` templates. Names you can use: `who`, `tier`, `tool`, `window`, `days`, `blocked`, `curfew`, `tiers`, `target`, `count`. A Spanish quiet-hours line is configuration, not a default:

```json
{"messages": {"curfew_reply": "Es hora de descansar. Hablamos mañana."}}
```

### Commands

Owner only. The local CLI counts as the owner.

| Command | Effect |
| --- | --- |
| `/family` | Summary of blocks and curfew hits. `/family 2` is two days. Default 7. |
| `/family tier <platform:id> <tier>` | Change a tier. You cannot change your own. |
| `/family forget <platform:id>` | Delete that person's audit lines (and run retention). |
| `/familia` | Same handler as `/family`. |

Set the platform `allow_admin_from` (and `group_allow_admin_from` in groups) to the owner's id. Hermes then limits slash commands to that id. The core documents that this gate never applies to plain chat: it does not limit tools. This plugin does.

## Measured results

Hermes commit `ed1afb08`, toolset `hermes-telegram`, tokenizer **tiktoken `cl100k_base`** (the estimator Hermes uses for tool schemas). The Hermes-4-70B tokenizer is not in that tree and was not used. No model was called.

| Figure | Measured | Command |
| --- | --- | --- |
| Visible tools, unfiltered vs kid | **20** vs **2** (`web_extract`, `web_search`) | `python bench/compare.py` |
| Tool-schema tokens, unfiltered vs kid | **9,350** vs **476** | `python bench/compare.py` |
| Owner memory + profile markers in a kid prompt | **0 / 2** (2 / 2 before the middleware) | `python bench/compare.py` |
| Recalled `<memory-context>` marker after middleware | **0** | `python bench/compare.py` |
| Owner markers with a broken policy; tools left on the request | **0**; tools removed | `python bench/compare.py` |
| Host isolation (`HERMES_PLUGIN_HOST_PROCESS=1`) | tier `guest`, **0** owner markers | `python bench/compare.py` |
| Prepared `/btw` request (no live model) | **0** owner markers | `python bench/compare.py` |
| Prepared `/bg` child session | **0** owner markers; `terminal` blocked; `memory` blocked | `python bench/compare.py` |
| Background memory review | `memory` blocked for the kid session; live review **not run** | `python bench/compare.py` |
| Shared group, message text copied into the reply | **0**; turn handled | `python bench/compare.py` |
| LAN block for the kid (`allow_private_urls` on) | blocked `192.168.1.1`, `127.0.0.1`, decimal `2130706433`, `192.168.1.1.nip.io`; allowed `93.184.215.14` | `python bench/compare.py` |
| `pre_tool_call` p95, 200 allow-path calls | **2.9e-05 s** (0.029 ms; the test bound is 2 ms) | `python bench/compare.py` and `pytest tests/test_overhead.py` |
| memory-shield alone vs with this plugin | **2 / 2** owner markers vs **0 / 2** | `python bench/coexistence.py` |
| readonly-guard `/readonly writable` then a write | write allowed alone; with this plugin the kid's `terminal` call stays blocked | `python bench/coexistence.py` |

`bench/coexistence.py` is optional. It clones two pins (see `bench/pins.json`) and is not part of CI. `todo_list` is on the kid allow-list but was not in the 20 schemas `hermes-telegram` produced here, so the visible set was two tools.

The earlier draft's 11,579 → 1,771 token pair was not reproduced. This run's schema totals are 9,350 and 476.

Stderr from `bench/compare.py` may say Nous Portal auth is missing. That comes from Hermes resolving auxiliary clients while it builds tool schemas. The JSON on stdout is the measurement. No prompt was sent to a model.

## Disclosure

- The plugin makes no network calls of its own. It does not resolve DNS and it does not fetch URLs.
- It writes two files under `HERMES_HOME`: `household-tiers.json` and `household-tiers-audit.jsonl`.
- The parent alert is the gateway's `adapter.send` to the chat in `notify`. The plugin only asks for that send.
- Each non-owner LLM request is modified: tools outside the tier are removed, owner memory blocks and `<memory-context>` recalls are replaced, and the tier note is appended to the system prompt.
- Tool calls outside the tier are blocked in `pre_tool_call`, including subagents and a background child that inherited the sender.
- Outside the tier's hours the gateway turn is answered by the plugin and is not passed to the model.
- No telemetry, no API keys, no self-update.
- This is **not** a certified parental-control system. It does not replace the platform's own age limits.
- Homework mode is a fixed instruction on the `kid` system prompt. Whether a given model follows it was not measured.

## Privacy

The owner is responsible for telling household members that this plugin is running, what it stores, and how to ask for a deletion.

The audit log stores decision codes, the `platform:id`, the tier, the tool name, a short reason code, the session id, the curfew window, and a count. It does not store message text, tool arguments, or prompt contents. `audit.enabled` defaults to true. `audit.retention_days` defaults to 30. Expired lines are removed when the plugin registers and whenever `/family` runs. `/family forget <platform:id>` deletes lines whose `who` or `target` is that id. The deletion receipt records the owner and the count, not the deleted id.

Older files from the previous filename are read if the new files are missing, and retention and forget apply to them too. The names are in the changelog.

## Known issues

- `plugins.isolation: host` does not forward the per-turn sender. Everyone is treated as `guest`, and the parent alert is skipped because the dispatch hook does not receive a live gateway. Use `in_process` for per-person tiers.
- Shared sessions (a thread with `thread_sessions_per_user: false`, or a group with `group_sessions_per_user: false`) are refused for tiers that do not keep owner memory. Direct messages still work. Turn on per-user session keys for those chats.
- Curfew runs after gateway admission. Senders the core never admits get nothing from this plugin.
- A broken `household-tiers.json` blocks tools, strips memory for every tier including the owner, and answers each gateway turn with an error until the file is fixed.
- HTTP redirects onto the local network are not followed. A public URL that redirects to a LAN address is not blocked here. DNS names are not resolved, so a hostname that points at the LAN is not blocked either, except `localhost`, names ending in `.local` or `.localhost`, IP literals (including decimal, hex, and octal forms), and the embedded-address forms of `nip.io`, `sslip.io`, `xip.io`, and `localtest.me`. A name that is public at one lookup and private at the next is not re-checked. When `allow_private_urls` is off, Hermes core still applies its own checks on its HTTP clients. The extra block in this plugin runs when private URLs are enabled, and only for addresses it can classify without a lookup.
- A WhatsApp session key that ends with an id other than `user_id` or `user_id_alt` does not match. The non-owner tier is refused.
- Homework-mode compliance is not measured.
- `hermes plugins validate` warns on Hermes imports inside `bench/`. Those files are not loaded with the plugin. The plugin module itself is not in that warning. Validation still exits 0. Doctor `--ci` reports no errors.
- No banner or screenshot is shipped.

## Tests

CI installs Hermes at `ed1afb082eb3e46cf2edae9d8f68fb67b09ee4df` and runs:

```bash
pytest -q
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
```

The catalog draft is `catalog/household-tiers.yaml`. Its `sha` field has to be updated to the commit you submit. Image and banner are intentionally absent.
