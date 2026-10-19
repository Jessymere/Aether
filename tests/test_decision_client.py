"""Tests for decision_client.py"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from decision_client import decide, decide_review, extract_decision, is_approved


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
                        {"value": "revise", "probability": 0.9},
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

        with patch("decision_client.urllib.request.urlopen", mock_urlopen):
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
                    "choice": "approve",
                    "confidence": 0.9,
                }
            ]
        }
        decision = extract_decision(result, "review")
        assert decision == "approve"

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
                    "choice": "approve",
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

        with patch("decision_client.urllib.request.urlopen", mock_urlopen):
            result = decide("test input", [{"type": "choice", "name": "test"}], api_key="test-key")
            assert result is not None
            assert result["answers"][0]["choice"] == "approve"

    def test_decide_raises_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decide should raise RuntimeError on HTTP error."""
        import urllib.error

        mock_urlopen = MagicMock()
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://example.com", 403, "Forbidden", {}, None
        )

        with patch("decision_client.urllib.request.urlopen", mock_urlopen):
            with pytest.raises(RuntimeError, match="Decisions request failed"):
                decide("test input", [{"type": "choice", "name": "test"}], api_key="test-key")

    def test_decide_raises_on_url_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decide should raise RuntimeError on URL error."""
        import urllib.error

        mock_urlopen = MagicMock()
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with patch("decision_client.urllib.request.urlopen", mock_urlopen):
            with pytest.raises(RuntimeError, match="Could not reach"):
                decide("test input", [{"type": "choice", "name": "test"}], api_key="test-key")


class TestDecideWithNoApiKey:
    """Tests for decide function when no explicit api_key is provided."""

    def test_decide_sends_authorization_header_with_gateway_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decide with no explicit api_key should still send Authorization header using gateway key."""
        from config import GATEWAY_API_KEY

        # Set a test gateway key
        test_gateway_key = "test-gateway-key-123"
        monkeypatch.setattr("decision_client.GATEWAY_API_KEY", test_gateway_key)

        mock_response_bytes = json.dumps({
            "model": "gpt-6-luna-decisions",
            "answers": [
                {
                    "type": "choice",
                    "name": "test",
                    "choice": "approve",
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

        with patch("decision_client.urllib.request.urlopen", mock_urlopen) as mock_urlopen_call:
            decide("test input", [{"type": "choice", "name": "test"}], api_key=None)
            # Check that the request was made with the gateway key in the Authorization header
            args, kwargs = mock_urlopen_call.call_args
            request: urllib.request.Request = args[0]
            assert request.headers.get("Authorization") == f"Bearer {test_gateway_key}"


class TestDecideReviewReturnsSameAsDecide:
    """Tests that decide_review returns exactly what decide returns."""

    def test_decide_review_returns_same_as_decide(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decide_review should return the same result as decide with its internal questions."""
        from config import DECISION_MODEL, DECISIONS_URL, GATEWAY_API_KEY

        # Use test values for config
        test_model = "test-model"
        test_base_url = "https://test.example.com"
        test_api_key = "test-api-key"
        monkeypatch.setattr("decision_client.DECISION_MODEL", test_model)
        monkeypatch.setattr("decision_client.DECISIONS_URL", test_base_url)
        monkeypatch.setattr("decision_client.GATEWAY_API_KEY", test_api_key)

        # Define the questions that decide_review uses internally
        review_questions = [
            {
                "type": "choice",
                "name": "review",
                "instructions": (
                    "Does the supplied evidence support accepting this change, "
                    "or does it need further review? Treat missing evidence as "
                    "a reason to review."
                ),
                "choices": [
                    {
                        "value": "approve",
                        "description": (
                            "The described change meets the task and the "
                            "included tests pass."
                        ),
                    },
                    {
                        "value": "revise",
                        "description": (
                            "Evidence is missing, tests are insufficient, "
                            "or the change may be incorrect."
                        ),
                    },
                ],
            }
        ]

        mock_response_bytes = json.dumps({
            "model": test_model,
            "answers": [
                {
                    "type": "choice",
                    "name": "review",
                    "choice": "approve",
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

        with patch("decision_client.urllib.request.urlopen", mock_urlopen):
            # Call decide_review
            result_review = decide_review("Test input", api_key=test_api_key)
            # Call decide with the same parameters and the review questions
            result_decide = decide(
                "Test input",
                review_questions,
                model=test_model,
                base_url=test_base_url,
                api_key=test_api_key,
            )
            assert result_review == result_decide