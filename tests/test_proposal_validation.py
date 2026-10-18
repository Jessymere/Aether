"""Tests for proposal_validation.py"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from proposal_validation import validate_proposal, _python_executable


class TestValidateProposal:
    """Tests for the validate_proposal function."""

    def test_valid_proposal_passes(self, temp_workspace: Path, valid_proposal: dict) -> None:
        """A valid proposal with compilable Python should pass validation."""
        # Temporarily override the sandbox directory to use our temp workspace
        original_tempdir = tempfile.TemporaryDirectory
        try:
            tempfile.TemporaryDirectory = lambda prefix="aether-proposal-": _FakeTempDir(
                temp_workspace
            )
            ok, msg = validate_proposal(valid_proposal)
            assert ok is True, f"Expected valid proposal to pass, got: {msg}"
        finally:
            tempfile.TemporaryDirectory = original_tempdir

    def test_unsafe_absolute_path_rejected(
        self, temp_workspace: Path, unsafe_absolute_path_proposal: dict
    ) -> None:
        """An absolute path should be rejected."""
        ok, msg = validate_proposal(unsafe_absolute_path_proposal)
        assert ok is False
        assert "Unsafe path" in msg

    def test_unsafe_traversal_path_rejected(
        self, temp_workspace: Path, unsafe_traversal_path_proposal: dict
    ) -> None:
        """A path with .. traversal should be rejected."""
        ok, msg = validate_proposal(unsafe_traversal_path_proposal)
        assert ok is False
        assert "Unsafe path" in msg

    def test_empty_proposal_passes(self, temp_workspace: Path, empty_proposal: dict) -> None:
        """An empty proposal (no files) should pass validation."""
        ok, msg = validate_proposal(empty_proposal)
        assert ok is True, f"Expected empty proposal to pass, got: {msg}"

    def test_proposal_with_syntax_error_fails(
        self, temp_workspace: Path
    ) -> None:
        """A proposal with Python syntax errors should fail compilation."""
        bad_proposal = {
            "summary": "Syntax error",
            "files": [
                {
                    "path": "bad.py",
                    "content": "def broken(:\n    pass\n",
                },
            ],
        }
        ok, msg = validate_proposal(bad_proposal)
        assert ok is False
        assert "Compilation failed" in msg


class TestPythonExecutable:
    """Tests for the _python_executable helper."""

    def test_returns_python_executable(self) -> None:
        """_python_executable should return a non-None path in non-frozen mode."""
        result = _python_executable()
        assert result is not None
        assert result.endswith("python.exe") or result.endswith("python")


class _FakeTempDir:
    """Fake temp dir that reuses an existing directory for testing."""

    def __init__(self, prefix: str = "aether-proposal-") -> None:
        self.prefix = str(prefix)
        self._dir = None

    def __enter__(self) -> Path:
        return Path(tempfile.mkdtemp(prefix=self.prefix))

    def __exit__(self, *args) -> None:
        pass
