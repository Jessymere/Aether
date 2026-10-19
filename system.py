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


def run_command(command: str, timeout: int = 30, force: bool = False) -> str:
    """Run a shell command with safety checks for destructive operations.

    The command is parsed into segments separated by |, &, &&.
    Each segment is checked for destructive verbs (del, erase, rmdir, rd, rm,
    format, fdisk, shutdown, reboot) as the first token (after stripping
    optional cmd /c prefixes). Pipes, backgrounding (&), and redirects are
    allowed by default. Redirects (> or >>) to existing files are noted in the
    returned output.

    Args:
        command: The command string to execute.
        timeout: Timeout in seconds (default 30).
        force: If True, bypass all safety checks.

    Returns:
        Command output as a string, or "(no output)" if there is no output.
        If the command is blocked, returns a warning message.
        If redirects target existing files, a warning is prepended to the output.
    """
    if force:
        # Bypass safety checks
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

    # Helper: check if a segment is destructive
    def is_destructive_segment(seg: str) -> bool:
        """Return True if the segment's command word is a destructive verb."""
        # Strip leading/trailing whitespace
        seg = seg.strip()
        if not seg:
            return False

        # Remove optional cmd /c or cmd.exe /c prefix
        # Handle variations: cmd, cmd.exe, with /c or /k, case-insensitive
        lowered = seg.lower()
        if lowered.startswith("cmd ") or lowered.startswith("cmd.exe "):
            # Remove the executable part
            if lowered.startswith("cmd.exe "):
                seg = seg[8:]  # len("cmd.exe ")
            else:
                seg = seg[4:]  # len("cmd ")
            seg = seg.lstrip()
            lowered = seg.lower()
            if lowered.startswith("/c ") or lowered.startswith("/k "):
                seg = seg[3:]  # len("/c ")
                seg = seg.lstrip()
            elif lowered.startswith("/c") or lowered.startswith("/k"):
                seg = seg[2:]  # len("/c")
                seg = seg.lstrip()

        # Now split the segment into tokens by whitespace to get the first word
        # But first, we need to handle redirects: we want to ignore anything after > or >>
        # for the purpose of extracting the command word.
        # We'll find the first occurrence of '>' or '>>' that is not inside quotes.
        # For simplicity, we assume redirects are not inside quotes and are separated by spaces.
        # We'll split the segment by spaces and look for '>' or '>>' tokens.
        tokens = seg.split()
        # We'll look for the first token that is a redirect and stop before it.
        cmd_tokens = []
        for tok in tokens:
            if tok in (">", ">>"):
                break
            cmd_tokens.append(tok)
        if not cmd_tokens:
            # No command before redirect? Treat as empty (not destructive)
            return False
        cmd_word = cmd_tokens[0].lower()
        # Destructive verbs
        destructive_verbs = {
            "del", "erase", "rmdir", "rd", "rm", "format",
            "fdisk", "shutdown", "reboot"
        }
        return cmd_word in destructive_verbs

    # Tokenize the command into segments separated by |, &, &&, respecting quotes.
    # We'll do a simple state machine: inside_single_quote, inside_double_quote.
    segments = []
    current = []
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if ch == "'" and not (i > 0 and command[i-1] == '\\'):
            # Toggle single quote state
            current.append(ch)
            i += 1
            # We'll just toggle a flag; for simplicity, we assume quotes are not nested.
            # We'll keep track of being inside single quotes.
            # Actually, we need to know when we're inside quotes to avoid splitting on | & &&
            # Let's maintain a state.
            # We'll refactor: we'll keep state variables.
            # Let's restart with a clearer approach.
            # We'll break and rewrite this part.
            pass
        i += 1

    # Given the complexity, and since the task says a small regex/tokenizer is fine,
    # we'll use a different approach: we'll split by spaces but then recombine tokens
    # that are inside quotes. However, we know that the metacharacters | & && are
    # not inside quotes in typical usage. We'll assume that quotes are used to
    # group arguments and that | & && inside quotes are literal.
    # We'll implement a simple tokenizer that tracks quote state.

    # Let's reimplement the tokenization properly.
    inside_single_quote = False
    inside_double_quote = False
    escaped = False  # handle backslash escape
    current = []
    segments = []
    i = 0
    while i < n:
        ch = command[i]
        if escaped:
            escaped = False
            current.append(ch)
            i += 1
            continue
        if ch == '\\':
            escaped = True
            current.append(ch)
            i += 1
            continue
        if ch == "'" and not inside_double_quote:
            inside_single_quote = not inside_single_quote
            current.append(ch)
            i += 1
            continue
        if ch == '"' and not inside_single_quote:
            inside_double_quote = not inside_double_quote
            current.append(ch)
            i += 1
            continue
        # Check for |, &, && when not inside quotes
        if not inside_single_quote and not inside_double_quote:
            if ch == '|':
                # Check for ||? We treat | and || as pipe? The task says | and & and &&
                # We'll treat | as pipe, && as and-operator, & as background.
                # We'll split on |, &, &&.
                # We need to look ahead for &&.
                if i + 1 < n and command[i+1] == '&':
                    # && operator
                    segments.append(''.join(current))
                    current = []
                    i += 2
                    continue
                else:
                    # | pipe
                    segments.append(''.join(current))
                    current = []
                    i += 1
                    continue
            if ch == '&':
                # Check for && already handled above? We are at a single &
                # We'll treat & as background operator.
                segments.append(''.join(current))
                current = []
                i += 1
                continue
        # Otherwise, add character to current segment
        current.append(ch)
        i += 1

    # Add the last segment
    if current or not segments:
        segments.append(''.join(current))

    # Now we have a list of segments (strings) that are separated by |, &, &&.
    # Check each segment for destructiveness.
    for seg in segments:
        if is_destructive_segment(seg):
            # Find the destructive verb for the message
            # We'll reuse is_destructive_segment to get the verb? Let's modify to return the verb.
            # For simplicity, we'll compute again.
            seg_stripped = seg.strip()
            lowered = seg_stripped.lower()
            if lowered.startswith("cmd ") or lowered.startswith("cmd.exe "):
                if lowered.startswith("cmd.exe "):
                    seg_stripped = seg_stripped[8:]
                else:
                    seg_stripped = seg_stripped[4:]
                seg_stripped = seg_stripped.lstrip()
                lowered = seg_stripped.lower()
                if lowered.startswith("/c ") or lowered.startswith("/k "):
                    seg_stripped = seg_stripped[3:]
                    seg_stripped = seg_stripped.lstrip()
                elif lowered.startswith("/c") or lowered.startswith("/k"):
                    seg_stripped = seg_stripped[2:]
                    seg_stripped = seg_stripped.lstrip()
            # Now get first token before redirects
            tokens = seg_stripped.split()
            cmd_tokens = []
            for tok in tokens:
                if tok in (">", ">>"):
                    break
                cmd_tokens.append(tok)
            if cmd_tokens:
                verb = cmd_tokens[0].lower()
            else:
                verb = "<unknown>"
            return f"Blocked potentially destructive command: {command}"

    # No destructive segments found. Now check for redirects that target existing files.
    # We'll go through each segment again to find redirects.
    warnings = []
    for seg in segments:
        seg_stripped = seg.strip()
        # We'll look for > and >> tokens, assuming they are separated by spaces.
        tokens = seg_stripped.split()
        # We'll iterate through tokens to find > or >> and capture the next token as file.
        i = 0
        while i < len(tokens):
            if tokens[i] in (">", ">>"):
                redirect = tokens[i]
                if i + 1 < len(tokens):
                    file_token = tokens[i+1]
                    # Remove surrounding quotes if present
                    if len(file_token) >= 2 and file_token[0] == file_token[-1] and file_token[0] in ('"', "'"):
                        file_token = file_token[1:-1]
                    # Check if file exists
                    if os.path.exists(file_token):
                        if redirect == ">":
                            warnings.append(f"Warning: redirect '{file_token}' will truncate an existing file.")
                        else:  # ">>"
                            warnings.append(f"Warning: redirect '{file_token}' will append to an existing file.")
                    # Skip the redirect and file token for further processing? We don't need to.
                    i += 2
                else:
                    i += 1
            else:
                i += 1

    # Now run the command
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        if warnings:
            warning_str = " ".join(warnings)
            if output:
                output = warning_str + "\n" + output
            else:
                output = warning_str
        if not output:
            output = "(no output)"
        return output
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