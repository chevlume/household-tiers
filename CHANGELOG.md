# Changelog

## 0.1.0

First release of household-tiers.

Catalog review: the active profile home comes from `get_hermes_home()`. The default `user` tier has no file tools. A non-owner tier that adds `read_file` or `search_files` may only use `file_roots` (default none) and cannot read credential directories or browser profiles. Memory entries are split on `ENTRY_DELIMITER` in this plugin. Relative file paths use `ctx.session_cwd()`.

Migration: an earlier build stored its policy and audit log as `guestlist.json` and `guestlist-audit.jsonl` under HERMES_HOME. This plugin does not read those names. Rename them to `household-tiers.json` and `household-tiers-audit.jsonl` if you still have them.
