$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it from https://astral.sh/uv or with: winget install --id astral-sh.uv -e"
}

uv venv training\.venv --python 3.13
& training\.venv\Scripts\python.exe -m pip install --upgrade pip
uv pip install --python training\.venv\Scripts\python.exe torchao
uv pip install --python training\.venv\Scripts\python.exe "unsloth[windows]" datasets trl bitsandbytes
Write-Host "Installing the CUDA PyTorch build (large one-time download)..."
uv pip install --python training\.venv\Scripts\python.exe --reinstall --index-url https://download.pytorch.org/whl/cu128 torch torchvision
Write-Host "Unsloth environment ready. Activate with: .\training\.venv\Scripts\Activate.ps1"
