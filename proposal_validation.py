import subprocess
import sys
import tempfile
from pathlib import Path


def _python_executable() -> str | None:
    """Return a Python interpreter path, or None when running as a frozen exe.

    When PyInstaller bundles the app into a single exe, sys.executable points
    to the exe itself, not to Python. We can't run compileall/unittest without
    a real Python interpreter, so return None to signal that validation should
    be skipped.
    """
    if getattr(sys, "frozen", False):
        return None
    return sys.executable


def validate_proposal(proposal: dict) -> tuple[bool, str]:
    """Compile and test a proposal in an isolated temporary directory."""
    python = _python_executable()
    if python is None:
        return True, "Validation skipped (running as bundled exe — no Python interpreter available)."

    with tempfile.TemporaryDirectory(prefix="aether-proposal-") as folder:
        root = Path(folder).resolve()
        for item in proposal.get("files", []):
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                return False, f"Unsafe path: {item['path']}"
            target = (root / relative).resolve()
            if root not in target.parents:
                return False, f"Unsafe path: {item['path']}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
        compile_result = subprocess.run(
            [python, "-m", "compileall", "-q", "."],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        if compile_result.returncode:
            return False, "Compilation failed:\n" + (compile_result.stdout + compile_result.stderr)
        test_result = subprocess.run(
            [python, "-m", "unittest", "discover", "-v"],
            cwd=root, capture_output=True, text=True, timeout=60,
        )
        if test_result.returncode:
            return False, "Tests failed:\n" + (test_result.stdout + test_result.stderr)
    return True, "Compilation and tests passed."
