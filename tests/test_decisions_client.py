"""Tests for decisions_client.py"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from decisions_client import decide, decide_review, extract_decision


class TestDecideReview:
    """Tests for decide_review function."""

    def test_decide_review_returns_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decide_review should return a dict from the decisions API."""
        mock_response = {
            "model": "gpt-6-luna-decisions",
            "answers": [
                {
                    "type": "choice",
                    "name": "review",
                    "choice": "review",
                    "probabilities": [
                        {"value": "accept", "probability": 0.1},
                        {"value": "review", "probability": 0.9},
                    ],
                    "confidence": 0.9,
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100, "cost": 0.0},
        }
        response_bytes = json.dumps(mock_response).encode()

        mock_urlopen = MagicMock()
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.read.return_value = response_bytes
        mock_urlopen.return_value.__enter__.return_value = mock_response_obj

        with patch("decisions_client.urllib.request.urlopen", mock_urlopen):
            result = decide_review("Test input", api_key="test-key")
            assert result is not None
            assert "answers" in result
            assert result["answers"][0]["choice"] == "review"

    def test_extract_decision_returns_choice(self) -> None:
        """extract_decision should return the choice value from a result."""
        result = {
            "answers": [
                {
                    "type": "choice",
                    "name": "review",
                    "choice": "accept",
                    "confidence": 0.9,
                }
            ]
        }
        decision = extract_decision(result, "review")
        assert decision == "accept"

    def test_extract_decision_returns_none_when_not_found(self) -> None:
        """extract_decision should return None when question name not found."""
        result = {"answers": [{"name": "other", "choice": "yes"}]}
        decision = extract_decision(result, "review")
        assert decision is None

    def test_extract_decision_returns_none_when_empty(self) -> None:
        """extract_decision should return None when answers list is empty."""
        result = {"answers": []}
        decision = extract_decision(result, "review")
        assert decision is None

    def test_extract_decision_returns_none_when_no_answers(self) -> None:
        """extract_decision should return None when no answers key."""
        result = {}
        decision = extract_decision(result, "review")
        assert decision is None


class TestDecideFunction:
    """Tests for the decide function."""

    def test_decide_returns_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decide should return a dict from the API."""
        mock_response_bytes = json.dumps({
            "model": "gpt-6-luna-decisions",
            "answers": [
                {
                    "type": "choice",
                    "name": "test",
                    "choice": "accept",
                    "confidence": 0.95,
                }
            ],
            "usage": {"input_tokens": 50, "output_tokens": 0, "total_tokens": 50, "cost": 0.0},
        }).encode()

        mock_urlopen = MagicMock()
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.read.return_value = mock_response_bytes
        mock_urlopen.return_value.__enter__.return_value = mock_response_obj

        with patch("decisions_client.urllib.request.urlopen", mock_urlopen):
            result = decide("test input", [{"type": "choice", "name": "test"}], api_key="test-key")
            assert result is not None
            assert result["answers"][0]["choice"] == "accept"

    def test_decide_raises_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decide should raise RuntimeError on HTTP error."""
        import urllib.error

        mock_urlopen = MagicMock()
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://example.com", 403, "Forbidden", {}, None
        )

        with patch("decisions_client.urllib.request.urlopen", mock_urlopen):
            with pytest.raises(RuntimeError, match="Decisions request failed"):
                decide("test input", [{"type": "choice", "name": "test"}], api_key="test-key")

    def test_decide_raises_on_url_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decide should raise RuntimeError on URL error."""
        import urllib.error

        mock_urlopen = MagicMock()
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with patch("decisions_client.urllib.request.urlopen", mock_urlopen):
            with pytest.raises(RuntimeError, match="Could not reach"):
                decide("test input", [{"type": "choice", "name": "test"}], api_key="test-key")
