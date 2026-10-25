$ErrorActionPreference = "Stop"

$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    throw "PyInstaller is not installed. Run: python -m pip install pyinstaller"
}

# Compile all source files to catch syntax errors before building
python -m py_compile desktop_ui.py agents.py config.py ollama_client.py orchestrator.py system.py vault.py self_improve.py update.py voice.py web_research.py vision.py link_tools.py patch_notes.py memory_store.py proposal_validation.py loop_guard.py decision_client.py mcp_client.py desk_backtest.py desk_brain.py desk_broker.py desk_data.py desk_ledger.py desk_runner.py desk_seats.py desk_solana.py desk_view.py preflight.py desk_alerts.py desk_forward.py desk_replay.py desk_rotation.py

# Build the executable with all necessary hidden imports and data files
pyinstaller --noconfirm --clean --windowed --name Aether --paths $project `
    --hidden-import=agents `
    --hidden-import=config `
    --hidden-import=ollama_client `
    --hidden-import=orchestrator `
    --hidden-import=system `
    --hidden-import=vault `
    --hidden-import=self_improve `
    --hidden-import=update `
    --hidden-import=voice `
    --hidden-import=web_research `
    --hidden-import=vision `
    --hidden-import=link_tools `
    --hidden-import=patch_notes `
    --hidden-import=memory_store `
    --hidden-import=proposal_validation `
    --hidden-import=loop_guard `
    --hidden-import=decision_client `
    --hidden-import=mcp_client `
    --hidden-import=desk_backtest `
    --hidden-import=desk_brain `
    --hidden-import=desk_broker `
    --hidden-import=desk_data `
    --hidden-import=desk_ledger `
    --hidden-import=desk_runner `
    --hidden-import=desk_seats `
    --hidden-import=desk_solana `
    --hidden-import=desk_view `
    --hidden-import=preflight `
    --hidden-import=desk_alerts `
    --hidden-import=desk_forward `
    --hidden-import=desk_replay `
    --hidden-import=desk_rotation `
    --add-data="vault;vault" `
    --add-data="memory.md;." `
    --add-data="memory.db;." `
    --add-data="mcp_servers.json;." `
    --collect-data customtkinter `
    desktop_ui.py

Write-Host "Aether executable created in $project\dist\Aether\Aether.exe"
Write-Host "Models are hosted (OpenRouter / NIM); Ollama is optional, for offline use only."
Write-Host "Data files (vault/, memory.md, memory.db, mcp_servers.json) are bundled."