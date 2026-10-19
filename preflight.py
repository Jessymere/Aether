"""
Preflight model checks for Aether.

Check which models are available locally and on the gateway,
and suggest a fallback configuration for offline use.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Tuple

import config
import ollama_client


@dataclasses.dataclass
class ModelStatus:
    role: str
    model: str
    surface: str  # "local", "gateway", "nim", "openrouter"
    installed: bool  # for local: whether model is installed in Ollama
    key_present: bool  # for hosted: whether that provider's key is set
    status: str  # "ready", "local-missing", "no-key", "provider-unreachable"
    hint: str  # actionable hint


#: Surface name -> the .env variable holding its key.
_KEY_NAMES = {
    "gateway": "EXPLABS_API_KEY",
    "nim": "NVIDIA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _key_name(surface: str) -> str:
    return _KEY_NAMES.get(surface, surface.upper() + "_API_KEY")


def detect_local_models() -> List[str]:
    """Return list of model names installed in local Ollama.
    Returns empty list if Ollama is down or unreachable.
    """
    try:
        with urllib.request.urlopen(f"{config.OLLAMA_URL}/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode())
        # Extract model names; each item has a "name" field like "model:tag" or just "model"
        installed = set()
        for item in payload.get("models", []):
            name = item.get("name", "")
            if name:
                installed.add(name)
        return list(installed)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
        # Ollama is unreachable or returned invalid data
        return []


def _reachable(base_url: str) -> bool:
    """Cheap reachability check for a provider host.

    Any HTTP response proves the host answered — including 4xx, since a bare
    root path genuinely 404s (and ``HTTPError`` subclasses ``URLError``, so it
    must be caught first or a 404 reads as an outage). A bare timeout also
    counts as reachable to avoid false negatives.
    """
    try:
        with urllib.request.urlopen(base_url, timeout=5):
            return True
    except urllib.error.HTTPError:
        # Host answered; the root path just isn't a real endpoint.
        return True
    except urllib.error.URLError as e:
        reason = str(e.reason).lower()
        return "timed out" in reason or "timeout" in reason
    except Exception:
        return False


def preflight(models: Optional[Dict[str, str]] = None) -> List[ModelStatus]:
    """Return a list of ModelStatus for each role.
    If models is None, uses config.MODELS.
    """
    if models is None:
        models = config.MODELS

    # Get installed local models once, and probe each provider at most once.
    # The key is read per provider rather than copied from PROVIDERS, because
    # the gateway is not in that map and would otherwise look key-less and
    # never be probed — reporting every gateway role unreachable regardless.
    installed_local = set(detect_local_models())
    bases = {provider: url for provider, (url, _) in config.PROVIDERS.items()}
    bases["gateway"] = config.EXPLABS_URL
    reachable: Dict[str, bool] = {
        provider: _reachable(url) for provider, url in bases.items()
        if config.provider_key(provider)
    }

    statuses: List[ModelStatus] = []
    for role, model_slug in models.items():
        base_url, api_key, surface = config.model_route(model_slug)
        if surface == "local":
            installed = model_slug in installed_local or any(
                name.startswith(model_slug + ":") for name in installed_local
            )
            status = "ready" if installed else "local-missing"
            hint = ""
            if not installed:
                hint = f"Run 'ollama pull {model_slug}' to install locally."
            statuses.append(ModelStatus(
                role=role,
                model=model_slug,
                surface=surface,
                installed=installed,
                key_present=False,  # not applicable
                status=status,
                hint=hint,
            ))
        else:  # hosted provider
            key_present = bool(api_key)
            if not key_present:
                status = "no-key"
                hint = f"set {_key_name(surface)} in the project .env file"
            elif not reachable.get(surface):
                status = "provider-unreachable"
                hint = f"Check the network connection to {surface}."
            else:
                status = "ready"
                hint = ""
            statuses.append(ModelStatus(
                role=role,
                model=model_slug,
                surface=surface,
                installed=False,  # not applicable
                key_present=key_present,
                status=status,
                hint=hint,
            ))
    return statuses


def suggest_config() -> Dict[str, str]:
    """Return a dictionary mapping roles to available local models.
    Prefers the role's own FAST_MODELS entry if installed, else any installed model.
    Returns empty dict if no local models are available.
    """
    installed_local = list(detect_local_models())
    if not installed_local:
        return {}

    result: Dict[str, str] = {}
    #: The catch-all for a seat whose own model is a cloud slug and no local
    #: copy exists: the smallest installed model, since this path is offline.
    any_local = sorted(installed_local, key=len)[0]
    for role, model_slug in config.MODELS.items():
        # Check if the model_slug is installed (exact or with tag)
        if model_slug in installed_local or any(
            name.startswith(model_slug + ":") for name in installed_local
        ):
            result[role] = model_slug
            continue
        # If not, check the FAST_MODELS alternative for this role
        fast_model = config.FAST_MODELS.get(role)
        if fast_model and (
            fast_model in installed_local or any(
                name.startswith(fast_model + ":") for name in installed_local
            )
        ):
            result[role] = fast_model
            continue
        # Otherwise fall back to some installed local model: a worse answer
        # beats no answer, and --auto-models only runs when the key is absent.
        result[role] = any_local
    return result


def format_report(statuses: List[ModelStatus]) -> str:
    """Format a compact aligned text table for the terminal."""
    lines = []
    # Header
    lines.append(f"{'Role':<18} {'Model':<30} {'Surface':<10} {'Status':<20} {'Hint'}")
    lines.append("-" * 90)
    for s in statuses:
        hint = s.hint if s.hint else ""
        lines.append(
            f"{s.role:<18} {s.model:<30} {s.surface:<10} {s.status:<20} {hint}"
        )
    return "\n".join(lines)


# Optional: a main function for command-line testing
if __name__ == "__main__":
    statuses = preflight()
    print(format_report(statuses))
    print("\nSuggested offline config:")
    print(suggest_config())