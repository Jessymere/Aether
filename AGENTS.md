# Aether — J.A.R.V.I.S.-style local AI assistant

Conversational AI assistant that chats, builds software, controls your computer, and remembers past conversations. Runs locally on Ollama. Shared core in `orchestrator.py` — `main.py` (CLI), `desktop_ui.py` (Tkinter → `Aether.exe`), and `web_ui.py` (browser) all drive the same `Orchestrator.run()` / `apply_proposal()` path, so approval always takes the same rollback backup, containment check, and changelog note.

## What Aether can do

**Chat / conversation**
- Natural language chat (no "build" framing required)
- Fast path: greetings ("hi", "hey", "how's your day", "thanks") → instant reply, 0 Ollama calls
- Q&A mode: questions ("can you go online", "what can you do", "how do I…") → direct answers, no pipeline
- Auto mode (Claude-style): toggle "Auto" button — files are written automatically without manual approval
- Conversation memory: persists to `runs/conversation.json`, replays on startup
- Voice output (TTS): Windows SAPI, toggle with "Voice: On/Off" button

**Build software**
- Full pipeline: planner → coder → sandbox validate → tester → security → reviewer → revise → approve → write
- Clarification loop: planner asks questions if request is ambiguous
- Cancel between agent steps; no files written on cancel
- Streaming output: planner/reviewer/tester tokens appear incrementally
- Approval gate: validate in sandbox before writing files (skipped in exe mode — no Python interpreter bundled)

**Trading desk (`desk` intent)**
- "run the desk", "scan the market", "backtest SPY", "panic" — routed by `classify_intent` to `Orchestrator.run_desk()`, 0 Ollama calls for the routing itself.
- Two venues, one pipeline: Solana memecoins (DexScreener) and US stocks/ETFs (Yahoo). Every venue declares `paper` or `live` and auto-degrades to paper without credentials.
- Candidates must clear a **three-verdicts walk-forward backtest** (`desk_backtest.evaluate`) over years the rule never saw — train, folds, holdout, plus a train-vs-holdout degradation check. A failed verdict benches the rule and writes the reason.
- Only the JUDGE has an opinion; it answers one narrow `buy`/`hold`/`skip` plus a confidence. Every floor, ceiling and size is enforced by code around it. Skips are ledger rows with `vetoed_by`, so the log can be graded on what it declined.
- It reads its own record back. `closed_trades()` rebuilds finished round trips from the fills (FIFO cost basis, both legs' fees), `record_summary()` reduces them to win rate and expectancy, and those go into the judge's facts *and* into `adapt_dial()`, which shrinks size after a cold patch. No retraining — the ledger is the feedback loop, paper and live kept apart.
- The desk also learns per rule: `closed_trades()` now carries the `rule_id` that opened each trade, and `rule_records()` groups them by rule. A rule whose own closed trades fall below `DESK_RECORD_WIN_FLOOR` with at least `DESK_RECORD_MIN_TRADES` is stood down by name, so the historical gate never even sees it.
- Size follows volatility, not one number for every ticker. `atr_pct()` measures the name's own daily range; the stop is 2 ranges away and the target 3, and a noisier name is sized back so a stop-out costs the same dollars as on a quiet one. A stop that suits SPY stops a meme coin out on an ordinary afternoon, so the distance fits the instrument. `DESK_REFERENCE_ATR_PCT` (default 4%) is the reference range; the saved reading is re-used on restart.
- Kill switch: `DeskRunner.panic()` halts, writes `desk/KILL` and flattens; the daily-loss, drawdown and losing-day ceilings trip it automatically.
- Desktop: a **Desk** tab with Start / Stop / **PANIC**, a live position table and the cycle log. Live mode is red-badged.
- Live Solana signs locally in `desk_solana.py` (Ed25519 via `pynacl`, Jupiter for the route) and needs `SOLANA_PRIVATE_KEY`; without a key the venue runs paper. It holds back `SOLANA_MIN_SOL` for fees, caps by balance and by price impact, and falls back to `None` (no trade) on any swap error.
- The crypto venue runs on Robinhood (`DESK_CRYPTO_BROKER=robinhood`, the default here), a custodial account; `DESK_CRYPTO_BROKER=jupiter` switches back to the Solana wallet. Every Robinhood request needs an Ed25519 signature, so `ROBINHOOD_API_KEY` alone is not enough: run `python -m desk_keys`, upload the printed public half in the app, and put the private half in `.env`. Until both halves parse the venue stays on paper. Signing is verified live — but keys are permission, not balance: an unfunded account reads `buying_power: 0` and refuses every buy, so fund it before a live run.

**System control (J.A.R.V.I.S. computer control)**
- `open notepad` / `open chrome` — launch applications
- `open file C:\path\to\file` / `open folder C:\path` — open files/folders
- `system info` / `pc specs` — OS, CPU, memory, disk
- `list C:\path` — list folder contents
- `run dir` / `cmd ipconfig` — run shell commands (destructive commands blocked)
- All system commands run locally, 0 Ollama calls

## Requirements

- Ollama at `http://127.0.0.1:11434` for any local role. The default `config.MODELS` runs every seat on a **free hosted tier** instead — NVIDIA NIM for the heavy seats, OpenRouter for the reviewer and the small helper — so no model download is needed. Set `NVIDIA_API_KEY` / `OPENROUTER_API_KEY` in `.env`; `python main.py --preflight` reports which slug resolves to which surface, and `--auto-models` falls back to an installed local model when a key is missing.
- Python on PATH. Builder deps are stdlib + `urllib` + `psutil` (`pip install -r requirements.txt`); optional extras live in `requirements-mcp.txt` / `requirements-ui.txt`.
- `mcp[cli]`, `npx`, and `uvx` for MCP tools. `pyinstaller` for `build_aether.ps1`.
- Windows SAPI (built-in) for voice output — no extra install.

## Entry points

- `python main.py "<request>"` — CLI. Supports chat, system commands, Q&A, and build requests.
  - `--auto-approve` — apply without prompting (auto mode).
  - `--fast` — planner/tester/security use 4B; coder/reviewer stay on 7B.
  - `--quality-rounds 0..2` — extra review/repair cycles (default 1).
  - `--project <path>` — target folder (default `workspace/`).
  - `--web` / `--auto-web` — optional web research brief.
- `python desktop_ui.py` — Tkinter Windows UI (becomes `Aether.exe`). **Chat** and **Desk** tabs, message bubbles, voice toggle, Auto toggle, Open Vault, Save to Vault buttons. The Desk tab drives the same `DeskRunner` the CLI does.
- `python web_ui.py` — browser UI on `http://127.0.0.1:8765`, same pipeline, with an Approve button that delegates to `apply_proposal()`.
- `python self_improve.py --rounds 1` — guarded self-improvement (`.py/.md/.Modelfile` only, ZIP backup, sandbox validation, diff preview, approval required).
- `python update.py` — check for updates.

## Verification

- `proposal_validation.py` runs `python -m compileall` and `python -m unittest discover` in a temp sandbox before approval.
- Self-improvement runs sandbox validation on proposed builder changes before approval.
- Loop guard stops repeated identical repair attempts after 2 repeats.
- Approved writes backed up to `.aether\backups\<timestamp>\` first; rollback via desktop app.
- Approved changes append to `CHANGELOG.md`. Generated files go to `workspace/`; transcripts to `runs/`.
- Destructive shell commands (`del`, `rm`, `format`, `shutdown`, `>`, `|`, `&`) are blocked.

## Memory

- `vault/` folder — plain-text `.md` files Aether reads at the start of every conversation and writes to as it works. Open in Obsidian, Notepad, or any text editor. Files include `user-preferences.md`, `project-context.md`, `conversation-summary.md`, `learnings.md`, `todo.md`, and `README.md`.
- `memory.md` — durable user preferences/project rules, included in every planning request.
- `memory.db` — SQLite facts; query with `memory_store.recent_context(MEMORY_DB)`.
- `runs/conversation.json` — persistent chat history, replays on UI startup.
- MCP `memory_graph` server stores durable facts/relationships.

## Training (isolated — never in the exe)

- `training\setup_windows.ps1` → `prepare_dataset.py` → `train.py` → `ollama create`
- QLoRA fine-tune + GGUF export. Don't install unsloth/torch into builder runtime.

## Updates

- `VERSION` in `config.py`; manifest hosted at `UPDATE_MANIFEST_URL`.
- "Check for Updates" button fetches manifest. Manual replace (Windows locks running exes). Never reboot.
- Code-sign with self-signed/internal cert before publishing.

## Safety

- Never propose absolute paths or `..` traversal; writes confined to selected project folder.
- Write-capable MCP tools require `--approve` after schema review.
- Web access off by default; web content is untrusted reference material.
- Self-improvement only touches `.py/.md/.Modelfile`, ZIP backup first, approval required.
- No git repository yet (`git_project` MCP server disabled until this folder is a Git repo).

## Key files

- `config.py` — `OLLAMA_URL`, `MODELS`, `FAST_MODELS`, `WORKSPACE`, `RUNS`, `VERSION`, `UPDATE_MANIFEST_URL`
- `orchestrator.py` — shared pipeline: `run()`, `run_qa()`, `apply_proposal()`, `run_self_improvement()`, `diff_preview()`
- `system.py` — system control: `open_path()`, `open_app()`, `system_info()`, `list_files()`, `run_command()`, `handle_system_request()`
- `voice.py` — Windows SAPI TTS: `Voice.speak()`, `Voice.toggle()`
- `vault.py` — memory vault: `read_vault()`, `write_file()`, `append_file()`, `list_files()`
- `update.py` — `check_for_updates()`
- `desktop_ui.py` — chat UI with voice, conversation memory, system integration
- `mcp_servers.json` — `filesystem_project`, `git_project` (disabled), `fetch_web`, `memory_graph`
- `modelfiles/` — `Planner.Modelfile`, `Coder.Modelfile`, `Reviewer.Modelfile`
