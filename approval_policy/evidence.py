"""Extract evidence from visible successful tool results, never hidden env state."""
from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

READ_ONLY = frozenset({
    "get_iban", "get_balance", "get_most_recent_transactions",
    "get_scheduled_transactions", "read_file", "get_user_info", "get_vendor",
})
WRITES = frozenset({
    "send_money", "schedule_transaction", "update_scheduled_transaction",
    "update_user_info", "update_password",
})
ACCOUNT_RE = r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}"


def amount(value: Any) -> Decimal | None:
    """Normalize valid positive currency values to cents, allowing float noise."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
        rounded = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not number.is_finite() or number <= 0 or abs(number - rounded) > Decimal("0.00000001"):
            return None
        return rounded
    except (InvalidOperation, ValueError):
        return None


def norm(value: Any) -> str:
    """Normalize identifiers/text for conservative matching."""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def call_parts(call: Any) -> tuple[str, dict[str, Any]]:
    """Read function and arguments from a mapping or AgentDojo call object."""
    if isinstance(call, dict):
        return call.get("function", call.get("tool", "")), dict(call.get("args") or {})
    return getattr(call, "function", ""), dict(getattr(call, "args", {}) or {})


def text_content(message: dict[str, Any]) -> str:
    """Extract visible text blocks from an AgentDojo tool message."""
    content = message.get("content") or []
    if isinstance(content, str):
        return content
    return "\n".join(
        str(block.get("content", block.get("text", "")) or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def decode(text: str) -> Any:
    """Decode JSON or YAML tool output; malformed content is not evidence."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        try:
            import yaml
            return yaml.safe_load(text)
        except ImportError:
            return None
        except Exception:
            return None


def event_key(event: dict[str, Any]) -> tuple[str, str]:
    """Create a stable key used to deduplicate execution log/message evidence."""
    return event.get("tool", ""), json.dumps(event.get("args", {}), sort_keys=True, default=str)


class Evidence:
    """Parsed facts from decision-visible tool outputs and execution history."""

    def __init__(self, context: Any):
        self.transactions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.documents: dict[str, str] = {}
        self.vendors: list[dict[str, Any]] = []
        self.user_info: dict[str, Any] = {}
        self.own_account: str | None = None
        self.vendor_queries: set[str] = set()
        self.seen_tools: set[str] = set()
        self.parse_errors: list[str] = []
        observed_events = []
        seen_transactions: set[str] = set()
        seen_orders: set[str] = set()

        for message in getattr(context, "messages", ()):
            if not isinstance(message, dict) or message.get("role") != "tool":
                continue
            tool, args = call_parts(message.get("tool_call"))
            if tool == "get_vendor":
                self.vendor_queries.add(norm(args.get("name")))
            if message.get("error"):
                continue
            raw = text_content(message)
            self.seen_tools.add(tool)
            if tool == "read_file":
                self.documents.setdefault(str(args.get("file_path", "")), raw)
                continue
            data = decode(raw)
            if tool in {"get_most_recent_transactions", "get_scheduled_transactions"}:
                if not isinstance(data, list):
                    self.parse_errors.append(tool)
                    continue
                target, seen = (
                    (self.transactions, seen_transactions)
                    if tool == "get_most_recent_transactions"
                    else (self.orders, seen_orders)
                )
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    key = str(row.get("id")) if row.get("id") is not None else json.dumps(row, sort_keys=True, default=str)
                    if key not in seen:
                        target.append(dict(row))
                        seen.add(key)
            elif tool == "get_iban":
                self.own_account = str(data if isinstance(data, str) else raw).strip()
            elif tool == "get_user_info" and isinstance(data, dict):
                self.user_info.update(data)
            elif tool == "get_vendor" and isinstance(data, dict) and not data.get("error"):
                self.vendors.append(dict(data))
            if tool in WRITES:
                observed_events.append({
                    "tool": tool,
                    "args": args,
                    "invoked": True,
                    "call_id": message.get("tool_call_id"),
                    "source": "successful_tool_message",
                })

        supplied = [
            dict(record)
            for record in getattr(context, "executed_calls", ())
            if record.get("invoked", record.get("executed", False))
            and record.get("execution_status") != "not_started"
            and not str(record.get("tool_error") or "").startswith(("ValidationError:", "ToolNotFoundError:"))
        ]
        self.executed = list(supplied)
        counts = Counter(event_key(record) for record in supplied)
        for event in observed_events:
            key = event_key(event)
            if counts[key]:
                counts[key] -= 1
            else:
                self.executed.append(event)

        if not self.own_account and self.transactions:
            endpoints = [
                {norm(row.get("sender")), norm(row.get("recipient"))} - {""}
                for row in self.transactions
            ]
            common = set.intersection(*endpoints) if endpoints else set()
            if len(common) == 1:
                self.own_account = next(iter(common))

    def incoming(self) -> list[dict[str, Any]]:
        """Return visible transactions received by the user's account."""
        own = norm(self.own_account)
        return [row for row in self.transactions if norm(row.get("recipient")) in ({"me", "self", own} - {""})]

    def outgoing(self) -> list[dict[str, Any]]:
        """Return visible transactions sent from the user's account."""
        own = norm(self.own_account)
        return [row for row in self.transactions if norm(row.get("sender")) in ({"me", "self", own} - {""})]

    def named_documents(self, task: str) -> list[tuple[str, str]]:
        """Return only files explicitly named by the user, not injected paths."""
        return [
            (path, text)
            for path, text in self.documents.items()
            if path and re.search(r"(?<![\w./\\-])" + re.escape(path) + r"(?![\w./\\-])", task, re.I)
        ]

    def merchant_records(self, task: str, recurring_only: bool = False) -> list[tuple[dict[str, Any], list[str]]]:
        """Match user-mentioned merchant words against visible transaction subjects."""
        words = set(re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", task.casefold()))
        stop = {
            "the", "for", "payment", "transaction", "history", "amount", "send", "them", "please",
            "this", "month", "paid", "pay", "difference", "from", "their", "that", "with", "total",
            "account", "what", "they", "have", "bill", "transfer", "recurring", "scheduled", "usually",
        }
        words -= stop
        rows = []
        for row in self.outgoing():
            if recurring_only and not row.get("recurring"):
                continue
            recipient = norm(row.get("recipient"))
            description_words = set(re.findall(r"[a-z][a-z0-9-]{2,}", norm(row.get("subject"))))
            matched = sorted(words & description_words)
            if recipient in words or matched:
                rows.append((row, matched))
        month_names = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        selected_months = {index for index, name in enumerate(month_names) if index and re.search(r"\b" + name + r"\b", task, re.I)}
        if selected_months:
            rows = [
                (row, names)
                for row, names in rows
                if str(row.get("date", ""))[5:7].isdigit() and int(str(row["date"])[5:7]) in selected_months
            ]
        return sorted(rows, key=lambda item: (len(item[1]), str(item[0].get("date", "")), str(item[0].get("id", ""))), reverse=True)
