"""
System integration for Aether — J.A.R.V.I.S.-style computer control.

Provides safe, auditable system operations: open apps, files, folders,
run shell commands (read-only by default), get system info.
"""

from __future__ import annotations

import os
import subprocess
import platform
import psutil
from pathlib import Path
from typing import Optional


def open_path(path: str) -> str:
    """Open a file, folder, or URL with the default Windows handler."""
    p = Path(path)
    if p.exists():
        os.startfile(str(p.resolve()))
        return f"Opened: {path}"
    if path.startswith(("http://", "https://")):
        import webbrowser
        webbrowser.open(path)
        return f"Opened URL: {path}"
    return f"Path not found: {path}"


def open_app(app_name: str) -> str:
    """Launch an application by name (searches common locations)."""
    # Try direct command first
    try:
        subprocess.Popen(app_name, shell=True)
        return f"Launched: {app_name}"
    except Exception:
        pass

    # Search common Windows install locations
    search_dirs = [
        Path(os.environ.get("PROGRAMFILES", "")),
        Path(os.environ.get("PROGRAMFILES(X86)", "")),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]
    for base in search_dirs:
        if not base.exists():
            continue
        for exe in base.rglob("*.exe"):
            if app_name.lower() in exe.name.lower():
                try:
                    subprocess.Popen(str(exe))
                    return f"Launched: {exe.name} ({exe.parent})"
                except Exception:
                    continue
    return f"Application not found: {app_name}"


def system_info() -> str:
    """Return concise system information."""
    uname = platform.uname()
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.5)
    disk = psutil.disk_usage("C:\\")
    lines = [
        f"OS: {uname.system} {uname.release} ({uname.version})",
        f"Machine: {uname.machine}, Processor: {uname.processor}",
        f"CPU usage: {cpu}%",
        f"Memory: {mem.used // (1024**2)} MB / {mem.total // (1024**2)} MB ({mem.percent}%)",
        f"Disk C: {disk.used // (1024**3)} GB / {disk.total // (1024**3)} GB ({disk.percent}%)",
    ]
    return "\n".join(lines)


def list_files(folder: str, limit: int = 30) -> str:
    """List files in a folder (safe, read-only)."""
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        return f"Folder not found: {folder}"
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:limit]
    lines = []
    for item in items:
        if item.is_dir():
            lines.append(f"[DIR]  {item.name}/")
        else:
            size = item.stat().st_size
            lines.append(f"[FILE] {item.name} ({size:,} bytes)")
    if len(items) == limit:
        lines.append(f"... (showing first {limit} items)")
    return "\n".join(lines) if lines else "(empty folder)"


def run_command(command: str, timeout: int = 30) -> str:
    """Run a shell command (read-only, no destructive operations)."""
    # Block dangerous commands
    dangerous = ["del ", "rm ", "format", "fdisk", "shutdown", "reboot", "> ", ">> ", "|", "&"]
    cmd_lower = command.lower()
    for d in dangerous:
        if d in cmd_lower:
            return f"Blocked potentially destructive command: {command}"
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return output if output else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s: {command}"
    except Exception as exc:  # noqa: BLE001
        return f"Command failed: {exc}"


def handle_system_request(request: str) -> Optional[str]:
    """Parse a natural language system request and dispatch to the right handler.
    Returns a response string, or None if not a system request."""
    lowered = request.lower().strip()

    # Open app
    if lowered.startswith(("open ", "launch ", "start ")):
        target = lowered.split(" ", 1)[1].strip()
        if target.endswith((".exe", ".app")) or " " not in target:
            return open_app(target)
        return open_path(target)

    # Open file/folder
    if lowered.startswith(("open file ", "open folder ")):
        target = lowered.split(" ", 2)[2].strip().strip('"')
        return open_path(target)

    # System info
    if any(k in lowered for k in ("system info", "computer info", "specs", "how's my pc", "pc status")):
        return system_info()

    # List files
    if lowered.startswith(("list ", "ls ", "files in ")):
        target = lowered.split(" ", 1)[1].strip().strip('"')
        # Remove "in " prefix if present
        if target.startswith("in "):
            target = target[3:].strip().strip('"')
        return list_files(target)

    # Run command
    if lowered.startswith(("run ", "cmd ", "exec ")):
        command = lowered.split(" ", 1)[1].strip()
        return run_command(command)

    return None
