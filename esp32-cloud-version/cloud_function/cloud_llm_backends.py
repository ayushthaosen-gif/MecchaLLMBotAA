"""
cloud_llm_backends.py
------------------------
Pluggable "which model actually answers" layer for the cloud function.
Select with the LLM_PROVIDER env var: "gemini" (default here), "claude",
or "stub" (for local testing with no API key).

This is the cloud-side equivalent of llm_backend.py from the Pi project
— same idea (swap the model without touching brain/gesture logic),
different place in the architecture, since here the LLM call happens in
the cloud function, not on the device.
"""

import os
from abc import ABC, abstractmethod

import requests


class LLMBackend(ABC):
    @abstractmethod
    def chat(self, system_prompt: str, user_message: str) -> str:
        raise NotImplementedError


class GeminiBackend(LLMBackend):
    """Calls Google's Gemini API. Requires GEMINI_API_KEY."""

    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set GEMINI_API_KEY to use LLM_PROVIDER=gemini")
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    def chat(self, system_prompt: str, user_message: str) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {"maxOutputTokens": 200},
        }
        resp = requests.post(url, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates returned")
            raise RuntimeError(f"Gemini returned no usable reply ({reason})")
        parts = candidates[0].get("content", {}).get("parts") or []
        if not parts:
            finish_reason = candidates[0].get("finishReason", "unknown")
            raise RuntimeError(f"Gemini returned an empty reply (finishReason={finish_reason})")
        return parts[0].get("text", "").strip()


class ClaudeBackend(LLMBackend):
    """Calls the Anthropic API. Requires ANTHROPIC_API_KEY."""

    def __init__(self):
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("Set ANTHROPIC_API_KEY to use LLM_PROVIDER=claude")
        self.model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
        self.client = anthropic.Anthropic(api_key=api_key)

    def chat(self, system_prompt: str, user_message: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()


def build_llm_backend(name: str) -> LLMBackend:
    name = (name or "gemini").lower()
    if name == "gemini":
        return GeminiBackend()
    if name == "claude":
        return ClaudeBackend()
    raise ValueError(f"Unknown LLM_PROVIDER '{name}' — use 'gemini' or 'claude'")
