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


def _patch_chat_everywhere(monkeypatch: pytest.MonkeyPatch, reply, stream) -> None:
    """Patch ``chat``/``stream_chat`` on the client and on every module that
    imported them by name.

    ``from ollama_client import chat`` binds at import time, so patching the
    client module alone leaves agents, vision and link_tools dialling out.
    """
    import ollama_client

    monkeypatch.setattr(ollama_client, "chat", reply)
    monkeypatch.setattr(ollama_client, "stream_chat", stream)
    for name in ("agents", "vision", "link_tools"):
        module = pytest.importorskip(name, reason=f"{name} unavailable")
        if hasattr(module, "chat"):
            monkeypatch.setattr(module, "chat", reply)
        if hasattr(module, "stream_chat"):
            monkeypatch.setattr(module, "stream_chat", stream)


@pytest.fixture(autouse=True)
def offline_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test off the network, including the hosted model providers.

    Autouse because the default is now a cloud slug: without this a bare
    ``classify_intent("")`` makes a real HTTPS call and the suite takes a
    minute. A test that wants a specific reply just patches over this.
    """
    def offline_chat(base_url, model, messages, **kwargs) -> str:
        return "chat"

    _patch_chat_everywhere(monkeypatch, offline_chat, lambda *a, **k: iter(["chat"]))


@pytest.fixture
def mock_ollama_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock chat to avoid network calls during tests."""
    from unittest.mock import MagicMock

    mock_chat = MagicMock(return_value='{"summary": "test", "files": []}')
    _patch_chat_everywhere(monkeypatch, mock_chat, lambda *a, **k: iter([]))


# ---------------------------------------------------------------------------
# Trading desk fixtures. The desk has three network surfaces — market data,
# the judge brain, and the broker — so tests stub all three and keep every
# path offline, exactly like the rest of the suite.
# ---------------------------------------------------------------------------
@pytest.fixture
def desk_db(tmp_path: Path) -> Path:
    """A throwaway desk ledger."""
    return tmp_path / "desk.db"


@pytest.fixture
def stub_brain():
    """A deterministic brain. Never calls a model."""
    from desk_brain import StubBrain

    return StubBrain(action="buy", confidence=0.9)


@pytest.fixture
def desk_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Point the desk's files at tmp_path and stub every network surface.

    Patches names on the ``desk_runner`` module (they are imported into its
    namespace), so nothing here reaches the real desk folder or the internet.
    """
    import desk_runner

    desk_dir = tmp_path / "desk"
    paths = {
        "kill": desk_dir / "KILL",
        "dial": desk_dir / "dial.json",
        "logs": desk_dir / "logs",
        "db": desk_dir / "desk.db",
    }
    monkeypatch.setattr(desk_runner, "DESK_KILL_FILE", paths["kill"])
    monkeypatch.setattr(desk_runner, "DESK_DIAL_FILE", paths["dial"])
    monkeypatch.setattr(desk_runner, "DESK_LOGS", paths["logs"])

    def fake_connect(path=None):
        from desk_ledger import connect

        return connect(paths["db"])

    monkeypatch.setattr(desk_runner, "connect", fake_connect)
    # Marks never hit the network; cost basis is what we want. The exit loop
    # passes no fallback (a failed mark must skip exits), so look it up.
    def cost_basis(self, symbol, fallback, venue="stocks"):
        if fallback is not None:
            return fallback
        return next((p.avg_price for v in self.venues
                     for p in self._broker(v).positions() if p.symbol == symbol), None)

    monkeypatch.setattr(desk_runner.DeskRunner, "_mark", cost_basis)
    return paths


def make_bars(n: int = 400, start: float = 100.0, step: float = 0.4) -> list:
    """A synthetic uptrend, long enough to clear the history gate."""
    from desk_data import Bar

    return [
        Bar(ts=f"2020-{1 + i % 12:02d}-{1 + i % 28:02d}", open=start + i * step,
            high=start + i * step + 1, low=start + i * step - 1,
            close=start + i * step, volume=1_000_000, symbol="SYN")
        for i in range(n)
    ]


@pytest.fixture
def synth_bars() -> list:
    return make_bars()
