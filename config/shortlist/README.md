# Shortlist project docs

Drop one JSON file per shortlisted project here. On startup the bot loads every `*.json` that does **not** start with `_`, upserts them as APPROVED monitoring targets, and registers official X / Telegram sources.

Global sniper defaults (network 99901, 1 HYPE buy, 2% tax, manual sell only) are defined in:

- `config/ELYSIUM_ULTRA_FAST_SNIPER.md`
- `config/elysium_sniper.py`

Use `_template.json` as the per-project schema reference. Supported `launchpad` values:

- `boxa`, `kleos`, `eleos`, `elyspump`, `ascend`
- `uniswap_v4`
- `generic_evm`
