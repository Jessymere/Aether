import json
import http.client
import time
import urllib.request
import urllib.error


def check_server(base_url: str, models: list[str]) -> tuple[bool, str]:
    """Check Ollama availability and report which configured models are missing."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode())
        installed = {item.get("name", "") for item in payload.get("models", [])}
        missing = [model for model in models if model not in installed and not any(name.startswith(model + ":") for name in installed)]
        if missing:
            return False, "Ollama is running, but these models are missing: " + ", ".join(missing)
        return True, "Ollama is ready: " + ", ".join(models)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        return False, f"Ollama is unavailable at {base_url}. Start Ollama first. ({exc})"


def _build_request_body(model: str, messages: list[dict], stream: bool, num_predict: int, json_mode: bool, api_key: str | None) -> tuple[dict, dict]:
    """Build request body and headers for either Ollama or OpenAI-compatible API."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if api_key:
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


def chat(base_url: str, model: str, messages: list[dict], timeout: int = 600, json_mode: bool = False, num_predict: int = 2048, images: list[str] | None = None, api_key: str | None = None) -> str:
    prepared_messages = [dict(message) for message in messages]
    if images:
        prepared_messages[-1]["images"] = images

    body, headers = _build_request_body(model, prepared_messages, False, num_predict, json_mode, api_key)

    if api_key:
        path = "/v1/chat/completions"
    else:
        path = "/api/chat"

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
            if api_key:
                return body_resp["choices"][0]["message"]["content"]
            return body_resp["message"]["content"]
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 or attempt == 2:
                detail = exc.read().decode(errors="replace")
                raise RuntimeError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
            time.sleep(2 ** attempt)
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "Could not reach Ollama. Start Ollama and verify that it is listening on "
                f"{base_url}. Details: {exc}"
            ) from exc
        except (http.client.RemoteDisconnected, ConnectionResetError, TimeoutError) as exc:
            last_error = exc
            if attempt == 2:
                raise RuntimeError(
                    "Ollama closed the connection while running the model. "
                    "Try setting Ollama Context length to 8k or 4k, then retry. "
                    f"Details: {exc}"
                ) from exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Ollama request failed after retries: {last_error}")


def stream_chat(base_url: str, model: str, messages: list[dict], timeout: int = 600, num_predict: int = 2048, api_key: str | None = None):
    """Yield content chunks from a streaming chat response."""
    prepared_messages = [dict(message) for message in messages]

    body, headers = _build_request_body(model, prepared_messages, True, num_predict, False, api_key)

    if api_key:
        path = "/v1/chat/completions"
    else:
        path = "/api/chat"

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
                    if api_key:
                        content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    else:
                        content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 or attempt == 2:
                detail = exc.read().decode(errors="replace")
                raise RuntimeError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
            time.sleep(2 ** attempt)
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "Could not reach Ollama. Start Ollama and verify that it is listening on "
                f"{base_url}. Details: {exc}"
            ) from exc
        except (http.client.RemoteDisconnected, ConnectionResetError, TimeoutError) as exc:
            last_error = exc
            if attempt == 2:
                raise RuntimeError(
                    "Ollama closed the connection while running the model. "
                    "Try setting Ollama Context length to 8k or 4k, then retry. "
                    f"Details: {exc}"
                ) from exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Ollama stream failed after retries: {last_error}")
