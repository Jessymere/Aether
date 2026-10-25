"""Tests for orchestrator intent classification."""

from __future__ import annotations

from pathlib import Path

from unittest.mock import MagicMock, patch

import pytest

from orchestrator import Orchestrator


class TestClassifyIntent:
    """Tests for the classify_intent method."""

    def test_greeting_returns_chat(self) -> None:
        """Simple greetings should be classified as chat."""
        orch = Orchestrator()
        assert orch.classify_intent("hi") == "chat"
        assert orch.classify_intent("hey") == "chat"
        assert orch.classify_intent("hello") == "chat"
        assert orch.classify_intent("good morning") == "chat"
        assert orch.classify_intent("good afternoon") == "chat"
        assert orch.classify_intent("good evening") == "chat"
        assert orch.classify_intent("thanks") == "chat"
        assert orch.classify_intent("thank you") == "chat"
        assert orch.classify_intent("ok") == "chat"
        assert orch.classify_intent("okay") == "chat"
        assert orch.classify_intent("are you there") == "chat"
        assert orch.classify_intent("who are you") == "chat"
        assert orch.classify_intent("help") == "chat"

    def test_short_greeting_returns_chat(self) -> None:
        """Short greetings under 40 chars should return chat without calling Ollama."""
        orch = Orchestrator()
        assert orch.classify_intent("hi") == "chat"
        assert orch.classify_intent("hello") == "chat"
        assert orch.classify_intent("hey") == "chat"

    def test_empty_string_returns_chat(self) -> None:
        """Empty string should default to chat."""
        orch = Orchestrator()
        assert orch.classify_intent("") == "chat"

    def test_whitespace_only_returns_chat(self) -> None:
        """Whitespace-only input should default to chat."""
        orch = Orchestrator()
        assert orch.classify_intent("   ") == "chat"

    @pytest.mark.parametrize("text", [
        "improve my AI",
        "fix my app",
        "add memory to the assistant",
        "please update the project",
    ])
    def test_clear_software_changes_route_to_build_without_classifier(self, text, monkeypatch) -> None:
        """Direct change requests should enter the build pipeline deterministically."""
        def unexpected_model_call(*args, **kwargs):
            raise AssertionError("clear software changes should not need classification")

        monkeypatch.setattr("orchestrator.ask", unexpected_model_call)
        assert Orchestrator().classify_intent(text) == "build"

    def test_capability_question_still_routes_to_chat(self) -> None:
        """A general question about Aether must not be mistaken for a change request."""
        assert Orchestrator().classify_intent("what can you do?") == "chat"


class TestOrchestratorInit:
    """Tests for Orchestrator initialization."""

    def test_default_values(self) -> None:
        """Default Orchestrator values should be correct."""
        orch = Orchestrator()
        assert orch.project is not None
        assert orch.auto_approve is False
        assert orch.fast is False
        assert orch.quality_rounds == 1
        assert orch.web is False
        assert orch.auto_web is False
        assert orch._cancelled is False

    def test_custom_values(self) -> None:
        """Custom values should be set correctly."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            orch = Orchestrator(
                project=project,
                auto_approve=True,
                fast=True,
                quality_rounds=2,
                web=True,
                auto_web=True,
            )
            assert orch.project == project.resolve()
            assert orch.auto_approve is True
            assert orch.fast is True
            assert orch.quality_rounds == 2
            assert orch.web is True
            assert orch.auto_web is True

    def test_quality_rounds_clamped(self) -> None:
        """quality_rounds should be clamped to 0-2 range."""
        orch = Orchestrator(quality_rounds=5)
        assert orch.quality_rounds == 2

        orch = Orchestrator(quality_rounds=-1)
        assert orch.quality_rounds == 0

        orch = Orchestrator(quality_rounds=1)
        assert orch.quality_rounds == 1


from pathlib import Path


class TestFastModels:
    """Tests for fast model switching."""

    def test_fast_models_are_faster(self) -> None:
        """Every seat moves to a different, lighter model under --fast."""
        from config import FAST_MODELS, MODELS

        for role, model in FAST_MODELS.items():
            assert model != MODELS[role], f"{role} does not actually switch in fast mode"

    def test_every_configured_model_has_a_provider(self) -> None:
        """Nothing may be a cloud slug that routing would quietly send to Ollama."""
        from config import FAST_MODELS, MODELS, model_route

        for role, model in {**MODELS, **FAST_MODELS}.items():
            _, _, surface = model_route(model)
            assert surface != "local", f"{role} -> {model} is unrouted"

    def test_all_roles_have_fast_models(self) -> None:
        """All MODELS roles should have a corresponding FAST_MODELS entry."""
        from config import MODELS, FAST_MODELS

        for role in MODELS:
            assert role in FAST_MODELS, f"Role {role} missing from FAST_MODELS"
