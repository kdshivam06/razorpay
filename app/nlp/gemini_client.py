"""Small Gemini JSON helper used behind deterministic NLP guardrails."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from app.config import get_settings


class JsonLlmClient(Protocol):
    """Minimal protocol for mocked or real structured LLM extraction."""

    def generate_json(self, prompt: str) -> dict[str, Any]:
        """Return a parsed JSON object for a prompt."""


class GeminiJsonClient:
    """Google Gemini wrapper that returns only parsed JSON objects.

    This helper reads GEMINI_API_KEY through app.config and deliberately exposes
    no execution capability; callers may only consume structured labels.
    """

    def __init__(self, model_name: str = "gemini-1.5-flash") -> None:
        settings = get_settings()
        import google.generativeai as genai

        genai.configure(api_key=settings.GEMINI_API_KEY)
        self._model = genai.GenerativeModel(model_name)

    def generate_json(self, prompt: str) -> dict[str, Any]:
        response = self._model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        text = getattr(response, "text", "") or ""
        return parse_json_object(text)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse strict JSON or the first JSON object embedded in model text."""

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def try_generate_json(client: JsonLlmClient | None, prompt: str) -> dict[str, Any]:
    """Call a structured LLM client, treating failures as no-signal."""

    if client is None:
        return {}
    try:
        return client.generate_json(prompt)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {}


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Bound probability-like values."""

    return max(lower, min(upper, value))


def normalize(text: str) -> str:
    """Loose normalization for Hinglish/English rule snippets."""

    return text.casefold().replace("-", " ").replace("_", " ")
