# Aether

A small Python orchestrator for Ollama. It routes a request through a planner, coder, and reviewer, then asks for approval before writing files.

Approved changes are recorded in `CHANGELOG.md` as persistent Aether patch notes. The changelog is included in future project context so the agents can understand prior updates.

The desktop app checks Ollama at startup and has a **Check Ollama** button that reports whether the configured models are installed.

Before approved files are overwritten, Aether saves existing versions under `.aether\backups\<timestamp>\` for rollback.

Use **Rollback Last Change** in the desktop app to restore the newest backup.

Use **Validate Proposal** to compile and test proposed files in a temporary sandbox. Aether requires this check to pass before enabling approval.

Aether also includes a dedicated testing agent that interprets automated failures and reports focused fixes separately from the code reviewer.

Durable MCP-learned facts are stored in readable `memory.md` and structured local `memory.db`, so future agents can retrieve compact recent memory without loading the entire history.

A loop guard stops repeated identical repair attempts after two repeats.

## Build the Windows app

After installing the normal UI requirements and PyInstaller, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_aether.ps1
```

The executable is created at `dist\Aether\Aether.exe`. Ollama stays installed separately because it owns the local model service and model storage.

## Run

1. Make sure Ollama is running.
2. From this directory, run:

```powershell
python main.py "Build a small command-line todo app"
```

Use `--auto-approve` only when you trust the generated changes:

```powershell
python main.py "Add tests for the todo app" --auto-approve
```

For faster iteration, use the smaller 4B model for all roles:

```powershell
python main.py "Make the todo list show completed items" --fast
```

Default mode uses the 7B coder for stronger implementation quality while keeping planning and review fast.

For an extra self-correction pass when quality matters:

```powershell
python main.py "Build a small calculator app" --quality-rounds 1
```

Enable optional web research when the request needs current information:

```powershell
python main.py "Build a client using the current OpenAI API" --web
```

The desktop app has a **Web research** checkbox. It uses a no-key public search endpoint, adds the results to planning, and keeps web access disabled unless selected.

The desktop app also enables **Auto web when needed** by default. It uses a conservative keyword check for time-sensitive or source-dependent requests such as “latest,” “current,” “official docs,” API questions, news, and URLs.

When the selected project becomes too large for the working context, the builder automatically summarizes older chunks with the local planner model and keeps the newest project context in full.

## Persistent memory

Edit `memory.md` to store stable preferences and project rules. The desktop app also has an **Edit Memory** button. The memory is included in every local planning and coding request.

Approved runs from the desktop app are saved under `runs/` and can be converted into training examples with `python training\prepare_dataset.py`.

## MCP tools

Install the optional MCP client:

```powershell
pip install -r requirements-mcp.txt
```

The builder includes a safe starter stack in `mcp_servers.json`:

- `filesystem_project`: files inside the generated `workspace/` only
- `git_project`: Git history and repository operations for this builder (disabled until this folder is a Git repository)
- `fetch_web`: web-page retrieval for research
- `memory_graph`: durable facts and relationships across sessions

List configured servers:

```powershell
python mcp_cli.py
```

Inspect one:

```powershell
python mcp_cli.py server-name
```

Review and save useful facts from an MCP result:

```powershell
python mcp_learn.py fetch_web fetch --arguments "{\"url\":\"https://example.com\"}"
```

The local reviewer filters secrets, ignores instructions embedded in external content, removes duplicates, asks for approval, and saves approved facts to `memory.md` plus the memory graph.

Servers are launched only when you inspect or call them. On Windows, the filesystem and memory entries use `cmd /c npx`; Git and Fetch use `uvx`. Install Node.js/npm and `uv` if those launchers are not already available. Write-capable tools require `--approve` after you review the tool schema.

## Image and video analysis

The desktop app can select images or videos and analyze them with the local `gemma4:e4b` vision model. Videos are sampled into representative frames with FFmpeg before analysis.

## Links and browser

Enter a URL in the desktop app to open it or analyze its text locally. For an in-app browser window, optionally install:

```powershell
pip install -r requirements-ui.txt
```

To work on an existing project from the terminal:

```powershell
python main.py "Add tests and fix the failing bug" --project "C:\path\to\your\project"
```

The desktop app has a **Choose Project Folder** button. Agents can read that folder, but proposed writes still require your approval and are constrained to that folder.

## Guarded self-improvement

To have the team inspect and improve this builder itself:

```powershell
python self_improve.py --rounds 1
```

It only proposes Python, Markdown, and Modelfile changes, preserves the safety gates, asks for approval, and creates a ZIP backup before applying anything.

The desktop app also includes **Improve This Builder**. After you approve a self-improvement proposal, it backs up the project and restarts the UI with the updated code.

Use `--quality-rounds 2` for up to two review-and-repair cycles. This improves reliability but takes longer.

The generated project files are placed in `workspace/`. Agent transcripts are saved in `runs/`.

## Browser UI

Run:

```powershell
python web_ui.py
```

Then open http://127.0.0.1:8765. The UI keeps the same review and approval gate.

For the modern chat-style desktop window:

```powershell
python modern_ui.py
```

Install `pywebview` from `requirements-ui.txt` for a native window; otherwise it opens the same interface in a browser.

## Windows desktop app

No browser is required. Run:

```powershell
python desktop_ui.py
```

This opens a native Windows window using Tkinter, which is included with the standard Python installer.

## Custom Ollama models

Build role-specific models from the included Modelfiles:

```powershell
ollama create local-planner -f modelfiles/Planner.Modelfile
ollama create local-coder -f modelfiles/Coder.Modelfile
ollama create local-reviewer -f modelfiles/Reviewer.Modelfile
```

The current config uses your existing model names, so creating these is optional until we switch the routing to them.

## Model roles

Every seat runs on a free hosted tier by default — no model download, and the
reviewer sits on a different provider from the rest so one free tier's rate
limit cannot stall a build.

- Planning and coding: `z-ai/glm-5.3` (NVIDIA NIM)
- Review: `nvidia/nemotron-3-ultra-550b-a55b:free` (OpenRouter)
- Security: `nvidia/nemotron-3-super-120b-a12b` (NVIDIA NIM — the 550b `ultra`
  timed out often enough to be unreliable here)
- Testing and release: `openai/gpt-oss-20b` (NVIDIA NIM)
- Vision: `nvidia/nemotron-3.5-super-vl-120b-a12b` (NVIDIA NIM)
- Intent classification / small helpers: `nvidia/nemotron-3.5-lightning-30b-a3b`, `liquid/lfm-2.5-2.6b:free`

Set `NVIDIA_API_KEY` and `OPENROUTER_API_KEY` in `.env`, then run
`python main.py --preflight` to see what resolves where. Any role can be
pointed at a local Ollama model instead by editing `config.MODELS` — or all of
them at once with `--fast` / `--auto-models`.
