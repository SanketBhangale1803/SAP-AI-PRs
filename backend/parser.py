"""Natural-language purchase request parser for SAP requisition intake."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any


SERVICE_NAME = "sap-requisition-nl-parser"
SERVICE_VERSION = "2.0.0"


def extract_quantity(message: str) -> int:
    lower = message.lower()
    patterns = [
        r"\b(?:need|order|buy|purchase|for)\s+(\d{1,4})\b",
        r"\b(\d{1,4})\s+(?:laptops?|monitors?|licenses?|seats?|chairs?|gloves?|hard hats?|items?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            return int(match.group(1))
    return 1


def extract_items(message: str) -> list[dict[str, Any]]:
    matches = re.finditer(
        r"\b(\d{1,4})\s+([A-Za-z][A-Za-z0-9 /&-]{2,48}?)(?=,|\band\b|\bfor\b|\bneeded\b|\.|$)",
        message,
        re.IGNORECASE,
    )
    items: list[dict[str, Any]] = []
    for match in matches:
        description = re.sub(r"\s+", " ", match.group(2)).strip(" .,-")
        description = re.sub(r"\b(?:for|needed|need|by)$", "", description, flags=re.IGNORECASE).strip()
        if description and not description.lower().startswith(("pr ", "purchase requisition")):
            items.append({"description": description.capitalize(), "quantity": int(match.group(1))})

    if items:
        return items

    return [{"description": extract_title(message), "quantity": extract_quantity(message)}]


def extract_budget(message: str) -> int:
    normalized = message.replace(",", "")
    match = re.search(r"\$ ?(\d{2,7})", normalized)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(?:budget|spend|cost)\D{0,12}(\d{2,7})\b", normalized, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def extract_cost_center(message: str) -> str:
    match = re.search(r"\b(?:cost center|cc)\D{0,8}(\d{3,8})\b", message, re.IGNORECASE)
    return match.group(1) if match else "MISSING"


def extract_plant(message: str) -> str:
    match = re.search(r"\b(?:plant|warehouse)\D{0,8}(\d{3,6})\b", message, re.IGNORECASE)
    if match:
        return match.group(1)
    if re.search(r"\b(chicago|ord)\b", message, re.IGNORECASE):
        return "1100"
    if re.search(r"\b(dallas|dfw)\b", message, re.IGNORECASE):
        return "1200"
    return "1000"


def extract_delivery_date(message: str) -> str:
    today = date.today()
    lower = message.lower()
    if "next friday" in lower:
        days_ahead = (4 - today.weekday()) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return str(today + timedelta(days=days_ahead))

    date_match = re.search(
        r"\b(?:by|needed by|need by|delivery by)?\s*"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})\b",
        lower,
    )
    if date_match:
        month_name, day = date_match.groups()
        month = datetime.strptime(month_name[:3], "%b").month
        year = today.year if month >= today.month else today.year + 1
        return str(date(year, month, int(day)))

    return str(today + timedelta(days=14))


def extract_title(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", message.strip())
    title = re.sub(
        r"^(?:please\s+)?(?:create|make|open|submit)?\s*(?:a\s+)?(?:pr|purchase requisition)?\s*(?:for)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    title = re.split(r",|\.|\bneeded\b|\bneed by\b", title, maxsplit=1, flags=re.IGNORECASE)[0]
    return title[:80].strip().capitalize() or "Indirect purchase request"


def parse_request(message: str) -> dict[str, Any]:
    items = extract_items(message)
    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "title": extract_title(message),
        "items": items,
        "quantity": items[0]["quantity"] if items else 1,
        "budget": extract_budget(message),
        "cost_center": extract_cost_center(message),
        "plant": extract_plant(message),
        "delivery_date": extract_delivery_date(message),
    }
