import json
import http.client
import time
import urllib.request
import urllib.error
import socket
from typing import Optional

import config

#: Provider name -> the .env variable that holds its key, for error messages.
PROVIDER_KEY_NAMES = {
    "gateway": "EXPLABS_API_KEY",
    "nim": "NVIDIA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def check_server(base_url: str, models: Optional[list[str]] = None) -> tuple[bool, str]:
    """Check Ollama and Experiential Labs gateway availability and report which configured models are missing.

    Args:
        base_url: The Ollama base URL (e.g., "http://127.0.0.1:11434").
        models: List of model slugs to check. If None, checks all models in config.MODELS.

    Returns:
        (ok, message): ok=True if all specified models are available (locally installed or gateway configured),
        ok=False otherwise with an actionable message.
    """
    if models is None:
        models = list(config.MODELS.values())

    # Split models by surface. Anything a provider map does not own is local.
    local_models = []
    gateway_models = []
    for model in models:
        _, _, surface = config.model_route(model)
        if surface == "local" or not config.provider_for(model):
            local_models.append(model)
        else:
            gateway_models.append(model)

    # Check local models via Ollama /api/tags
    missing_local = []
    if local_models:
        try:
            with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode())
            installed = {item.get("name", "") for item in payload.get("models", [])}
            for model in local_models:
                if model not in installed and not any(name.startswith(model + ":") for name in installed):
                    missing_local.append(model)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
            # If Ollama is unreachable, treat all local models as missing
            missing_local = local_models[:]

    # Check hosted models, grouped by provider so one answer covers them all
    cloud: dict[str, list[str]] = {}
    for model in gateway_models:
        cloud.setdefault(config.provider_for(model), []).append(model)

    def reachable(base_url: str) -> bool:
        # Any response, even 4xx/5xx, means the host is there; only a refused
        # or unresolvable connection counts as down. A timeout is treated as
        # up, because a slow free tier is not a wrong key.
        try:
            urllib.request.urlopen(base_url, timeout=2).close()
            return True
        except urllib.error.HTTPError:
            # A bare host usually 404s. HTTPError subclasses URLError, so this
            # has to come first or every live provider reads as unreachable.
            return True
        except urllib.error.URLError as exc:
            return "timeout" in str(getattr(exc, "reason", exc)).lower()
        except Exception:
            return False

    no_key: list[str] = []
    unreachable: list[str] = []
    for provider, models_of_provider in cloud.items():
        if not models_of_provider:
            continue
        if not config.provider_key(provider):
            no_key.extend(models_of_provider)
            continue
        base_url = (config.EXPLABS_URL if provider == "gateway"
                    else config.PROVIDERS[provider][0])
        if not reachable(base_url):
            unreachable.extend(models_of_provider)

    # Determine overall status and message
    if missing_local:
        return False, f"Ollama is running, but these models are missing: {', '.join(missing_local)}"
    if no_key:
        names = sorted({PROVIDER_KEY_NAMES[p] for p in cloud
                        if cloud[p] and not config.provider_key(p)})
        return False, f"set {', '.join(names)} in the project .env file"
    if unreachable:
        names = sorted({p for p in cloud if any(m in unreachable for m in cloud[p])})
        return False, f"model provider host unreachable: {', '.join(names)}"

    # All good
    parts = []
    if local_models:
        parts.append(f"Local models ready ({len(local_models) - len(missing_local)}/{len(local_models)})")
    if gateway_models:
        parts.append(f"Hosted models ready ({len(gateway_models) - len(no_key) - len(unreachable)}/{len(gateway_models)})")
    return True, ", ".join(parts)


def _uses_openai_format(base_url: str, api_key: str | None, surface: str | None) -> bool:
    """Return True when the OpenAI-compatible wire format must be used.

    Every cloud surface speaks OpenAI; only ``local`` is Ollama. An explicit
    ``surface`` wins. Otherwise fall back to a key or a non-loopback URL —
    never the key alone, which used to send Ollama-format JSON to the gateway.
    """
    if surface is not None:
        return surface != "local"
    if api_key:
        return True
    return not base_url.startswith(("http://127.0.0.1", "http://localhost"))


def _http_error_message(base_url: str, code: int, detail: str, openai: bool) -> str:
    """Build an actionable error for a non-2xx response."""
    service = "The model provider" if openai else "Ollama"
    message = f"{service} returned HTTP {code} at {base_url}: {detail}"
    if code in {401, 403} and openai:
        message += (
            "  Hint: the provider rejected this request. Verify the API key in "
            "the project .env file is current and has access to this model. "
            "A configured key can still be invalid or lack model permission."
        )
    return message


def _retryable(code: int) -> bool:
    """5xx and 429 retry; 429 is the free tiers' normal answer to a burst."""
    return code >= 500 or code == 429


def _retry_delay(attempt: int, code: int = 0) -> None:
    # Rate limits clear on their own schedule, so they get a longer wait than
    # a server hiccup.
    time.sleep(min(2 ** attempt * (2 if code == 429 else 1), 20))


def _message_text(message: dict) -> str:
    """The reply text, tolerating reasoning models that answer elsewhere.

    NIM's reasoning models stream their thinking into ``reasoning_content``
    and leave ``content`` null — especially at a low ``max_tokens``, where the
    budget is spent before any visible answer is produced.
    """
    content = message.get("content")
    if content is None:
        content = message.get("reasoning_content") or ""
    return content


def _build_request_body(model: str, messages: list[dict], stream: bool, num_predict: int, json_mode: bool, api_key: str | None, openai: bool) -> tuple[dict, dict]:
    """Build request body and headers for either Ollama or the OpenAI-compatible API."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if openai:
        # OpenAI-compatible format (Experiential Labs gateway)
        body = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "max_tokens": num_predict,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
    else:
        # Ollama format
        body = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {"num_ctx": 8192, "num_predict": num_predict},
        }
        if json_mode:
            body["format"] = "json"

    return body, headers


def _attach_images(message: dict, images: list[str], openai: bool) -> dict:
    """Attach base64 images to the last message in the surface's own dialect.

    Ollama takes a bare ``images`` list; OpenAI-compatible providers want
    content parts with a data URI. Sending ``images`` to a cloud model is
    ignored silently, so the answer comes back describing nothing.
    """
    if not openai:
        message["images"] = images
        return message
    parts = [{"type": "text", "text": message.get("content") or ""}]
    parts.extend({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{data}"},
    } for data in images)
    message["content"] = parts
    return message


def chat(base_url: str, model: str, messages: list[dict], timeout: int = 600, json_mode: bool = False, num_predict: int = 2048, images: list[str] | None = None, api_key: str | None = None, surface: str | None = None) -> str:
    prepared_messages = [dict(message) for message in messages]
    openai = _uses_openai_format(base_url, api_key, surface)
    if images:
        prepared_messages[-1] = _attach_images(prepared_messages[-1], images, openai)
    body, headers = _build_request_body(model, prepared_messages, False, num_predict, json_mode, api_key, openai)

    path = "/v1/chat/completions" if openai else "/api/chat"

    payload = json.dumps(body).encode()
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=payload,
        headers=headers,
        method="POST",
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body_resp = json.loads(response.read().decode())
            if openai:
                return _message_text(body_resp["choices"][0].get("message") or {})
            return body_resp["message"]["content"]
        except urllib.error.HTTPError as exc:
            last_error = exc
            if not _retryable(exc.code) or attempt == 2:
                detail = exc.read().decode(errors="replace")
                raise RuntimeError(_http_error_message(base_url, exc.code, detail, openai)) from exc
            _retry_delay(attempt, exc.code)
        except urllib.error.URLError as exc:
            where = "the model provider" if openai else "Ollama"
            raise RuntimeError(
                f"Could not reach {where} at {base_url}. Details: {exc}"
            ) from exc
        except (http.client.RemoteDisconnected, ConnectionResetError, TimeoutError) as exc:
            last_error = exc
            if attempt == 2:
                if openai:
                    raise RuntimeError(
                        f"The model provider dropped the request. Details: {exc}"
                    ) from exc
                raise RuntimeError(
                    "Ollama closed the connection while running the model. "
                    "Try setting Ollama Context length to 8k or 4k, then retry. "
                    f"Details: {exc}"
                ) from exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Request failed after retries: {last_error}")


def stream_chat(base_url: str, model: str, messages: list[dict], timeout: int = 600, num_predict: int = 2048, api_key: str | None = None, surface: str | None = None):
    """Yield content chunks from a streaming chat response."""
    prepared_messages = [dict(message) for message in messages]

    openai = _uses_openai_format(base_url, api_key, surface)
    body, headers = _build_request_body(model, prepared_messages, True, num_predict, False, api_key, openai)

    path = "/v1/chat/completions" if openai else "/api/chat"

    payload = json.dumps(body).encode()
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=payload,
        headers=headers,
        method="POST",
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line == "[DONE]":
                        return
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if openai:
                        content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    else:
                        content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
        except urllib.error.HTTPError as exc:
            last_error = exc
            if not _retryable(exc.code) or attempt == 2:
                detail = exc.read().decode(errors="replace")
                raise RuntimeError(_http_error_message(base_url, exc.code, detail, openai)) from exc
            _retry_delay(attempt, exc.code)
        except urllib.error.URLError as exc:
            where = "the model provider" if openai else "Ollama"
            raise RuntimeError(
                f"Could not reach {where} at {base_url}. Details: {exc}"
            ) from exc
        except (http.client.RemoteDisconnected, ConnectionResetError, TimeoutError) as exc:
            last_error = exc
            if attempt == 2:
                if openai:
                    raise RuntimeError(
                        f"The model provider closed the stream. Details: {exc}"
                    ) from exc
                raise RuntimeError(
                    "Ollama closed the connection while running the model. "
                    "Try setting Ollama Context length to 8k or 4k, then retry. "
                    f"Details: {exc}"
                ) from exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Stream failed after retries: {last_error}")
