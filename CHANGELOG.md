# Changelog

## 0.1.0

First release of household-tiers.

Migration: an earlier build stored its policy and audit log as `guestlist.json` and `guestlist-audit.jsonl` under HERMES_HOME. Those names are still read when `household-tiers.json` or `household-tiers-audit.jsonl` is absent. New writes go to the household-tiers files. `/family forget` and audit retention also purge the legacy audit file.
