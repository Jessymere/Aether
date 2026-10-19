"""The desk seam into the orchestrator, and the streaming-callback contract.

Both of these are regression tests for real bugs:

  * the desk branch must not swallow desk requests into `chat`, and
  * `_stream_reviewer` / `_stream_tester` must accept `bus` / `agent_name`,
    because every desktop run passes them and used to raise TypeError.
"""

from __future__ import annotations

import inspect
import re

import pytest

import desk_runner
from agents import _bus_prompt, stream_ask
from message_bus import MessageBus
from orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Intent routing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "show me the trading desk",
    "run the desk",
    "backtest ma_cross on SPY",
    "hey, backtest SPY",
    "what's my portfolio doing",
    "scan the market for memecoins",
    "hit the panic button",
])
def test_desk_phrases_route_to_the_desk(text, monkeypatch):
    monkeypatch.setattr("agents.ask", lambda *a, **k: "desk")
    assert Orchestrator().classify_intent(text) == "desk"


@pytest.mark.parametrize("text", ["hi", "hey", "hello", "thanks", "ok"])
def test_greetings_still_route_to_chat(text, monkeypatch):
    monkeypatch.setattr("agents.ask", lambda *a, **k: "chat")
    assert Orchestrator().classify_intent(text) == "chat"


def test_desk_routing_makes_no_model_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a desk phrase must not need the model")

    monkeypatch.setattr("agents.ask", boom)
    assert Orchestrator().classify_intent("run the trading desk") == "desk"


def test_cycles_are_read_from_the_request():
    from orchestrator import _cycles_from

    assert _cycles_from("run 3 cycles please") == 3
    assert _cycles_from("run the desk") == 1
    assert _cycles_from("run 99 cycles") == 20  # clamped


def test_run_dispatches_to_the_desk(monkeypatch):
    monkeypatch.setattr(Orchestrator, "run_desk", lambda self, **kw: "DESK REPORT")
    result = Orchestrator().run("run the trading desk")
    assert result.mode == "desk"
    assert result.desk_report == "DESK REPORT"
    assert result.error == "DESK REPORT"  # front ends that only read .error


def test_a_desk_crash_becomes_a_reported_error(monkeypatch):
    def boom(self, **kw):
        raise RuntimeError("broker exploded")

    monkeypatch.setattr(Orchestrator, "run_desk", boom)
    result = Orchestrator().run("run the trading desk")
    assert result.mode == "desk"
    assert "broker exploded" in result.error
    assert result.traceback


def test_orchestration_result_carries_a_desk_report_field():
    from orchestrator import OrchestrationResult

    assert OrchestrationResult(request="x").desk_report is None


def test_run_desk_builds_a_runner_and_returns_its_report(monkeypatch, desk_env,
                                                         stub_brain):
    """End to end through the seam, with every network edge stubbed."""
    monkeypatch.setattr(desk_runner, "scan", lambda *a, **k: [])
    monkeypatch.setattr(desk_runner.DeskRunner, "_dial",
                        lambda self, venue: __import__("desk_seats").Dial(0.5, "t"))
    monkeypatch.setattr("desk_brain.make_brain", lambda *a, **k: stub_brain)

    report = Orchestrator().run_desk(cycles=1, venues=["stocks"])
    assert "Aether desk" in report


# ---------------------------------------------------------------------------
# The streaming contract
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["_stream_planner", "_stream_reviewer", "_stream_tester"])
def test_stream_helpers_accept_bus_and_agent_name(method):
    params = inspect.signature(getattr(Orchestrator, method)).parameters
    assert "bus" in params and "agent_name" in params


def test_stream_ask_accepts_bus_and_agent_name():
    params = inspect.signature(stream_ask).parameters
    assert "bus" in params and "agent_name" in params


def test_every_stream_call_site_passes_the_bus():
    """Guards the real bug: call sites passing kwargs the method never took.

    Matches ``self._stream_*`` so the ``def`` lines (which read ``bus=None``)
    are skipped, and searches a window after each match because some call
    sites are wrapped across lines.
    """
    source = inspect.getsource(Orchestrator)
    for call in ("self._stream_planner(", "self._stream_reviewer(",
                 "self._stream_tester("):
        sites = list(re.finditer(re.escape(call), source))
        assert sites, f"{call} is never called"
        for match in sites:
            window = source[match.end():match.end() + 400]
            assert "bus=bus" in window, window
            assert "agent_name=" in window, window


def test_bus_prompt_includes_recent_traffic():
    bus = MessageBus()
    bus.send("scan", "judge", "found SOL, liquidity 900k")
    prompt = _bus_prompt("Decide.", bus, "judge")
    assert "found SOL" in prompt
    assert "scan → judge" in prompt
    assert prompt.startswith("Decide.")


def test_bus_prompt_says_so_when_there_is_nothing():
    assert "(no prior messages)" in _bus_prompt("p", MessageBus(), "judge")


def test_blocking_and_streaming_paths_share_one_prompt_builder():
    """They must not drift — that was the original bug."""
    import agents

    assert "_bus_prompt(" in inspect.getsource(agents.stream_ask)
    assert "_bus_prompt(" in inspect.getsource(agents.ask_with_context_bus)
