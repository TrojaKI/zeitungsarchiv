"""Tests for the LLM provider fallback chain.

Regression tests for the bug where _chat_ollama only caught RuntimeError:
ollama raises ResponseError/httpx.ConnectError when the host is down or the
model is missing, so neither the model fallback nor the cross-provider
fallback ever triggered in the most common failure case.
"""

import sys
import types

import pytest

from app.llm import provider


def _fake_ollama_module(exception: Exception) -> types.ModuleType:
    """Build a fake ollama module whose Client.chat always raises."""
    module = types.ModuleType("ollama")

    class FakeClient:
        def __init__(self, host: str | None = None) -> None:
            pass

        def chat(self, **kwargs):
            raise exception

    module.Client = FakeClient  # type: ignore[attr-defined]
    return module


class TestOllamaFallback:

    @pytest.mark.parametrize("exception", [
        ConnectionError("connection refused"),
        OSError("network unreachable"),
        Exception("ollama.ResponseError: model not found"),
    ])
    def test_non_runtime_errors_raise_runtime_error(self, monkeypatch, exception):
        """Any per-model failure must surface as RuntimeError so chat_json can fall back."""
        monkeypatch.setitem(sys.modules, "ollama", _fake_ollama_module(exception))

        with pytest.raises(RuntimeError, match="All Ollama models exhausted"):
            provider._chat_ollama("prompt")

    def test_chat_json_falls_back_to_openrouter_on_connection_error(self, monkeypatch):
        """Ollama down (ConnectionError) + fallback_on_empty → OpenRouter is used."""
        monkeypatch.setitem(
            sys.modules, "ollama", _fake_ollama_module(ConnectionError("refused"))
        )
        monkeypatch.setattr(provider, "_PROVIDER", "ollama")
        monkeypatch.setattr(provider, "_OPENROUTER_API_KEY", "test-key")
        monkeypatch.setattr(provider, "_chat_openrouter", lambda prompt: '{"ok": true}')

        assert provider.chat_json("prompt", fallback_on_empty=True) == '{"ok": true}'

    def test_chat_json_raises_without_fallback(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "ollama", _fake_ollama_module(ConnectionError("refused"))
        )
        monkeypatch.setattr(provider, "_PROVIDER", "ollama")

        with pytest.raises(RuntimeError):
            provider.chat_json("prompt", fallback_on_empty=False)
