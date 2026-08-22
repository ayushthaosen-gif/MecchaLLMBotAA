"""
llm_backend.py
---------------
Swappable "brain" for brain.py. Two options:

  - OllamaBackend (default): free, runs entirely on the Pi via Ollama.
    No API key, no per-message cost, no internet required for the chat
    itself. Lower quality than Claude, and slower on bigger models.

  - ClaudeBackend: paid, calls the Anthropic API. Much stronger replies,
    costs money per token, needs internet.

Select with the LLM_BACKEND env var ("ollama" or "claude") in brain.py.

--- Setting up Ollama on the Pi (one-time) ---
    curl -fsSL https://ollama.com/install.sh | sh
    ollama pull gemma3:1b        # fast, ~18-22 tok/s on a Pi 5 8GB
    # or: ollama pull qwen2.5:3b   # slower (~4-7 tok/s), noticeably smarter
    ollama serve                 # runs the local API on port 11434

Set OLLAMA_MODEL to whichever tag you pulled.
"""

import os
from abc import ABC, abstractmethod

import requests


class LLMBackend(ABC):
    @abstractmethod
    def chat(self, system_prompt: str, user_message: str) -> str:
        """Return the assistant's reply text."""
        raise NotImplementedError


class OllamaBackend(LLMBackend):
    """Free, local, no API key. Requires `ollama serve` running on the Pi."""

    def __init__(self):
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.model = os.environ.get("OLLAMA_MODEL", "gemma3:1b")
        self.timeout = float(os.environ.get("OLLAMA_TIMEOUT", "30"))

    def chat(self, system_prompt: str, user_message: str) -> str:
        resp = requests.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "").strip()


class ClaudeBackend(LLMBackend):
    """Paid, calls the Anthropic API. Import is local so `anthropic` is only
    a hard dependency if you actually choose this backend."""

    def __init__(self):
        import anthropic  # noqa: local import, see docstring above

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("Set ANTHROPIC_API_KEY to use LLM_BACKEND=claude")

        # Haiku 4.5 by default — fast + cheap. Bump to "claude-sonnet-5" for
        # more depth.
        self.model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
        self.client = anthropic.Anthropic(api_key=api_key)

    def chat(self, system_prompt: str, user_message: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()


def build_llm_backend(name: str) -> LLMBackend:
    name = (name or "ollama").lower()
    if name == "ollama":
        return OllamaBackend()
    if name == "claude":
        return ClaudeBackend()
    raise ValueError(f"Unknown LLM_BACKEND '{name}' — use 'ollama' or 'claude'")
