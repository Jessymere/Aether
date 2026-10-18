"""
Experiential Labs Decisions client.

Calls the /v1/decisions endpoint for structured decision-making with gpt-6-luna-decisions.
Differs from /v1/chat/completions: payload uses 'input' + 'questions' (choice-type),
response returns 'answers' + 'usage' instead of 'choices'/'message'.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any


def decide(
    input_text: str,
    questions: list[dict[str, Any]],
    model: str = "gpt-6-luna-decisions",
    base_url: str = "https://api.experientiallabs.ai",
    api_key: str = "",
    timeout: int = 60,
) -> dict[str, Any]:
    """Send a decision request to the Experiential Labs Decisions API.

    Args:
        input_text: The context/change description to review.
        questions: List of question dicts with type, name, instructions, choices.
        model: Model slug (default: gpt-6-luna-decisions).
        base_url: API base URL.
        api_key: Experiential API key.
        timeout: Request timeout in seconds (default 60).

    Returns:
        Parsed JSON response with 'answers' and 'usage' keys.
    """
    body = {
        "model": model,
        "input": input_text,
        "questions": questions,
    }

    payload = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    }
    request = urllib.request.Request(
        base_url + "/v1/decisions",
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 400:
                detail = response.read().decode(errors="replace")
                raise RuntimeError(
                    "Decisions request failed (HTTP %d): %s" % (response.status, detail)
                )
            raw = response.read().decode()
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(
            "Decisions request failed (HTTP %d): %s" % (exc.code, detail)
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Could not reach Experiential Decisions API at %s. Details: %s"
            % (base_url, exc)
        ) from exc


def decide_review(
    input_text: str,
    api_key: str = "",
    model: str = "gpt-6-luna-decisions",
    base_url: str = "https://api.experientiallabs.ai",
) -> dict[str, Any]:
    """Convenience wrapper for a single review decision.

    Asks whether the supplied evidence supports accepting a change,
    or whether it needs further review. Missing evidence => review.
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
                    "value": "accept",
                    "description": (
                        "The described change meets the task and the "
                        "supplied tests cover its relevant behavior."
                    ),
                },
                {
                    "value": "review",
                    "description": (
                        "Evidence is missing, the tests are insufficient, "
                        "or the change may be incorrect."
                    ),
                },
            ],
        }
    ]
    return decide(input_text, questions, model=model, base_url=base_url, api_key=api_key)


def extract_decision(result: dict[str, Any], question_name: str = "review") -> str | None:
    """Extract the chosen value from a decisions response by question name."""
    answers = result.get("answers", [])
    for answer in answers:
        if answer.get("name") == question_name:
            return answer.get("choice") or answer.get("value")
    return None
