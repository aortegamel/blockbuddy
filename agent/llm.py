"""BlockBuddy brain — OpenAI-compatible chat completions.

Default points at a local opencode server. Env (.env supported):
  BLOCKBUDDY_BASE_URL default http://localhost:4096/v1
  BLOCKBUDDY_API_KEY default empty
  BLOCKBUDDY_MODEL default opencode-default
  BLOCKBUDDY_FALLBACK_CHAIN optional comma-separated extra models
"""
import json
import os
import urllib.request
from pathlib import Path


def _load_dotenv():
    # minimal .env loader (no dependency): KEY=VALUE, skips blanks/#, strips quotes
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

DEFAULT_BASE_URL = "http://localhost:4096/v1"
DEFAULT_MODEL = "opencode-default"


def _base_url():
    return os.environ.get("BLOCKBUDDY_BASE_URL", DEFAULT_BASE_URL).rstrip("/") or DEFAULT_BASE_URL


def _model():
    return os.environ.get("BLOCKBUDDY_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def _api_key():
    return os.environ.get("BLOCKBUDDY_API_KEY", "")


def _fallback_models():
    raw = os.environ.get("BLOCKBUDDY_FALLBACK_CHAIN", "")
    return [m for m in (x.strip() for x in raw.split(",")) if m]


def _endpoint():
    base = _base_url()
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def provider():
    """Primary model name (used in log lines)."""
    return _model()


def _chat(model, system, prompt, max_tokens, *, history=None, temperature=None):
    messages = [{"role": "system", "content": system}]
    for entry in history or ():
        if (isinstance(entry, dict)
                and entry.get("role") in ("user", "assistant")
                and isinstance(entry.get("content"), str)):
            messages.append({"role": entry["role"], "content": entry["content"]})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(_endpoint(), data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def complete(system, prompt, max_tokens=4000, chain=None, *, history=None, temperature=None):
    """Return a response, trying each model after a failure.

    The chain overrides configured models. History accepts user and assistant
    messages with string content. Malformed entries do not enter the request.
    """
    models = [m for m in (chain or []) if m] or [_model()] + _fallback_models()
    last_error = None
    for m in models:
        try:
            return _chat(m, system, prompt, max_tokens, history=history, temperature=temperature)
        except Exception as exc:
            last_error = exc
            if len(models) > 1:
                print(f"[llm] {m} failed ({str(exc)[:120]}) — trying next model")
    if last_error is None:  # unreachable: models always holds at least the default
        raise RuntimeError("no models configured")
    raise last_error
