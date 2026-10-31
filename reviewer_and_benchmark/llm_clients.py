"""OpenAI-compatible reviewer model callbacks with token usage reporting."""
from __future__ import annotations

import os
from typing import Any

OPENAI_MODEL = "gpt-4o-mini"
OPENROUTER_MODEL = "openai/gpt-4o-mini"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _chat_fn(client: Any, model: str, max_tokens: int):
    def call(prompt: str) -> tuple[str, int, int]:
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = response.usage
        return (
            response.choices[0].message.content or "",
            int(usage.prompt_tokens),
            int(usage.completion_tokens),
        )
    return call


def openai_fn(model: str = OPENAI_MODEL, client: Any = None, max_tokens: int = 200):
    if client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required for the OpenAI reviewer.")
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _chat_fn(client, model, max_tokens)


def openrouter_fn(model: str = OPENROUTER_MODEL, client: Any = None, max_tokens: int = 200):
    if client is None:
        if not os.getenv("OPENROUTER_API_KEY"):
            raise ValueError("OPENROUTER_API_KEY is required for the OpenRouter reviewer.")
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url=OPENROUTER_BASE_URL)
    return _chat_fn(client, model, max_tokens)


def default_fn(model: str | None = None, max_tokens: int = 200):
    if os.getenv("OPENROUTER_API_KEY"):
        return openrouter_fn(model or OPENROUTER_MODEL, max_tokens=max_tokens)
    return openai_fn(model or OPENAI_MODEL, max_tokens=max_tokens)
