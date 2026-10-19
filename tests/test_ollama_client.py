"""Wire-format tests for the model client.

The client speaks two dialects: Ollama's own for a local model, and the
OpenAI-compatible one for every hosted provider. Getting that switch wrong is
silent — images sent in the Ollama dialect to a cloud model are ignored and the
answer comes back describing nothing — so the format decision is pinned here
rather than exercised only by hand.

``chat`` is imported at module scope on purpose: the autouse fixture in
conftest rebinds ``ollama_client.chat``, which a local import would pick up.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

import ollama_client
from ollama_client import (
    _attach_images,
    _message_text,
    _retry_delay,
    _retryable,
    _uses_openai_format,
    chat,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


def capture(monkeypatch, payload=None, error=None):
    """Replace urlopen and return the list of requests it received."""
    seen: list = []

    def fake(request, timeout=None):
        seen.append(request)
        if error is not None:
            raise error
        return FakeResponse(payload or {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", fake)
    return seen


# ---------------------------------------------------------------------------
# Surface -> wire format
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("surface", ["gateway", "nim", "openrouter"])
def test_hosted_surfaces_use_the_openai_wire(surface: str) -> None:
    assert _uses_openai_format("https://example.test", "k", surface) is True


def test_local_surface_uses_the_ollama_wire_even_with_a_key() -> None:
    assert _uses_openai_format("http://127.0.0.1:11434", "stray", "local") is False


def test_surface_beats_every_inference_fallback() -> None:
    """A loopback URL must not drag a hosted seat back to the Ollama wire."""
    assert _uses_openai_format("http://127.0.0.1:11434", "k", "nim") is True


def test_unknown_surface_falls_back_to_the_url_shape() -> None:
    assert _uses_openai_format("https://example.test", None, None) is True
    assert _uses_openai_format("http://localhost:11434", None, None) is False


# ---------------------------------------------------------------------------
# Image attachment
# ---------------------------------------------------------------------------
def test_ollama_images_are_a_bare_list() -> None:
    message = _attach_images({"role": "user", "content": "look"}, ["AAA"], openai=False)

    assert message["images"] == ["AAA"]
    assert message["content"] == "look"


def test_openai_images_become_data_uri_content_parts() -> None:
    message = _attach_images({"role": "user", "content": "look"}, ["AAA", "BBB"], openai=True)

    assert isinstance(message["content"], list)
    assert message["content"][0] == {"type": "text", "text": "look"}
    assert [part["image_url"]["url"] for part in message["content"][1:]] == [
        "data:image/jpeg;base64,AAA",
        "data:image/jpeg;base64,BBB",
    ]


def test_attach_images_leaves_no_ollama_key_on_a_cloud_message() -> None:
    message = _attach_images({"role": "user", "content": "look"}, ["AAA"], openai=True)

    assert "images" not in message


# ---------------------------------------------------------------------------
# Request shape through chat()
# ---------------------------------------------------------------------------
def test_chat_posts_to_the_openai_path_with_a_bearer_token(monkeypatch) -> None:
    seen = capture(monkeypatch)

    chat("https://integrate.api.nvidia.com", "m", [{"role": "user", "content": "hi"}],
         api_key="secret", surface="nim")

    request = seen[0]
    assert request.full_url.endswith("/v1/chat/completions")
    assert request.get_header("Authorization") == "Bearer secret"

    body = json.loads(request.data)
    assert body["max_tokens"] == 2048


def test_chat_posts_to_the_ollama_path_without_a_token(monkeypatch) -> None:
    seen = capture(monkeypatch, {"message": {"content": "ok"}})

    chat("http://127.0.0.1:11434", "m", [{"role": "user", "content": "hi"}], surface="local")

    request = seen[0]
    assert request.full_url.endswith("/api/chat")
    assert request.get_header("Authorization") is None


def test_chat_sends_images_as_content_parts_on_a_hosted_surface(monkeypatch) -> None:
    seen = capture(monkeypatch)

    chat("https://integrate.api.nvidia.com", "m", [{"role": "user", "content": "what colour"}],
         images=["QUJD"], api_key="k", surface="nim")

    body = json.loads(seen[0].data)
    content = body["messages"][-1]["content"]
    assert content[0]["text"] == "what colour"
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def test_chat_does_not_mutate_the_callers_messages(monkeypatch) -> None:
    capture(monkeypatch)
    messages = [{"role": "user", "content": "what colour"}]

    chat("https://integrate.api.nvidia.com", "m", messages, images=["QUJD"],
         api_key="k", surface="nim")

    assert messages == [{"role": "user", "content": "what colour"}]


def test_json_mode_sets_the_provider_response_format(monkeypatch) -> None:
    seen = capture(monkeypatch)

    chat("https://integrate.api.nvidia.com", "m", [{"role": "user", "content": "hi"}],
         api_key="k", surface="nim", json_mode=True)

    assert json.loads(seen[0].data)["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# Reply parsing and errors
# ---------------------------------------------------------------------------
def test_reasoning_models_fall_back_to_reasoning_content() -> None:
    assert _message_text({"content": None, "reasoning_content": "thought"}) == "thought"


def test_a_normal_reply_wins_over_reasoning() -> None:
    assert _message_text({"content": "answer", "reasoning_content": "thought"}) == "answer"


def test_a_401_names_the_missing_key_file(monkeypatch) -> None:
    error = urllib.error.HTTPError("https://x.test", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
    error.read = lambda: b'{"detail":"bad key"}'  # type: ignore[method-assign]
    capture(monkeypatch, error=error)

    with pytest.raises(RuntimeError, match="check the provider's API key"):
        chat("https://integrate.api.nvidia.com", "m", [{"role": "user", "content": "hi"}],
             api_key="k", surface="nim")


def test_a_404_is_not_retried(monkeypatch) -> None:
    error = urllib.error.HTTPError("https://x.test", 404, "Not Found", {}, None)  # type: ignore[arg-type]
    error.read = lambda: b""  # type: ignore[method-assign]
    seen = capture(monkeypatch, error=error)

    with pytest.raises(RuntimeError, match="404"):
        chat("https://integrate.api.nvidia.com", "m", [{"role": "user", "content": "hi"}],
             api_key="k", surface="nim")

    assert len(seen) == 1


def test_an_unreachable_provider_is_named_as_the_provider(monkeypatch) -> None:
    capture(monkeypatch, error=urllib.error.URLError("refused"))

    with pytest.raises(RuntimeError, match="the model provider"):
        chat("https://integrate.api.nvidia.com", "m", [{"role": "user", "content": "hi"}],
             api_key="k", surface="nim")


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,retry", [(429, True), (500, True), (503, True),
                                        (400, False), (401, False), (404, False)])
def test_retry_policy(code: int, retry: bool) -> None:
    assert _retryable(code) is retry


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------
def stub_providers(monkeypatch, *, key="k", host_response=None):
    """Point every provider at a fake host and give it (or not) a key."""
    monkeypatch.setattr(ollama_client.config, "PROVIDERS", {
        "nim": ("https://nim.test", key),
        "openrouter": ("https://or.test", key),
    })
    monkeypatch.setattr(ollama_client.config, "MODEL_PROVIDERS", {
        "nim/slug": "nim", "or/slug": "openrouter",
    })

    def fake(request, timeout=None):
        target = request.full_url if hasattr(request, "full_url") else request
        if host_response is not None:
            raise host_response
        raise urllib.error.HTTPError(str(target), 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", fake)


def test_a_bare_host_404_still_counts_as_reachable(monkeypatch) -> None:
    """HTTPError subclasses URLError — the 404 must not read as 'down'."""
    stub_providers(monkeypatch)

    ok, message = ollama_client.check_server("http://127.0.0.1:11434", ["nim/slug"])

    assert ok is True
    assert "unreachable" not in message


def test_a_refused_connection_reads_as_unreachable(monkeypatch) -> None:
    stub_providers(monkeypatch, host_response=urllib.error.URLError("refused"))

    ok, message = ollama_client.check_server("http://127.0.0.1:11434", ["nim/slug"])

    assert ok is False
    assert "unreachable" in message


def test_preflight_probes_the_gateway_too(monkeypatch) -> None:
    """The gateway is outside config.PROVIDERS and used to be skipped."""
    import preflight

    probed: list[str] = []
    monkeypatch.setattr(preflight, "detect_local_models", lambda: [])
    monkeypatch.setattr(preflight, "_reachable", lambda url: probed.append(url) or True)

    statuses = preflight.preflight({"seat": "gpt-6-luna-decisions"})

    assert preflight.config.EXPLABS_URL in probed
    assert statuses[0].status == "ready"


def test_a_missing_key_is_named_rather_than_probed(monkeypatch) -> None:
    stub_providers(monkeypatch, key="")

    ok, message = ollama_client.check_server("http://127.0.0.1:11434", ["nim/slug"])

    assert ok is False
    assert "NVIDIA_API_KEY" in message


# ---------------------------------------------------------------------------
# Vision goes through the same route
# ---------------------------------------------------------------------------
def test_vision_forwards_the_resolved_surface(monkeypatch) -> None:
    """vision must not pin itself to Ollama now that its model is hosted."""
    import vision

    calls: list[dict] = []
    monkeypatch.setattr(vision, "chat", lambda base, model, messages, **kw: calls.append(
        {"base": base, "surface": kw.get("surface"), "images": kw.get("images")}) or "ok")

    vision._ask("look", ["QUJD"], 16)

    assert calls[0]["surface"] != "local"
    assert calls[0]["images"] == ["QUJD"]


def test_a_rate_limit_waits_longer_than_a_server_error(monkeypatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr(ollama_client.time, "sleep", waits.append)

    _retry_delay(0, 500)
    _retry_delay(0, 429)

    assert waits[1] > waits[0]
