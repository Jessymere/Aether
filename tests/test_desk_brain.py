"""The decision socket. The model picks; code does the arithmetic."""

from __future__ import annotations

import desk_brain as DB


# ---------------------------------------------------------------------------
# Coercion — the model must never break the desk by phrasing things oddly
# ---------------------------------------------------------------------------
def test_action_coercion_handles_prose():
    assert DB._coerce_action("BUY") == "buy"
    assert DB._coerce_action("I would buy this") == "buy"
    assert DB._coerce_action("skip.") == "skip"
    assert DB._coerce_action("hold") == "hold"


def test_action_coercion_defaults_to_skip_not_buy():
    """The safe default. An unparseable answer must not open a position."""
    assert DB._coerce_action(None) == "skip"
    assert DB._coerce_action("gibberish") == "skip"


def test_confidence_words_map_to_numbers_in_code():
    assert DB._coerce_confidence("high") == 0.9
    assert DB._coerce_confidence("medium") == 0.6
    assert DB._coerce_confidence("low") == 0.3


def test_confidence_accepts_a_number_too():
    assert DB._coerce_confidence(0.75) == 0.75
    assert DB._coerce_confidence(75) == 0.75  # a percentage was meant


def test_unknown_confidence_is_zero_so_the_floor_catches_it():
    assert DB._coerce_confidence("extremely sure") == 0.0
    assert DB._coerce_confidence(None) == 0.0


def test_prompt_is_plain_lines(monkeypatch):
    prompt = DB.build_prompt({"price": 1.5, "liquidity_usd": 250_000})
    assert "- price: 1.5" in prompt and "- liquidity_usd: 250000" in prompt


# ---------------------------------------------------------------------------
# StubBrain
# ---------------------------------------------------------------------------
def test_stub_brain_returns_a_decision_every_time():
    decision = DB.StubBrain("buy", 0.9).decide({}, symbol="SOL")
    assert decision.action == "buy" and decision.symbol == "SOL"
    assert decision.skipped_reason == ""


def test_stub_brain_records_a_skip_reason_for_non_buys():
    assert DB.StubBrain("skip", 0.1).decide({}).skipped_reason.startswith("stub")


# ---------------------------------------------------------------------------
# make_brain routing — never inferred from a key being present
# ---------------------------------------------------------------------------
def test_make_brain_honours_an_explicit_choice(monkeypatch):
    assert isinstance(DB.make_brain("ollama"), DB.OllamaBrain)
    assert isinstance(DB.make_brain("stub"), DB.StubBrain)
    assert isinstance(DB.make_brain("socket"), DB.SocketBrain)


def test_socket_falls_back_to_ollama_without_a_gateway_key(monkeypatch):
    monkeypatch.setattr(DB, "GATEWAY_API_KEY", "")
    assert isinstance(DB.make_brain("socket"), DB.OllamaBrain)


def test_socket_is_used_when_the_key_is_present(monkeypatch):
    monkeypatch.setattr(DB, "GATEWAY_API_KEY", "xpl_test")
    assert isinstance(DB.make_brain("socket"), DB.SocketBrain)


def test_make_brain_never_raises_on_junk():
    assert isinstance(DB.make_brain("nonsense"), DB.SocketBrain)
    assert isinstance(DB.make_brain(None), (DB.SocketBrain, DB.OllamaBrain, DB.StubBrain))


# ---------------------------------------------------------------------------
# SocketBrain — against a stubbed decision_client
# ---------------------------------------------------------------------------
def test_socket_brain_parses_a_typed_answer(monkeypatch):
    import decision_client

    # ``extract_decision`` reads the ``choice`` key — that is the gateway's
    # answer shape, not ``value``.
    monkeypatch.setattr(decision_client, "decide", lambda *a, **k: {
        "answers": [{"name": "action", "choice": "buy"},
                    {"name": "confidence", "choice": "high"}]})
    decision = DB.SocketBrain().decide({"price": 1.0}, symbol="SOL")
    assert decision.action == "buy"
    assert decision.confidence == 0.9
    assert decision.brain == "socket"
    assert decision.latency_ms >= 0


def test_socket_brain_asks_for_choices_not_free_text(monkeypatch):
    import decision_client

    captured = {}

    def fake_decide(text, questions, **kwargs):
        captured["questions"] = questions
        return {"answers": []}

    monkeypatch.setattr(decision_client, "decide", fake_decide)
    DB.SocketBrain().decide({"price": 1.0}, symbol="X")
    names = [q["name"] for q in captured["questions"]]
    assert names == ["action", "confidence"]
    assert all(q["type"] == "choice" for q in captured["questions"])


def test_socket_brain_degrades_to_skip_on_a_network_error(monkeypatch):
    import decision_client

    def boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(decision_client, "decide", boom)
    decision = DB.SocketBrain().decide({}, symbol="X")
    assert decision.action == "skip"
    assert "brain unavailable" in decision.skipped_reason


# ---------------------------------------------------------------------------
# OllamaBrain
# ---------------------------------------------------------------------------
def test_ollama_brain_parses_json_out_of_noise(monkeypatch):
    import ollama_client

    monkeypatch.setattr(DB, "model_route", lambda m: ("", None, "local"))
    monkeypatch.setattr(ollama_client, "chat",
                        lambda *a, **k: 'sure!\n{"action": "buy", "confidence": "medium"}')
    decision = DB.OllamaBrain("m").decide({}, symbol="X")
    assert decision.action == "buy" and decision.confidence == 0.6


def test_json_loader_survives_garbage():
    assert DB._loads("not json") == {}
    assert DB._loads("") == {}
    assert DB._loads('{"a": 1}') == {"a": 1}
    assert DB._loads("prefix {\"a\": 1} suffix") == {"a": 1}
    assert DB._loads([1, 2]) == {}


def test_ollama_brain_degrades_to_skip_on_error(monkeypatch):
    import ollama_client

    monkeypatch.setattr(DB, "model_route", lambda m: ("", None, "local"))

    def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(ollama_client, "chat", boom)
    assert DB.OllamaBrain("m").decide({}).action == "skip"
