"""Token and estimated API-cost accounting by project component."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any


class _MeteredCompletions:
    def __init__(self, completions: Any, meter: "UsageMeter", component: str):
        self._completions = completions
        self._meter = meter
        self._component = component

    def create(self, *args: Any, **kwargs: Any) -> Any:
        response = self._completions.create(*args, **kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens = getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", None))
            output_tokens = getattr(usage, "completion_tokens", getattr(usage, "output_tokens", None))
            if input_tokens is not None and output_tokens is not None:
                self._meter.record(self._component, int(input_tokens), int(output_tokens))
        return response


class _MeteredChat:
    def __init__(self, chat: Any, meter: "UsageMeter", component: str):
        self.completions = type("CompletionsProxy", (), {})()
        self.completions.create = _MeteredCompletions(chat.completions, meter, component).create


class MeteredOpenAIClient:
    """Transparent client proxy that meters chat completion response usage."""

    def __init__(self, client: Any, meter: "UsageMeter", component: str):
        self._client = client
        self.chat = _MeteredChat(client.chat, meter, component)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class UsageMeter:
    """Aggregate token counts and optional estimated dollar costs."""

    def __init__(self, prices_per_million: dict[str, dict[str, float]] | None = None):
        self.prices = prices_per_million or {}
        self.usage: dict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        )

    def record(self, component: str, input_tokens: int, output_tokens: int, calls: int = 1) -> None:
        """Add token usage for `agent`, `policy`, or `reviewer`."""
        if min(input_tokens, output_tokens, calls) < 0:
            raise ValueError("Usage values cannot be negative.")
        item = self.usage[component]
        item["calls"] += calls
        item["input_tokens"] += input_tokens
        item["output_tokens"] += output_tokens

    def wrap(self, component: str, fn: Callable[..., tuple[Any, int, int]]) -> Callable[..., Any]:
        """Wrap a callback returning `(result, input_tokens, output_tokens)`."""
        def measured(*args: Any, **kwargs: Any) -> Any:
            result, input_tokens, output_tokens = fn(*args, **kwargs)
            self.record(component, int(input_tokens), int(output_tokens))
            return result
        return measured

    def wrap_openai_client(self, client: Any, component: str) -> MeteredOpenAIClient:
        """Wrap an OpenAI-compatible client to collect chat completion token usage."""
        return MeteredOpenAIClient(client, self, component)

    def report(self) -> dict[str, dict[str, int | float]]:
        """Return token totals and costs where component pricing was provided."""
        report: dict[str, dict[str, int | float]] = {}
        for component, usage in self.usage.items():
            prices = self.prices.get(component, {})
            item: dict[str, int | float] = dict(usage)
            if "input" in prices and "output" in prices:
                cost = (
                    usage["input_tokens"] * prices["input"]
                    + usage["output_tokens"] * prices["output"]
                ) / 1_000_000
                item["estimated_cost_usd"] = round(cost, 8)
            report[component] = item
        return report
