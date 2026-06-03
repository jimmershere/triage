"""LLM transport abstraction for swarms.

A :class:`LlmClient` is anything with a ``generate(prompt, **options) -> str``
method. The bundled :class:`OllamaClient` speaks the Ollama HTTP API; the
:class:`MockLlmClient` is for tests and deterministic local development.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class LlmClient(Protocol):
    """Protocol every swarm-compatible LLM client must satisfy."""

    def generate(self, prompt: str, **options: Any) -> str: ...


class OllamaClient:
    """Stream-disabled Ollama generate client.

    Endpoint defaults to local Ollama but is overridable per instance.
    """

    def __init__(
        self,
        *,
        url: str = "http://localhost:11434",
        model: str = "qwen2.5-coder:7b",
        timeout: float = 90.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, **options: Any) -> str:
        payload = {
            "model": options.pop("model", self.model),
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": options.pop("temperature", 0.1),
                "num_predict": options.pop("num_predict", 512),
                **options,
            },
        }
        req = urllib.request.Request(
            f"{self.url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("response", "")
        except urllib.error.URLError as exc:
            raise OllamaTransportError(str(exc)) from exc


class OllamaTransportError(RuntimeError):
    """Raised when an Ollama HTTP call cannot complete."""


class MockLlmClient:
    """Deterministic LLM client for tests.

    Initialize with either a dict that maps an agent name (or any substring of
    the prompt) to a canned response, or a callable that receives the prompt
    and returns the response.
    """

    def __init__(
        self,
        responses: dict[str, str] | Callable[[str], str] | None = None,
        *,
        default: str = "",
    ) -> None:
        self.responses = responses or {}
        self.default = default
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, **options: Any) -> str:
        self.calls.append({"prompt": prompt, "options": options})
        if callable(self.responses):
            return self.responses(prompt)
        for key, response in self.responses.items():
            if key and key in prompt:
                return response
        return self.default
