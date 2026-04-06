"""
Multi-provider LLM abstraction.

Provider selection via LLM_PROVIDER env var (ollama | openrouter | langdock).
Ollama is the default to preserve backwards compatibility.
"""

import logging
import os

log = logging.getLogger(__name__)

# --- Common config -----------------------------------------------------------
_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
_MODEL_OVERRIDE = os.getenv("LLM_MODEL", "")

# --- Ollama config -----------------------------------------------------------
_OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")

# --- OpenRouter config -------------------------------------------------------
_OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "")
_OPENROUTER_MODEL    = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-super-49b-v1:free")
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Comma-separated fallback list; defaults to OPENROUTER_MODEL as single entry
_OPENROUTER_MODELS: list[str] = [
    m.strip()
    for m in os.getenv("OPENROUTER_MODELS", _OPENROUTER_MODEL).split(",")
    if m.strip()
]

# --- LangDock config ---------------------------------------------------------
_LANGDOCK_API_KEY = os.getenv("LANGDOCK_API_KEY", "")
_LANGDOCK_API_URL = os.getenv("LANGDOCK_API_URL", "https://api.langdock.com/openai/v1")
_LANGDOCK_MODEL   = os.getenv("LANGDOCK_MODEL", "")


def chat_json(prompt: str) -> str:
    """
    Send prompt to the configured LLM provider and return the raw JSON string.

    Raises RuntimeError on provider errors so callers can fall back gracefully.
    """
    if _PROVIDER == "ollama":
        return _chat_ollama(prompt)
    elif _PROVIDER == "openrouter":
        from openai import RateLimitError

        models = [_MODEL_OVERRIDE] if _MODEL_OVERRIDE else _OPENROUTER_MODELS
        last_exc: Exception | None = None
        for model in models:
            try:
                return _chat_openai_compat(
                    prompt,
                    base_url=_OPENROUTER_BASE_URL,
                    api_key=_OPENROUTER_API_KEY,
                    model=model,
                    extra_headers={
                        "HTTP-Referer": "https://github.com/zeitungsarchiv",
                        "X-Title": "Zeitungsarchiv",
                    },
                )
            except RateLimitError as exc:
                log.warning("OpenRouter model %r rate-limited, trying next in list...", model)
                last_exc = exc
        raise RuntimeError(f"All OpenRouter models rate-limited: {models}") from last_exc
    elif _PROVIDER == "langdock":
        if not _LANGDOCK_MODEL and not _MODEL_OVERRIDE:
            raise RuntimeError("LANGDOCK_MODEL must be set when LLM_PROVIDER=langdock")
        return _chat_openai_compat(
            prompt,
            base_url=_LANGDOCK_API_URL,
            api_key=_LANGDOCK_API_KEY,
            model=_MODEL_OVERRIDE or _LANGDOCK_MODEL,
        )
    else:
        raise RuntimeError(f"Unknown LLM_PROVIDER: {_PROVIDER!r}. Use ollama, openrouter, or langdock.")


def _chat_ollama(prompt: str) -> str:
    """Send prompt via Ollama client, return raw content string."""
    import ollama  # optional import - only needed for ollama provider

    model = _MODEL_OVERRIDE or _OLLAMA_MODEL
    log.debug("Ollama chat: model=%s host=%s", model, _OLLAMA_HOST)
    client   = ollama.Client(host=_OLLAMA_HOST)
    response = client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format="json",
    )
    return response.message.content


def _chat_openai_compat(
    prompt: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    extra_headers: dict | None = None,
) -> str:
    """Send prompt via OpenAI-compatible API, return raw content string."""
    from openai import OpenAI  # optional import - only needed for non-ollama providers

    log.debug("OpenAI-compat chat: base_url=%s model=%s", base_url, model)
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        default_headers=extra_headers or {},
    )
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return completion.choices[0].message.content
