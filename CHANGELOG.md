# Changelog

## 0.1.1

A tier whose tool list is `["*"]` still has built-in owner memory withheld unless that tier is listed in `memory_tiers`. The `memory` tool is hidden for every other tier. Local-network checks apply to every non-owner tier.

Text from an external provider `system_prompt_block()` that Hermes joins after the built-in memory boxes is removed with those boxes. A provider block with no built-in box in the prompt is still not located. A compaction handoff on a later main request has owner-entry text redacted. The auxiliary summary request is still not seen by this plugin.

`search_files` refuses a root that contains a symlink out of `file_roots` or into a blocked path. The parsed policy and unchanged `MEMORY.md` / `USER.md` are reused. Secret-directory paths are reused until a parent directory of those paths changes.

## 0.1.0

First release of household-tiers.

Catalog review: the active profile home comes from `get_hermes_home()`. The default `user` tier has no file tools. A non-owner tier that adds `read_file` or `search_files` may only use `file_roots` (default none) and cannot read credential directories or browser profiles. Memory entries are split on `ENTRY_DELIMITER` in this plugin. Relative file paths use `ctx.session_cwd()`.

Migration: an earlier build stored its policy and audit log as `guestlist.json` and `guestlist-audit.jsonl` under HERMES_HOME. This plugin does not read those names. Rename them to `household-tiers.json` and `household-tiers-audit.jsonl` if you still have them.
