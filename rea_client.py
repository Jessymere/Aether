"""
REA (Reverse Engineer Anything) client for Aether.

Provides access to the REA MCP server for reverse engineering capabilities:
- Native binary analysis (Hopper, Ghidra, IDA)
- JavaScript/Electron application analysis
- .NET assembly analysis
- Python application analysis
- Android APK analysis
- Web page analysis
- And more

REA MCP server: https://github.com/morluto/rea
Install: npm install -g rea-agents
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def check_rea_available() -> bool:
    """Check if REA is available via npx."""
    try:
        result = subprocess.run(
            ["npx", "-y", "rea-agents@latest", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def rea_analyze_binary(
    binary_path: str,
    tool: str | None = None,
    arguments: dict | None = None,
) -> dict:
    """Analyze a native binary using REA.

    Args:
        binary_path: Path to the binary file to analyze.
        tool: Optional specific REA tool to use (e.g., 'inspect_binary').
        arguments: Additional arguments for the analysis.

    Returns:
        Dictionary with analysis results.
    """
    import subprocess

    args = ["npx", "-y", "rea-agents@latest", "analyze-binary", binary_path]
    if tool:
        args.extend(["--tool", tool])
    if arguments:
        for key, value in arguments.items():
            args.extend([f"--{key}", str(value)])

    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"REA binary analysis failed: {result.stderr}")
    return json.loads(result.stdout)


def rea_analyze_javascript(
    project_path: str,
    framework: str | None = None,
) -> dict:
    """Analyze a JavaScript/Electron application using REA.

    Args:
        project_path: Path to the JavaScript/Electron project directory.
        framework: Optional framework hint (e.g., "electron", "react", "vue").

    Returns:
        Dictionary with analysis results including modules, imports, etc.
    """
    import subprocess

    args = ["npx", "-y", "rea-agents@latest", "analyze-javascript", project_path]
    if framework:
        args.extend(["--framework", framework])

    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"REA JavaScript analysis failed: {result.stderr}")
    return json.loads(result.stdout)


def rea_analyze_dotnet(
    assembly_path: str,
) -> dict:
    """Analyze a .NET assembly using REA.

    Args:
        assembly_path: Path to the .NET assembly (dll/exe) to analyze.

    Returns:
        Dictionary with analysis results including types, methods, etc.
    """
    import subprocess

    args = ["npx", "-y", "rea-agents@latest", "analyze-dotnet", assembly_path]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"REA .NET analysis failed: {result.stderr}")
    return json.loads(result.stdout)


def rea_analyze_python(
    project_path: str,
) -> dict:
    """Analyze a Python application using REA.

    Args:
        project_path: Path to the Python project directory.

    Returns:
        Dictionary with analysis results including modules, imports, etc.
    """
    import subprocess

    args = ["npx", "-y", "rea-agents@latest", "analyze-python", project_path]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"REA Python analysis failed: {result.stderr}")
    return json.loads(result.stdout)


def rea_analyze_apk(
    apk_path: str,
) -> dict:
    """Analyze an Android APK using REA.

    Args:
        apk_path: Path to the APK file to analyze.

    Returns:
        Dictionary with analysis results including manifest, classes, etc.
    """
    import subprocess

    args = ["npx", "-y", "rea-agents@latest", "analyze-apk", apk_path]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"REA APK analysis failed: {result.stderr}")
    return json.loads(result.stdout)


def rea_analyze_web(
    url: str,
) -> dict:
    """Analyze a web page using REA.

    Args:
        url: URL of the web page to analyze.

    Returns:
        Dictionary with analysis results including page structure, scripts, etc.
    """
    import subprocess

    args = ["npx", "-y", "rea-agents@latest", "analyze-web", url]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"REA web analysis failed: {result.stderr}")
    return json.loads(result.stdout)


def rea_list_tools() -> dict:
    """List available REA analysis tools."""
    import subprocess

    result = subprocess.run(
        ["npx", "-y", "rea-agents@latest", "list-tools"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"REA list-tools failed: {result.stderr}")
    return json.loads(result.stdout)


def rea_check_requirements() -> dict:
    """Check REA requirements and available tools."""
    import subprocess

    result = subprocess.run(
        ["npx", "-y", "rea-agents@latest", "check"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"REA check failed: {result.stderr}")
    return json.loads(result.stdout)
