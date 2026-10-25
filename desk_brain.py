"""The decision socket: the one seat that is allowed an opinion.

Architecture B's contract, taken literally. The brain answers exactly one
narrow question — ``buy``, ``hold`` or ``skip`` — plus a coarse confidence.
It never computes a number; every threshold lives in ``desk_seats`` /
``desk_backtest`` and is applied *around* this call.

Confidence is asked as a *choice* (low/medium/high) rather than a float,
because the model is bad at arithmetic and good at picking. The bucket is
mapped to a number in code, which is the whole point.

Backends share one interface:

  * ``OpenRouterBrain`` — a chat decision routed through ``config.model_route``.
  * ``OllamaBrain`` — explicitly selected local-model compatibility path.
  * ``SocketBrain`` — legacy typed decisions endpoint, available by explicit
    configuration but no longer the default.

Selection goes through :func:`make_brain`. Which surface a model lives on is
decided by ``config.model_route``, never by whether a key happens to be set.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional, Protocol

from config import (
    DECISION_MODEL,
    DECISIONS_URL,
    DESK_BRAIN,
    DESK_BRAIN_FALLBACK_MODEL,
    DESK_BRAIN_MODEL,
    GATEWAY_API_KEY,
    OLLAMA_URL,
    model_route,
)
from desk_ledger import Decision

#: The model picks one of these; code maps it to a number.
CONFIDENCE_WORDS = {"low": 0.3, "medium": 0.6, "high": 0.9}
ACTIONS = ("buy", "hold", "skip")

SYSTEM = (
    "You are the judging seat of a small trading desk. You are given the hard "
    "numbers for one candidate. Decide whether to BUY, HOLD or SKIP, and how "
    "sure you are. Do not do arithmetic — the numbers are already computed. "
    "Treat missing or thin data as a reason to skip."
)


class Brain(Protocol):
    """What the JUDGE seat can rely on."""

    name: str

    def decide(self, facts: dict[str, Any], symbol: str = "") -> Decision:
        ...


def build_prompt(facts: dict[str, Any]) -> str:
    """Render facts as plain ``name: value`` lines so nothing needs computing.

    The framing matters as much as the facts. Without it a judge model tends to
    answer ``skip`` for everything: the numbers alone say nothing about the
    fact that the rule already survived a walk-forward gate on years it never
    saw, and every threshold it is being held to is enforced by code anyway.
    """
    lines = [f"- {key}: {value}" for key, value in facts.items()]
    record = (
        "\nThe desk's own closed record is in the facts as record_*. Judge this "
        "candidate on its own merits, but let a cold record make you choosier."
        if facts.get("record_closed_trades") else ""
    )
    return (
        "You are the JUDGE seat of a trading desk. One candidate is in front of "
        "you. A deterministic backtest has already accepted its rule; the "
        "floors and ceilings are enforced elsewhere, so do not recompute them.\n\n"
        "Candidate facts:\n"
        + "\n".join(lines) + record
        + "\n\nThe return_5bar_pct, return_20bar_pct and return_60bar_pct "
        "fields are historical returns, not forecasts. Use "
        "multi_horizon_alignment to notice disagreement across timeframes; "
        "higher volatility, a weak volume ratio or a deep drawdown are risk "
        "evidence, not reasons to invent missing facts.\n\n"
        "Answer buy, hold or skip, with your confidence. Buy means open a "
        "position now; hold means the setup is good but not yet; skip means pass."
    )


def _coerce_action(value: Any) -> str:
    text = str(value or "").strip().lower()
    for action in ACTIONS:
        if action in text:
            return action
    return "skip"


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 100.0 if number > 1.0 else number
    return CONFIDENCE_WORDS.get(str(value or "").strip().lower(), 0.0)


class SocketBrain:
    """Typed decision via the Experiential Labs decisions endpoint."""

    name = "socket"

    def __init__(self, model: str = DESK_BRAIN_MODEL, timeout: int = 30) -> None:
        self.model = model
        self.timeout = timeout

    def decide(self, facts: dict[str, Any], symbol: str = "") -> Decision:
        from decision_client import decide, extract_decision

        questions = [
            {
                "type": "choice",
                "name": "action",
                "instructions": (
                    "Should this candidate be bought now, held, or skipped? "
                    "Skip if the evidence is thin or contradictory."
                ),
                "choices": [
                    {"value": "buy", "description": "Open a position now."},
                    {"value": "hold", "description": "Fine, but not worth acting on today."},
                    {"value": "skip", "description": "Thin, contradictory, or unattractive."},
                ],
            },
            {
                "type": "choice",
                "name": "confidence",
                "instructions": "How sure are you? Pick one word.",
                "choices": [
                    {"value": "low", "description": "Weak conviction."},
                    {"value": "medium", "description": "Reasonable conviction."},
                    {"value": "high", "description": "Strong conviction."},
                ],
            },
        ]

        started = time.time()
        try:
            result = decide(build_prompt(facts), questions, model=self.model,
                            base_url=DECISIONS_URL, api_key=GATEWAY_API_KEY,
                            timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            return Decision(
                symbol=symbol, action="skip", confidence=0.0, brain=self.name,
                model=self.model, reason=f"brain unavailable: {exc}",
                skipped_reason=f"brain unavailable: {exc}",
            )

        action = _coerce_action(extract_decision(result, "action"))
        confidence = _coerce_confidence(extract_decision(result, "confidence"))
        latency = int((time.time() - started) * 1000)
        return Decision(
            symbol=symbol, action=action, confidence=confidence, brain=self.name,
            model=self.model, raw=result, latency_ms=latency,
            reason=f"judge said {action} ({confidence:.2f})",
            skipped_reason="" if action == "buy" else f"judge: {action}",
        )


class OllamaBrain:
    """Ask a configured chat model; the route may be local or hosted."""

    name = "ollama"

    def __init__(self, model: str = DESK_BRAIN_FALLBACK_MODEL) -> None:
        self.model = model

    def decide(self, facts: dict[str, Any], symbol: str = "") -> Decision:
        from ollama_client import chat

        base_url, api_key, surface = model_route(self.model)
        prompt = (
            build_prompt(facts)
            + '\n\nReply with JSON only: {"action": "buy|hold|skip", '
              '"confidence": "low|medium|high"}'
        )
        started = time.time()
        try:
            raw = chat(
                base_url or OLLAMA_URL,
                self.model,
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": prompt}],
                # Reasoning models spend tokens thinking before the JSON; at 120
                # they were cut off mid-thought and every reply came back empty.
                json_mode=True, num_predict=1500, api_key=api_key, surface=surface,
            )
            payload = _loads(raw)
        except Exception as exc:  # noqa: BLE001
            return Decision(
                symbol=symbol, action="skip", confidence=0.0, brain=self.name,
                model=self.model, reason=f"brain unavailable: {exc}",
                skipped_reason=f"brain unavailable: {exc}",
            )
        if "action" not in payload:
            # Still a skip, but named, so the ledger never shows a broken reply
            # as the judge's considered opinion.
            return Decision(
                symbol=symbol, action="skip", confidence=0.0, brain=self.name,
                model=self.model, raw=payload, reason="judge reply unparseable",
                skipped_reason="judge reply unparseable",
            )

        action = _coerce_action(payload.get("action"))
        confidence = _coerce_confidence(payload.get("confidence"))
        latency = int((time.time() - started) * 1000)
        return Decision(
            symbol=symbol, action=action, confidence=confidence, brain=self.name,
            model=self.model, raw=payload, latency_ms=latency,
            reason=f"judge said {action} ({confidence:.2f})",
            skipped_reason="" if action == "buy" else f"judge: {action}",
        )


class OpenRouterBrain(OllamaBrain):
    """Trading-desk decision seat using the configured OpenRouter model."""

    name = "openrouter"

    def __init__(self, model: str = DESK_BRAIN_MODEL,
                 fallback: str = DESK_BRAIN_FALLBACK_MODEL) -> None:
        super().__init__(model)
        self.fallback = fallback

    def decide(self, facts: dict[str, Any], symbol: str = "") -> Decision:
        first = super().decide(facts, symbol)
        unusable = first.skipped_reason.startswith(("brain unavailable", "judge reply unparseable"))
        if not unusable or not self.fallback or self.fallback == self.model:
            return first
        second = OllamaBrain(self.fallback).decide(facts, symbol)
        second.brain = self.name
        return second


class StubBrain:
    """Deterministic brain for tests and for ``--offline`` runs."""

    name = "stub"

    def __init__(self, action: str = "buy", confidence: float = 0.8) -> None:
        self.action = action
        self.confidence = confidence

    def decide(self, facts: dict[str, Any], symbol: str = "") -> Decision:
        return Decision(
            symbol=symbol, action=self.action, confidence=self.confidence,
            brain=self.name, reason=f"stub {self.action}",
            skipped_reason="" if self.action == "buy" else f"stub: {self.action}",
        )


def make_brain(name: Optional[str] = None) -> Brain:
    """Resolve a brain by name, degrading sensibly when a route is unusable."""
    chosen = (name or DESK_BRAIN or "socket").strip().lower()
    if chosen == "openrouter":
        return OpenRouterBrain()
    if chosen == "ollama":
        return OllamaBrain()
    if chosen == "stub":
        return StubBrain()
    if chosen == "socket":
        # No gateway key means the socket would 401; fall back rather than
        # fail every decision. Note this is an explicit routing choice, not
        # an inference about which surface the model lives on.
        if GATEWAY_API_KEY:
            return SocketBrain()
        return OllamaBrain()
    return SocketBrain(model=DESK_BRAIN_MODEL or DECISION_MODEL)


def _loads(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}
