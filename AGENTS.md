# Aether — J.A.R.V.I.S.-style local AI assistant

Conversational AI assistant that chats, builds software, controls your computer, and remembers past conversations. Runs locally on Ollama. Shared core in `orchestrator.py` — both `main.py` (CLI) and `desktop_ui.py` (Tkinter → `Aether.exe`) use the same pipeline.

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

**System control (J.A.R.V.I.S. computer control)**
- `open notepad` / `open chrome` — launch applications
- `open file C:\path\to\file` / `open folder C:\path` — open files/folders
- `system info` / `pc specs` — OS, CPU, memory, disk
- `list C:\path` — list folder contents
- `run dir` / `cmd ipconfig` — run shell commands (destructive commands blocked)
- All system commands run locally, 0 Ollama calls

## Requirements

- Ollama running at `http://127.0.0.1:11434` with models: `qwen3.5:4b` (planner/tester/security/release), `qwen2.5-coder:7b` (coder + reviewer), `gemma4:e4b` (vision)
- Python on PATH. No venv/pyproject/requirements for the builder (stdlib + Ollama HTTP only).
- `mcp[cli]`, `npx`, and `uvx` for MCP tools. `pyinstaller` for `build_aether.ps1`.
- Windows SAPI (built-in) for voice output — no extra install.

## Entry points

- `python main.py "<request>"` — CLI. Supports chat, system commands, Q&A, and build requests.
  - `--auto-approve` — apply without prompting (auto mode).
  - `--fast` — planner/tester/security use 4B; coder/reviewer stay on 7B.
  - `--quality-rounds 0..2` — extra review/repair cycles (default 1).
  - `--project <path>` — target folder (default `workspace/`).
  - `--web` / `--auto-web` — optional web research brief.
- `python desktop_ui.py` — Tkinter Windows UI (becomes `Aether.exe`). Chat input at bottom, message bubbles, voice toggle, three modes (Chat/Build/Q&A), Auto toggle, Open Vault, Save to Vault buttons.
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
