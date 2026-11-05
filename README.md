# household-tiers

Per-person tool tiers for one shared [Hermes](https://github.com/NousResearch/hermes-agent) bot. People are `owner`, `user`, or `guest`. The `family` preset adds `kid`: a curfew, a parent alert, owner memory kept out of other prompts, and a fixed homework note.

The gateway enforces this with `post_gateway_admission`, `pre_gateway_dispatch`, `pre_tool_call`, `subagent_start`, `llm_request` middleware, and the `/family` command (`/familia` is the same command registered again; `register_command` has no alias field). A broken policy fails closed.

This release was executed against Hermes commit `ed1afb082eb3e46cf2edae9d8f68fb67b09ee4df`. That commit reports base version **0.22.7** (193 commits after the `v0.22.7` tag). `requires_hermes` is `>=0.22.7` because catalog rule 14 requires a SemVer floor no newer than the current release, and a floor above `0.22.7` would not load on this commit. The `v0.22.7` tag itself was not re-tested.

Tested Python: CPython **3.14.10** locally, and GitHub Actions `ubuntu-latest` with uv's CPython 3.14. Not tested on 3.11, 3.12, or 3.13.

## Install

`hermes plugins install household-tiers` is not available yet. The catalog entry in `catalog/household-tiers.yaml` is a draft in this private repository and has not been merged into the Hermes catalog, so a catalog name does not resolve.

Until that listing exists, clone the git repository into the plugins directory and enable it:

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

Restart the gateway. The plugin is standard-library Python only. It declares no Python dependencies. `bench/compare.py` is separate and needs `tiktoken` (`pip install tiktoken`). It exits if tiktoken is missing.

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
| `preset` | `family` adds the `kid` tier, the homework note, and hours `08:00–21:00` for `kid` and `guest`. The clock is `timezone`, below. |
| `timezone` | IANA name for tiers whose `hours` entry does not set `tz`. Default `UTC`. Example: `"timezone": "Europe/Madrid"`. |
| `users` | Map of `platform:id` to a tier name. |
| `tiers` | Tool names, or `["*"]`. Overrides the preset. `todo` and `todo_list` are both listed for `kid` and `user` because the Hermes tool is `todo_list`. `user` also has `read_file` and `search_files`, but not for paths under `HERMES_HOME`. |
| `hours` | Per tier: `{"allow": "HH:MM-HH:MM", "tz"?, "reply"?, "reply_in_groups"?}`. A window that crosses midnight is allowed. `tz` overrides `timezone`. `reply` replaces the default quiet-hours text for that tier. |
| `notes` | Text appended to the system prompt for that tier. The same string every turn. |
| `memory_tiers` | List of tier names that still receive owner memory. Default `["owner"]`. A string is a broken policy and fails closed. `user` is not in the default, so that tier does not get owner memory in the prompt and cannot read the household directory. |
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

Hermes commit `ed1afb08`, toolset `hermes-telegram`. Tokenizer: **tiktoken `cl100k_base`**. Each schema is `json.dumps(..., sort_keys=True, ensure_ascii=False)`. The `text_to_speech` default path embeds the process temp directory; that directory is replaced with `/tmp/<process-temp>/` before counting so the total does not move between runs. Two runs of `python bench/compare.py` both printed **9301** unfiltered and **477** filtered. No model was called. `bench/compare.py` exits with status 2 if tiktoken is not installed.

| Figure | Measured | Command |
| --- | --- | --- |
| Visible tools, unfiltered vs kid | **20** vs **2** (`web_extract`, `web_search`) | `python bench/compare.py` |
| Tool-schema tokens, unfiltered vs kid | **9301** vs **477** | `python bench/compare.py` |
| Owner markers, chat completions | **0** (2 before) | `python bench/compare.py` (`shapes.chat_completions`) |
| Owner markers, Anthropic `system` string | **0**; visible tools `web_extract`, `web_search` | `python bench/compare.py` (`shapes.anthropic_system_string`) |
| Owner markers, Anthropic `system` blocks with `cache_control` | **0**; same two tools | `python bench/compare.py` (`shapes.anthropic_system_blocks`) |
| Owner markers, Codex `instructions` + `input` | **0**; same two tools | `python bench/compare.py` (`shapes.codex_responses`) |
| Owner markers with a broken policy | **0**; tools removed | `python bench/compare.py` |
| Host isolation (`HERMES_PLUGIN_HOST_PROCESS=1`) | tier `guest`, **0** owner markers | `python bench/compare.py` |
| Prepared `/btw` request (no live model) | **0** owner markers | `python bench/compare.py` |
| Prepared `/bg` child session | **0** owner markers; `terminal` blocked; `memory` blocked | `python bench/compare.py` |
| Background memory review | `memory` blocked for the kid session; live review **not run** | `python bench/compare.py` |
| Shared group, message text copied into the reply | **0**; turn handled | `python bench/compare.py` |
| LAN block for the kid (`allow_private_urls` on) | blocked `192.168.1.1`, `127.0.0.1`, decimal `2130706433`, `192.168.1.1.nip.io`; allowed `93.184.215.14` | `python bench/compare.py` |
| `pre_tool_call` p95, 200 allow-path calls | **3.3e-05 s** and **3.4e-05 s** on the two runs after the third-audit fix (the test bound is 2 ms) | `python bench/compare.py` and `pytest tests/test_overhead.py` |
| Multi-paragraph memory (`§` entries) on real transports | **0** second-paragraph, later-entry, and profile leaks; tools **web_extract, web_search**; not refused | `python bench/compare.py` (`multiparagraph`, transports `chat_completions`, `anthropic_messages`, `codex_responses`, `bedrock_converse`, and chat after `apply_anthropic_cache_control`) |
| memory-shield alone vs with this plugin | **2 / 2** owner markers vs **0 / 2** | `python bench/coexistence.py` |
| readonly-guard `/readonly writable` then a write | write allowed alone; with this plugin the kid's `terminal` call stays blocked | `python bench/coexistence.py` |

`bench/coexistence.py` is optional and is not part of CI. It clones memory-shield `4deef3e29a906874031ca69620c393a93af7bddc` and readonly-guard `2c2b4d98f1e3c8578bd76b811614c752e2d77d35` (see `bench/pins.json`). `todo_list` is on the kid allow-list but was not in the 20 schemas `hermes-telegram` produced here, so the visible set was two tools.

Stderr from `bench/compare.py` may say Nous Portal auth is missing. That comes from Hermes resolving auxiliary clients while it builds tool schemas. The JSON on stdout is the measurement. No prompt was sent to a model.

## Disclosure

- The plugin does not send HTTP requests, telemetry, or self-updates, and it does not hold API keys. When a non-owner tier calls `web_extract`, `web_crawl`, or `browser_navigate` and private URLs are enabled, it resolves the hostname with the system resolver (`socket.getaddrinfo`) and blocks the call if any address is private, loopback, or link-local. It does not fetch the URL.
- It reads `config.yaml` through Hermes (`hermes_cli.config.read_raw_config`) to see `security.allow_private_urls` and the legacy `browser.allow_private_urls` flag. `HERMES_ALLOW_PRIVATE_URLS` overrides that.
- It writes two files under `HERMES_HOME`: `household-tiers.json` and `household-tiers-audit.jsonl`.
- The parent alert is the gateway's `adapter.send` to the chat in `notify`. The plugin only asks for that send.
- For every non-owner tier it rewrites injected sections of the LLM request, then the tool lists. Injected sections are system and developer messages, Anthropic `system`, Codex `instructions`, and Bedrock `system`. The kid's own messages and tool results are not rewritten, and a turn is not refused because they contain owner-entry text. Verified on payloads from Hermes transports at `ed1afb08`: `chat_completions`, `anthropic_messages` (`system` as a string or as `cache_control` blocks), `codex_responses` (`instructions` and `input`), and `bedrock_converse` (`system` plus `toolConfig.tools[].toolSpec`). The same chat payload after `apply_anthropic_cache_control` (OpenRouter cache markers) was also checked. Gemini was not executed: this middleware runs before Gemini conversion, so that path was not built. A boxed memory block is removed from its header through the on-disk entry text when that text is what the prompt contains; otherwise it stops at the next boxed header or at the next section Hermes places after memory (`<!-- hermes-plugin-sections:start -->`, `Active Hermes profile:`, `Conversation started:`, `Timezone:`, `# Hermes runtime environment`). The date line, profile line, and runtime block stay. Blank lines and `§` entries inside the block are included. An external provider's `system_prompt_block()` is not one of those boxes, so the rest of that block reaches every tier. Entry text from `MEMORY.md` / `USER.md` of 8 or more characters that still appears anywhere in an injected system section, including SOUL, AGENTS.md, or skills text, is redacted there. `<memory-context>` recalls are replaced wherever they appear, including on a user message. The turn is refused only when a boxed memory header is still in an injected section after that strip. Pasting the header, or typing an exact entry, does not refuse the turn and does not change which tools are offered. A tier note is appended. Tools the tier may use are kept, including `web_search`, on the shapes above. Bedrock `cachePoint` markers are not tools and are left in place.
- Tiers without `*` that may call `read_file` or `search_files` cannot use those tools on a path under `HERMES_HOME`, or on a search root that contains `HERMES_HOME` (memory, sessions, the audit log, the policy file). Relative paths use Hermes `_resolve_base_dir` (the session directory, then `TERMINAL_CWD`, then the process cwd). The path is passed through `os.path.realpath` before the comparison, including when the final component does not exist, so a symlink in an existing prefix is followed. Comparison uses `os.path.normcase`. The case-fold check was a monkeypatched `normcase` on Linux, not a Mac.
- Tool calls outside the tier are blocked in `pre_tool_call`, including subagents and a background child that inherited the sender.
- Outside the tier's hours the gateway turn is answered by the plugin and is not passed to the model. The clock is the `timezone` key, default `UTC`, unless that tier's `hours` entry sets `tz`.
- `bench/coexistence.py` clones two third-party git repositories. That script is optional and is not required to run the plugin. It is not in CI.
- No telemetry, no API keys, no self-update.
- This is **not** a certified parental-control system. It does not replace the platform's own age limits.
- Homework mode is a fixed instruction on the `kid` system prompt. Whether a given model follows it was not measured.

## Privacy

The owner is responsible for telling household members that this plugin is running, what it stores, and how to ask for a deletion.

The audit log stores decision codes, the `platform:id`, the tier, a tool name matching `[A-Za-z0-9_.-]{1,64}`, a short reason code, the session id, the curfew window, and a count. It does not store message text, tool arguments, or prompt contents. `audit.enabled` defaults to true. `audit.retention_days` defaults to 30. Expired lines are removed when the plugin registers and whenever `/family` runs. `/family forget <platform:id>` deletes lines whose `who` or `target` is that id. The deletion receipt records the owner and the count, not the deleted id.

Files left by the previous filename are not read. The changelog has those names and how to rename them.

## Known issues

- `plugins.isolation: host` does not forward the per-turn sender. Everyone is treated as `guest`, and the parent alert is skipped because the dispatch hook does not receive a live gateway. Use `in_process` for per-person tiers.
- Shared sessions (a thread with `thread_sessions_per_user: false`, or a group with `group_sessions_per_user: false`) are refused for tiers that do not keep owner memory. Direct messages still work. Turn on per-user session keys for those chats.
- Curfew runs after gateway admission. Senders the core never admits get nothing from this plugin.
- A broken `household-tiers.json` blocks tools, strips memory for every tier including the owner, and answers each gateway turn with an error until the file is fixed.
- HTTP redirects onto the local network are not followed. A public URL that 30x-redirects to a LAN address is not blocked here. Hostnames are resolved once at `pre_tool_call`. A name that is public at that lookup and private when the connection opens is not re-checked. IP literals (including decimal, hex, and octal), `localhost`, names ending in `.local` or `.localhost`, and the embedded-address forms of `nip.io`, `sslip.io`, `xip.io`, and `localtest.me` are classified as well. A failed lookup is treated as local. When `allow_private_urls` is off, Hermes core still applies its own checks and this plugin does not add a second block.
- A WhatsApp session key that ends with an id other than `user_id` or `user_id_alt` does not match. The non-owner tier is refused.
- Gemini conversion was not executed. Docker and SSH file-tool backends were not executed. macOS itself was not used; `os.path.normcase` was monkeypatched in the Linux tests.
- Context compaction does not go through this plugin. `apply_llm_request_middleware` is called from `agent/turn_api_request.py` only (Hermes `ed1afb08`). The auxiliary summary prompt is built in `agent/context_compressor.py`. When a memory provider supplied context, `_memory_provider_section` adds a `MEMORY PROVIDER CONTEXT:` block whose body is JSON inside `<memory-provider-context>`, and that request is sent by the auxiliary client. `post_compaction` is an observer (`hermes_cli/plugins.py`, fired from `agent/compaction_events.py`): the callback receives `session_id`, `old_session_id`, `kind`, `reason`, `before_tokens`, `after_tokens`, and `messages_removed`. It does not receive the summary text, and the return value is ignored. Built-in `MEMORY.md` / `USER.md` on a normal turn does go through `llm_request`. A summary that later sits in the transcript is ordinary message text on the next main request; this plugin does not treat it as a memory block. This compaction path was not executed, because it needs an auxiliary model call.
- A shared external memory provider (byterover, retaindb, or any other `system_prompt_block()`) is not a boxed `MEMORY.md` / `USER.md` section, so its block is not removed. Checked with a simulated provider block on a prompt built by `build_system_prompt`: text that is not also an owner entry stays in the kid's system prompt. Every tier that shares that Hermes home receives the provider block.
- Any piece of 8 or more characters from the owner's `MEMORY.md` or `USER.md` (a whole entry, or a blank-line paragraph inside one) is redacted wherever it appears in an injected system section. That is the whole system prompt string, so the same piece is also removed from SOUL, AGENTS.md, or skills text when those sections contain it. Shorter pieces are left alone. The kid's own messages are not redacted.
- Homework-mode compliance is not measured.
- `hermes plugins validate` warns on private Hermes imports. The plugin calls `tools.file_tools_paths._resolve_base_dir` and reads `MEMORY.md` / `USER.md` through `MemoryStore` so path checks and leftover memory match Hermes. `bench/` imports adapters for measurement. Validation still exits 0. Doctor `--ci` reports no errors.
- No banner or screenshot is shipped.

## Tests

CI installs Hermes at `ed1afb082eb3e46cf2edae9d8f68fb67b09ee4df` and runs:

```bash
pytest -q
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
```

The catalog draft is `catalog/household-tiers.yaml`. Its `sha` field has to be updated to the commit you submit. Image and banner are intentionally absent.

A short walkthrough is in [`docs/demo.md`](docs/demo.md). Regenerate it with `python bench/demo.py`. It reads [`docs/demo-config.json`](docs/demo-config.json).
