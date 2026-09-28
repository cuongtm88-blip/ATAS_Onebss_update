from __future__ import annotations

import re
import unicodedata


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def unaccent(value: object) -> str:
    text = unicodedata.normalize("NFD", normalize(value))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d")


def ticket_identity(transaction_id: object, subscriber_id: object) -> str:
    """Canonical identity shared by OneBSS and Google Sheet lookups."""
    subscriber = str(subscriber_id or "").strip()
    if subscriber.startswith("'"):
        subscriber = subscriber[1:]
    return f"{normalize(transaction_id)}|{normalize(subscriber)}"


def assignment_cohort_key(
    customer_name: object, labor_province: object, service: object,
) -> str:
    """Return a stable key for tickets that must stay with one employee."""
    customer = normalize(customer_name)
    province = normalize(labor_province)
    service_name = normalize(service)
    if not customer or not province or not service_name:
        return ""
    return "\x1f".join((customer, province, service_name))
