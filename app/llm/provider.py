"""
Multi-provider LLM abstraction.

Provider selection via LLM_PROVIDER env var (ollama | openrouter | langdock).
Ollama is the default to preserve backwards compatibility.

Fallback behaviour (when fallback_on_empty=True is passed to chat_json):
- LLM_PROVIDER=ollama:  try all OLLAMA_MODELS → if all fail/empty, retry via OpenRouter
- LLM_PROVIDER=openrouter: try all OPENROUTER_MODELS → if all fail, retry via Ollama
Both directions require the respective API key / host to be configured.
"""

import logging
import os

log = logging.getLogger(__name__)

# Load .env automatically so the correct provider is used when the worker or
# CLI is started directly (without going through app.web.main).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Common config -----------------------------------------------------------
_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
_MODEL_OVERRIDE = os.getenv("LLM_MODEL", "")

# --- Ollama config -----------------------------------------------------------
_OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")
# Comma-separated fallback list; defaults to OLLAMA_MODEL as single entry
_OLLAMA_MODELS: list[str] = [
    m.strip()
    for m in os.getenv("OLLAMA_MODELS", _OLLAMA_MODEL).split(",")
    if m.strip()
]

# --- OpenRouter config -------------------------------------------------------
_OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "")
_OPENROUTER_MODEL    = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
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


def chat_json(prompt: str, *, fallback_on_empty: bool = False) -> str:
    """
    Send prompt to the configured LLM provider and return the raw JSON string.

    If *fallback_on_empty* is True the call is retried via the other provider
    when all models of the primary provider fail or return empty JSON:
    - ollama → OpenRouter (requires OPENROUTER_API_KEY)
    - openrouter → Ollama (requires OLLAMA_HOST to be reachable)

    Raises RuntimeError on provider errors so callers can fall back gracefully.
    """
    if _PROVIDER == "ollama":
        try:
            result = _chat_ollama(prompt)
        except RuntimeError:
            if fallback_on_empty and _OPENROUTER_API_KEY:
                log.info("chat_json: All Ollama models failed, retrying with OpenRouter")
                return _chat_openrouter(prompt)
            raise
        if fallback_on_empty and _OPENROUTER_API_KEY and _is_empty_json(result):
            log.info("chat_json: Ollama returned empty result, retrying with OpenRouter")
            return _chat_openrouter(prompt)
        return result

    elif _PROVIDER == "openrouter":
        try:
            return _chat_openrouter(prompt)
        except RuntimeError:
            if fallback_on_empty:
                log.info("chat_json: All OpenRouter models exhausted, retrying with Ollama")
                return _chat_ollama(prompt)
            raise

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


def _is_empty_json(raw: str | None) -> bool:
    """Return True if *raw* is None, empty, or an empty JSON array/object."""
    if not raw:
        return True
    stripped = raw.strip()
    return stripped in ("[]", "{}", "[ ]", "{ }")


def _chat_openrouter(prompt: str) -> str:
    """Try each model in OPENROUTER_MODELS in order; raise if all fail."""
    from openai import BadRequestError, NotFoundError, RateLimitError

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
        except RateLimitError:
            log.warning("OpenRouter model %r rate-limited, trying next...", model)
            last_exc = RateLimitError  # type: ignore[assignment]
        except (NotFoundError, BadRequestError) as exc:
            log.warning("OpenRouter model %r not found / invalid, trying next...", model)
            last_exc = exc
        except RuntimeError as exc:
            log.warning("OpenRouter model %r returned empty/null, trying next...", model)
            last_exc = exc
    raise RuntimeError(f"All OpenRouter models exhausted: {models}") from last_exc


def _chat_ollama(prompt: str) -> str:
    """Try each model in OLLAMA_MODELS in order; raise if all fail."""
    import ollama  # optional import - only needed for ollama provider

    models = [_MODEL_OVERRIDE] if _MODEL_OVERRIDE else _OLLAMA_MODELS
    last_exc: Exception | None = None
    for model in models:
        try:
            log.debug("Ollama chat: model=%s host=%s", model, _OLLAMA_HOST)
            client   = ollama.Client(host=_OLLAMA_HOST)
            response = client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )
            content = response.message.content
            if content is None:
                raise RuntimeError(f"Ollama returned null content (model={model})")
            return content
        except RuntimeError as exc:
            log.warning("Ollama model %r failed, trying next...", model)
            last_exc = exc
    raise RuntimeError(f"All Ollama models exhausted: {models}") from last_exc


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
    if not completion.choices:
        raise RuntimeError(
            f"LLM returned empty/null choices (model={model}, base_url={base_url})"
        )
    content = completion.choices[0].message.content
    if content is None:
        raise RuntimeError(f"LLM returned null content (model={model}, base_url={base_url})")
    return content
