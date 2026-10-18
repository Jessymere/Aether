"""Shared pytest fixtures for Aether tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory and return its path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory and return its path."""
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return project


@pytest.fixture
def mock_ollama_url() -> str:
    """Return a dummy Ollama URL (never actually called in unit tests)."""
    return "http://127.0.0.1:11434"


@pytest.fixture
def empty_proposal() -> dict:
    """Return an empty proposal dict."""
    return {"files": []}


@pytest.fixture
def valid_proposal() -> dict:
    """Return a valid proposal dict with a simple Python file."""
    return {
        "summary": "Create hello.py",
        "files": [
            {
                "path": "hello.py",
                "content": "print('hello world')\n",
            },
        ],
    }


@pytest.fixture
def unsafe_absolute_path_proposal() -> dict:
    """Return a proposal with an absolute path (should be rejected)."""
    return {
        "summary": "Unsafe path",
        "files": [
            {
                "path": "/etc/passwd",
                "content": "bad\n",
            },
        ],
    }


@pytest.fixture
def unsafe_traversal_path_proposal() -> dict:
    """Return a proposal with path traversal (should be rejected)."""
    return {
        "summary": "Path traversal",
        "files": [
            {
                "path": "../../etc/passwd",
                "content": "bad\n",
            },
        ],
    }


@pytest.fixture
def invalid_json_proposal() -> dict:
    """Return a proposal missing the files list (should be rejected by parse_proposal)."""
    return {"summary": "No files"}


@pytest.fixture
def mock_ollama_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock ollama_client.chat to avoid network calls during tests."""
    from unittest.mock import MagicMock

    import ollama_client
    import agents

    mock_chat = MagicMock(return_value='{"summary": "test", "files": []}')

    def mock_chat_func(
        base_url: str,
        model: str,
        messages: list,
        timeout: int = 600,
        json_mode: bool = False,
        num_predict: int = 2048,
        images: list | None = None,
        api_key: str | None = None,
    ) -> str:
        return mock_chat(base_url, model, messages, timeout, json_mode, num_predict, images, api_key)

    monkeypatch.setattr(ollama_client, "chat", mock_chat_func)
