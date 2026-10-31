"""Derive bounded banking authorizations from user intent and visible evidence."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from approval_policy.evidence import ACCOUNT_RE, amount, decode, norm

NUMBER = r"\d[\d,]*(?:\.\d+)?"
CURRENCY = r"USD|EUR|GBP|SGD|dollars?|euros?|pounds?"
PAYMENT_RE = re.compile(
    rf"\b(?P<verb>send|transfer|pay|refund|wire|remit|reimburse|repay|make (?:a |the )?payment of)\s+"
    rf"(?P<currency>[$€£]|(?:{CURRENCY})\s+)?\s*(?P<amount>{NUMBER})"
    rf"(?:\s+(?P<currency_after>{CURRENCY}))?\s+(?:back\s+)?to\s+"
    rf"(?:the\s+)?(?:account\s+)?(?P<recipient>.+?)"
    rf"(?=\s+(?:on|for|as|and|then|with|using|please|thanks|thank|twice|two times|three times)\b|[.!?;,\n]|$)", re.I,
)
EXPLANATION = re.compile(r"\b(?:explain|example|hypothetical|the file says|quote|how (?:can|do|to))\b", re.I)
WRITE_INTENT = re.compile(r"\b(?:pay|send|transfer|refund|return|wire|remit|reimburse|repay|payment|adjust|update|change|set up|schedule|reset|follow)\b", re.I)


def _negative(prefix: str) -> bool:
    """Detect a local negation immediately governing a candidate operation."""
    return bool(re.search(r"(?:do not|don't|never|must not|not to)\s*(?:\w+\s+){0,2}$", prefix[-60:], re.I))


def _same(key: str, value: Any, expected: Any) -> bool:
    """Compare a proposed argument with one authorization constraint."""
    if key == "amount":
        return amount(value) == expected
    if key == "recurring":
        if isinstance(value, str):
            value = {"true": True, "false": False}.get(value.casefold(), value)
        return value is expected
    if isinstance(expected, tuple):
        return norm(value) in {norm(item) for item in expected}
    return norm(value) == norm(expected)


@dataclass
class Grant:
    """One bounded authorization for a specific operation/obligation."""

    tool: str
    values: dict[str, Any]
    source: str
    obligation: str
    partial: bool = False
    currency: str | None = None
    invoice_number: str | None = None

    def matches(self, tool: str, args: dict[str, Any]) -> bool:
        """Check tool and authorized arguments, including invoice identity."""
        if tool != self.tool:
            return False
        if self.invoice_number and tool == "send_money":
            mentioned = re.search(r"\b(?:invoice|inv\.?)\s*#?\s*([A-Z0-9][A-Z0-9/-]*)\b", str(args.get("subject", "")), re.I)
            if mentioned and any(character.isdigit() for character in mentioned[1]) and norm(mentioned[1]).lstrip("0") != norm(self.invoice_number).lstrip("0"):
                return False
        if self.partial:
            changes = {key: value for key, value in args.items() if key != "id" and value is not None}
            if not changes or any(key not in self.values or not _same(key, value, self.values[key]) for key, value in changes.items()):
                return False
            return "id" not in self.values or _same("id", args.get("id"), self.values["id"])
        return all(_same(key, args.get(key), value) for key, value in self.values.items())


@dataclass
class Plan:
    """All bounded grants, operation scopes, and unresolved evidence for a task."""

    grants: list[Grant] = field(default_factory=list)
    scopes: set[str] = field(default_factory=set)
    unresolved: list[str] = field(default_factory=list)


def direct_payments(task: str) -> list[Grant]:
    """Extract explicit, non-negated direct payment instructions from user text."""
    if EXPLANATION.search(task):
        return []
    grants = []
    for match in PAYMENT_RE.finditer(task):
        prefix = task[:match.start()]
        local = re.split(r"[.;\n]|\bbut\b", prefix, flags=re.I)[-1]
        if _negative(local) or re.search(r"\bif\b", local, re.I):
            continue
        recipient = re.sub(r"^[\"']|[\"']$", "", match["recipient"].strip())
        if not recipient or amount(match["amount"]) is None:
            continue
        values: dict[str, Any] = {"recipient": (recipient,), "amount": amount(match["amount"])}
        day = re.match(r"\s+on\s+(\d{4}-\d{2}-\d{2})\b", task[match.end():], re.I)
        if day:
            values["date"] = day[1]
        currency = (match["currency"] or match["currency_after"] or "").strip().upper() or None
        repetitions = re.match(r"\s+(twice|two times|three times)\b", task[match.end():], re.I)
        count = 3 if repetitions and repetitions[1].casefold() == "three times" else 2 if repetitions else 1
        for repetition in range(count):
            grants.append(Grant("send_money", dict(values), "user_task", f"explicit:{match.start()}:{repetition}", currency=currency))
    if not grants:
        other = re.search(rf"\bpay\s+([A-Za-z][A-Za-z0-9_.-]*)\s+([$€£])?({NUMBER})(?=\s|[.!?,;]|$)", task, re.I)
        if other and not _negative(task[:other.start()]) and not re.search(r"\bif\b", task[:other.start()], re.I):
            value = amount(other[3])
            if value:
                grants.append(Grant("send_money", {"recipient": (other[1],), "amount": value}, "user_task", "explicit:payee_first", currency=other[2]))
    return grants


def _invoice(text: str) -> tuple[str, Decimal] | None:
    """Extract a single payee and amount from structured or labeled invoice text."""
    data = decode(text)
    if isinstance(data, dict):
        payee = data.get("iban", data.get("recipient", data.get("pay_to")))
        totals = {amount(data[key]) for key in ("total", "amount_due", "amount") if key in data}
        totals.discard(None)
        if payee and len(totals) == 1:
            return str(payee), totals.pop()
    accounts = re.findall(rf"^\s*(?:IBAN|pay to(?: IBAN)?|recipient|bank account)\s*[:=]\s*({ACCOUNT_RE})\b", text, re.I | re.M)
    totals = re.findall(rf"^\s*(?:total(?: due)?|amount due|balance due)\s*[:=\t ]+[$€£]?\s*({NUMBER})\s*$", text, re.I | re.M)
    payees = {norm(account): account for account in accounts}
    values = {amount(value) for value in totals} - {None}
    if len(payees) != 1 or len(values) != 1:
        return None
    total = next(iter(values))
    subtotals = re.findall(rf"^\s*subtotal\s*[:=\t ]+[$€£]?\s*({NUMBER})\s*$", text, re.I | re.M)
    subtotal_values = {amount(value) for value in subtotals} - {None}
    if len(subtotal_values) > 1:
        return None
    subtotal = next(iter(subtotal_values)) if subtotal_values else None
    items = []
    for line in text.splitlines():
        item = re.match(rf"^\s*([A-Za-z][^\n]*?)\s{{2,}}[$€£]?({NUMBER})\s*$", line)
        if item and not re.match(r"(?:subtotal|total|amount|balance|tax|shipping|delivery|fee)\b", item[1], re.I):
            value = amount(item[2])
            if value:
                items.append(value)
    if items:
        expected_items_total = subtotal if subtotal is not None else total
        if sum(items) != expected_items_total:
            return None
    if subtotal is not None:
        tax_lines = re.findall(rf"^\s*(?:tax|sales tax|vat|gst)\s*[:=\t ]+[$€£]?\s*({NUMBER})\s*$", text, re.I | re.M)
        tax_values = {amount(value) for value in tax_lines} - {None}
        if len(tax_values) > 1:
            return None
        if tax_values and subtotal + next(iter(tax_values)) != total:
            return None
    return next(iter(payees.values()), ""), total


def _invoice_identity(text: str, path: str) -> tuple[str | None, str | None, str | None]:
    """Parse vendor and invoice ID, rejecting conflicts with explicit filename IDs."""
    data = decode(text)
    vendor, numbers = None, set()
    if isinstance(data, dict):
        vendor = data.get("vendor", data.get("supplier", data.get("vendor_name")))
        for key in ("invoice_id", "invoice_number"):
            if data.get(key) is not None:
                numbers.add(str(data[key]).strip())
    if vendor is None:
        names = {name.strip() for name in re.findall(r"^\s*Vendor\s*:\s*(.+?)\s*$", text, re.I | re.M)}
        if len(names) > 1:
            return None, None, "conflicting_invoice_vendor_names"
        vendor = next(iter(names)) if names else None
    numbers.update(re.findall(r"^\s*INVOICE\s*(?:#|No\.?\s*|Number\s*[:#]?)\s*([A-Z0-9][A-Z0-9/-]*)\s*$", text, re.I | re.M))
    filename = re.split(r"[/\\]", path)[-1]
    file_number = re.fullmatch(r"invoice[-_](\d+)(?:\.[A-Za-z0-9]+)?", filename, re.I)
    normalized = {norm(number).lstrip("0") for number in numbers}
    if len(normalized) > 1 or (file_number and normalized and normalized != {file_number[1].lstrip("0")}):
        return vendor, None, "invoice_number_conflicts_with_document_identity"
    number = next(iter(numbers)) if numbers else file_number[1] if file_number else None
    return str(vendor) if vendor is not None else None, number, None


def _address(text: str) -> dict[str, tuple[str, ...]] | None:
    """Extract a bounded street/city address, not adjacent injected actions."""
    data = decode(text)
    if isinstance(data, dict) and data.get("street") and data.get("city"):
        return {"street": (str(data["street"]),), "city": (str(data["city"]),)}
    text = re.sub(r"\s+(?:and update|and change|also[, ]).*", "", text, flags=re.I)
    lines = [value.strip() for value in text.splitlines() if value.strip()]
    if len(lines) == 1:
        lines = [value.strip() for value in text.split(",") if value.strip()]
    for index, line in enumerate(lines):
        if re.search(r"\b\d+\b", line) and re.search(r"\b(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|way|court|ct|place|pl)\b", line, re.I):
            if index + 1 < len(lines):
                city_line = lines[index + 1]
                city = city_line.split(",")[0].strip()
                if city and not re.search(r"\b(?:please|send|pay|password|ignore)\b", city_line, re.I):
                    return {"street": (line,), "city": tuple(dict.fromkeys([city, city_line, ", ".join(lines[index + 1:])]))}
    return None


def _rent_amount(task: str, documents: list[tuple[str, str]], old_amount: Decimal | None):
    """Calculate an explicitly requested rent amount or one supported by a named notice."""
    direct = re.search(rf"\brent\s+(?:is|will be|of|to|=|:)\s*[$€£]?({NUMBER})", task, re.I)
    if direct:
        return amount(direct[1]), "user_task"
    targets = []
    for path, text in documents:
        if not re.search(r"\brent\b", text, re.I):
            continue
        to = re.search(rf"\brent\b[\s\S]{{0,100}}?(?:increased|changed|set|reduced)\s+to\s*[$€£]?({NUMBER})", text, re.I)
        by = re.search(rf"\brent\b[\s\S]{{0,100}}?(increased|raised|decreased|reduced)\s+by\s*[$€£]?({NUMBER})(\s*%)?", text, re.I)
        if to:
            targets.append((amount(to[1]), path))
        elif by and old_amount:
            delta = Decimal(by[2].replace(",", ""))
            if by[3]:
                delta = old_amount * delta / 100
            if by[1].casefold() in {"decreased", "reduced"}:
                delta = -delta
            targets.append(((old_amount + delta).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), path))
    unique = {value for value, _ in targets if value and value > 0}
    return targets[0] if len(unique) == 1 else (None, "missing_or_conflicting_rent_notice")


def _merchant_choice(rows):
    """Select a merchant only if equally matched records do not conflict on payee."""
    if not rows:
        return None
    top = [entry for entry in rows if len(entry[1]) == len(rows[0][1])]
    if len({norm(row.get("recipient")) for row, _ in top}) != 1:
        return None
    return top[0]


def compile_plan(task: str, evidence, selected_invoice_path: str | None = None) -> Plan:
    """Compile user intent and visible evidence into bounded authorizations."""
    plan = Plan()
    text = task.replace("’", "'")
    if EXPLANATION.search(text):
        return plan
    if re.search(r"\b(?:do not|don't|never|must not)\s+(?:pay|send|transfer|refund|update|change|reset)\b", text, re.I) and not re.search(r"\b(?:but|instead)\b", text, re.I):
        return plan
    direct = direct_payments(text)
    plan.grants.extend(direct)
    if direct:
        plan.scopes.add("send_money")
    documents = evidence.named_documents(text)
    invoice_scope = bool(re.search(r"\b(?:pay|settle)\s+(?:(?:the|these|those|my|both|this|two)\s+)?(?:invoice|bill)s?\b", text, re.I))
    invoice_scope |= bool(re.search(r"\binvoice\b", text, re.I) and re.search(r"\bpay\s+(?:the\s+)?vendor\b", text, re.I))
    if invoice_scope:
        if not documents and selected_invoice_path:
            selected_document = evidence.documents.get(selected_invoice_path)
            if selected_document is not None:
                documents = [(selected_invoice_path, selected_document)]
        plan.scopes.add("send_money")
        for path, document in documents:
            invoice = _invoice(document)
            if invoice:
                payee, total = invoice
                vendor, number, identity_error = _invoice_identity(document, path)
                registry = [record for record in evidence.vendors if vendor and norm(record.get("name")) == norm(vendor)]
                account_records = [record for record in evidence.vendors if norm(record.get("iban")) == norm(payee)]
                if identity_error:
                    plan.unresolved.append(identity_error + ":" + path)
                elif registry and not any(norm(record.get("iban")) == norm(payee) and amount(record.get("outstanding")) == total for record in registry):
                    plan.unresolved.append("invoice_conflicts_with_visible_vendor_registry:" + path)
                elif vendor and not registry and (account_records or norm(vendor) in evidence.vendor_queries):
                    plan.unresolved.append("invoice_vendor_not_verified_by_visible_registry:" + path)
                else:
                    plan.grants.append(Grant("send_money", {"recipient": (payee,), "amount": total}, path, "invoice:" + path, invoice_number=number))
            else:
                plan.unresolved.append("invoice_missing_or_conflicting_fields:" + path)
        if not documents:
            plan.unresolved.append("invoice_not_identified_or_not_read")

    refund_scope = bool(re.search(r"\b(?:refund|reimburse|repay)\b|\bsend\b.*\bback\b|\breturn\b.*\b(?:sent|received|money)\b", text, re.I))
    if refund_scope and (not direct or re.search(r"\brefund that\b|what they|sent me|send them back", text, re.I)):
        plan.scopes.add("send_money")
        refund_clause = re.search(r"\b(?:refund|reimburse|repay)\b([^.;]*)", text, re.I)
        target_text = refund_clause[0] if refund_clause else text
        targets = {norm(value) for value in re.findall(ACCOUNT_RE, target_text, re.I)}
        incoming = [row for row in evidence.incoming() if not targets or norm(row.get("sender")) in targets]
        explicit_refund = re.search(rf"\brefund\s+(?:that\s+)?[$€£]?({NUMBER})\b", text, re.I)
        if explicit_refund:
            incoming = [row for row in incoming if amount(row.get("amount")) == amount(explicit_refund[1])]
        if incoming:
            incoming.sort(key=lambda row: (str(row.get("date", "")), str(row.get("id", ""))), reverse=True)
            candidates = [incoming[0]] if targets else incoming
            if not targets and len({norm(row.get("sender")) for row in candidates}) != 1:
                plan.unresolved.append("refund_sender_ambiguous")
            else:
                row = candidates[0]
                value = amount(row.get("amount"))
                total = re.search(rf"\b(?:we\s+spent|total bill(?:\s+was)?|spent)\s*[$€£]?({NUMBER})(?:\s+in total)?", text, re.I)
                if total and re.search(r"\b(?:difference|too much|share)\b", text, re.I):
                    people = re.search(r"\b(?:between|among)\s+(\d+)\b", text, re.I)
                    count = int(people[1]) if people else 2
                    value = (value - Decimal(total[1].replace(",", "")) / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if value and count > 0 else None
                if value and value > 0:
                    plan.grants.append(Grant("send_money", {"recipient": (str(row["sender"]),), "amount": value}, "transaction:" + str(row.get("id")), "refund:" + str(row.get("id"))))
                else:
                    plan.unresolved.append("no_positive_refund_due")
        else:
            plan.unresolved.append("refund_requires_incoming_transaction")

    percentage = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if percentage and re.search(r"\b(?:difference|additional|too little|VAT|increase)\b", text, re.I):
        plan.scopes.add("send_money")
        chosen = _merchant_choice(evidence.merchant_records(text))
        if chosen:
            row, matched_names = chosen
            value = amount(row.get("amount"))
            fee = re.search(rf"\bfee\s+(?:of\s+)?[$€£]?({NUMBER})", text, re.I)
            if value:
                value = (value * Decimal(percentage[1]) / 100 + (Decimal(fee[1].replace(",", "")) if fee else 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                topic = re.match(r"\s*([\w &.-]+?)\s+(?:called|sent me|notified|informed)\b", text, re.I)
                aliases = [str(row["recipient"])]
                if topic and set(re.findall(r"\w+", norm(topic[1]))) & set(matched_names):
                    aliases.append(topic[1].strip())
                plan.grants.append(Grant("send_money", {"recipient": tuple(aliases), "amount": value}, "transaction:" + str(row.get("id")), "supplement:" + str(row.get("id"))))
        else:
            plan.unresolved.append("supplement_requires_matching_merchant_transaction")

    schedule_scope = bool(re.search(r"\b(?:set up|create|schedule)\b.*\b(?:recurring|scheduled|standing|transaction)\b", text, re.I))
    if schedule_scope:
        plan.scopes.add("schedule_transaction")
        recipient = re.search(rf"\brecipient\s+(?:is|:|=)\s*({ACCOUNT_RE})", text, re.I)
        chosen = _merchant_choice(evidence.merchant_records(text, recurring_only=True))
        if recipient and chosen:
            row = chosen[0]
            value = amount(row.get("amount"))
            if value:
                plan.grants.append(Grant("schedule_transaction", {"recipient": (recipient[1],), "amount": value, "recurring": bool(re.search(r"\brecurring\b", text, re.I))}, "user_task+transaction:" + str(row.get("id")), "subscription:" + recipient[1]))
        else:
            plan.unresolved.append("schedule_requires_payee_and_reference_amount")

    rent_scope = bool(re.search(r"\brent\b|\bstanding orders?\b", text, re.I))
    rent_scope |= bool(re.search(r"\bfollow\b.*\binstructions\b", text, re.I) and any(re.search(r"\brent\b", document, re.I) for _, document in documents))
    if rent_scope and WRITE_INTENT.search(text):
        plan.scopes.add("update_scheduled_transaction")
        orders = [row for row in evidence.orders if re.search(r"\brent\b", str(row.get("subject", "")), re.I)]
        if len(orders) == 1:
            order = orders[0]
            target, source = _rent_amount(text, documents, amount(order.get("amount")))
            recipient = re.search(rf"\b(?:landlord'?s?\s+)?account\s+(?:is|:|=)\s*({ACCOUNT_RE})", text, re.I)
            if target:
                values = {key: order[key] for key in ("recipient", "subject", "date", "recurring") if key in order}
                values.update(id=str(order["id"]), amount=target)
                if recipient:
                    values["recipient"] = recipient[1]
                plan.grants.append(Grant("update_scheduled_transaction", values, source + "+standing_order", "rent:" + str(order["id"]), partial=True))
            else:
                plan.unresolved.append(source)
        else:
            plan.unresolved.append("rent_order_missing_or_ambiguous")

    address_scope = bool(re.search(r"\b(?:update|change)\b.*\b(?:address|account information|records)\b", text, re.I))
    if address_scope:
        plan.scopes.add("update_user_info")
        literal = re.search(r"\bnew address\s*(?::|is)\s*(.+)", text, re.I | re.S)
        sources = [("user_task", literal[1])] if literal else documents
        for source, document in sources:
            fields = _address(document)
            if fields:
                plan.grants.append(Grant("update_user_info", fields, source, "address:" + source, partial=True))
        if not any(grant.tool == "update_user_info" for grant in plan.grants):
            plan.unresolved.append("address_evidence_missing")

    password_scope = bool(re.search(r"\b(?:update|change|reset|set)\b.*\bpassword\b", text, re.I))
    if password_scope:
        plan.scopes.add("update_password")
        password = re.search(r"\bpassword\s+(?:to|is|as|:|=)\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s.,;]+))", text, re.I)
        check_required = bool(re.search(r"\b(?:check|history|transactions)\b", text, re.I))
        conditional = bool(re.search(r"\bif\b", text, re.I))
        delegated = bool(re.search(r"\bsuspici?ous\b|\bsuspicous\b", text, re.I))
        if password and (not check_required or "get_most_recent_transactions" in evidence.seen_tools) and (not conditional or delegated):
            value = next(group for group in password.groups() if group is not None)
            plan.grants.append(Grant("update_password", {"password": value}, "user_task", "password_change"))
        else:
            plan.unresolved.append("password_condition_or_value_unconfirmed")
    return plan
