"""
Experiential Labs Decisions client for Aether.

Calls the /v1/decisions endpoint with gpt-6-luna-decisions model for
fast, typed decision-making (APPROVE/REVISE with confidence scores).

This is distinct from the Ollama chat API (/v1/chat/completions) and
is designed for lightweight decision gates within the build pipeline.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any, Optional

from config import DECISIONS_URL, DECISION_MODEL, GATEWAY_API_KEY


def decide(
    input_text: str,
    questions: list[dict],
    model: str = DECISION_MODEL,
    base_url: str = DECISIONS_URL,
    api_key: str | None = None,
    timeout: int = 60,
) -> dict:
    """Send a decision request to the Experiential Decisions API.

    Args:
        input_text: The context/request text to evaluate.
        questions: List of question dicts with type, name, instructions, choices.
        model: Model slug (default: gpt-6-luna-decisions).
        base_url: API base URL.
        api_key: Experiential API key.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response with 'answers' and 'usage' keys.
    """
    # Fall back to the configured gateway key so callers that pass nothing
    # (including decide_review below) still authenticate.
    api_key = api_key or GATEWAY_API_KEY

    body = {
        "model": model,
        "input": input_text,
        "questions": questions,
    }

    payload = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{base_url}/v1/decisions",
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 400:
                detail = response.read().decode(errors="replace")
                raise RuntimeError(
                    f"Decisions request failed (HTTP {response.status}): {detail}"
                )
            raw = response.read().decode()
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"Decisions request failed (HTTP {exc.code}): {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Decisions API at {base_url}. Details: {exc}"
        ) from exc


def decide_review(
    input_text: str,
    api_key: str | None = None,
    model: str = DECISION_MODEL,
    base_url: str = DECISIONS_URL,
) -> dict:
    """Convenience wrapper for a review decision.

    Asks whether the supplied evidence supports accepting a change,
    or whether it needs further review. Missing evidence => review.

    Args:
        input_text: Context/proposal text to review.
        api_key: Experiential API key.
        model: Model slug.
        base_url: API base URL.

    Returns:
        Decision result dict with 'answers' containing the choice.
    """
    questions = [
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
    return decide(input_text, questions, model=model, base_url=base_url, api_key=api_key)


def extract_decision(result: dict, question_name: str = "review") -> str | None:
    """Extract the chosen value from a decisions response by question name.

    Args:
        result: The decision result dict from decide()/decide_review().
        question_name: The question name to extract (default: "review").

    Returns:
        The chosen value (e.g. "approve" or "revise"), or None if not found.
    """
    answers = result.get("answers", [])
    for answer in answers:
        if answer.get("name") == question_name:
            return answer.get("choice")
    return None


def is_approved(result: dict, question_name: str = "review") -> bool:
    """Check if a decision result indicates approval.

    Args:
        result: The decision result dict.
        question_name: The question name to check.

    Returns:
        True if the decision is "approve", False otherwise.
    """
    decision = extract_decision(result, question_name)
    return decision == "approve"
