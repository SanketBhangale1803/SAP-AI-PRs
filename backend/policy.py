"""Procurement policy engine for SAP requisition drafts.

Enforces cost-center budget caps, spend thresholds, duplicate-risk checks,
and catalog compliance before a draft can be submitted to SAP Build.
"""

from __future__ import annotations

from typing import Any


SERVICE_NAME = "sap-requisition-policy-engine"
SERVICE_VERSION = "2.0.0"

# Cost center annual soft caps used for local policy gating (demo master data).
COST_CENTER_BUDGET_CAPS: dict[str, int] = {
    "4100": 25000,
    "7780": 15000,
    "1200": 40000,
    "1000": 50000,
}

HIGH_SPEND_THRESHOLD = 5000
CRITICAL_SPEND_THRESHOLD = 10000
DUPLICATE_LOOKBACK = 25

CATEGORY_RULES = [
    {
        "keywords": ["laptop", "monitor", "keyboard", "mouse", "iphone", "ipad", "server", "dock"],
        "material_group": "IT-HARDWARE",
        "gl_account": "641200",
        "vendor": "Approved IT Catalog",
        "buyer": "IT Procurement",
    },
    {
        "keywords": ["training", "license", "software", "subscription", "aws", "course"],
        "material_group": "SERVICES-SOFTWARE",
        "gl_account": "653100",
        "vendor": "Preferred Services Vendor",
        "buyer": "Services Procurement",
    },
    {
        "keywords": ["glove", "hard hat", "safety", "ppe", "warehouse", "forklift"],
        "material_group": "MRO-SAFETY",
        "gl_account": "622500",
        "vendor": "Grainger Catalog",
        "buyer": "MRO Buyer",
    },
    {
        "keywords": ["chair", "desk", "paper", "toner", "office", "notebook"],
        "material_group": "OFFICE-SUPPLIES",
        "gl_account": "621000",
        "vendor": "Office Depot Catalog",
        "buyer": "Indirect Buyer",
    },
]


def infer_category(message: str) -> dict[str, str]:
    lower = message.lower()
    for rule in CATEGORY_RULES:
        if any(keyword in lower for keyword in rule["keywords"]):
            return rule
    return {
        "material_group": "INDIRECT-GOODS",
        "gl_account": "629000",
        "vendor": "Buyer review required",
        "buyer": "Shared Procurement Desk",
    }


def evaluate_risk(
    message: str,
    budget: int,
    cost_center: str,
    vendor: str,
    *,
    duplicate_hits: list[dict[str, Any]] | None = None,
) -> tuple[str, list[str]]:
    flags: list[str] = []
    lower = message.lower()
    duplicate_hits = duplicate_hits or []

    if cost_center == "MISSING":
        flags.append("Cost center missing; route back to requester before SAP submission.")
    if vendor == "Buyer review required":
        flags.append("No preferred source found; buyer should confirm vendor or catalog item.")
    if budget >= HIGH_SPEND_THRESHOLD:
        flags.append(f"Spend exceeds ${HIGH_SPEND_THRESHOLD:,}; manager and finance approval required.")
    if any(term in lower for term in ["urgent", "asap", "rush"]):
        flags.append("Urgent request; capture business justification and delivery constraints.")
    if any(term in lower for term in ["laptop", "monitor", "iphone"]):
        flags.append("Check duplicate open PRs and asset standards before ordering.")
    if duplicate_hits:
        ids = ", ".join(hit.get("requisition_id", "?") for hit in duplicate_hits[:3])
        flags.append(f"Possible duplicate of recent draft(s): {ids}.")

    cap = COST_CENTER_BUDGET_CAPS.get(cost_center)
    if cap is not None and budget > cap:
        flags.append(
            f"Request ${budget:,} exceeds cost center {cost_center} soft cap ${cap:,}; finance override required."
        )

    if not flags:
        flags.append("Ready for guided submission; no major policy gaps detected.")

    if len(flags) >= 3 or budget >= CRITICAL_SPEND_THRESHOLD:
        level = "High"
    elif len(flags) == 2 or budget >= HIGH_SPEND_THRESHOLD or cost_center == "MISSING" or duplicate_hits:
        level = "Medium"
    else:
        level = "Low"
    return level, flags


def approval_route(
    budget: int,
    category: dict[str, str],
    cost_center: str,
    requester: dict[str, Any] | None = None,
    *,
    over_budget_cap: bool = False,
) -> list[str]:
    manager_name = str((requester or {}).get("manager_name", "")).strip()
    route = ["Requester", "Cost center owner" if cost_center != "MISSING" else "Requester correction"]
    if manager_name:
        route.append(f"Manager approval ({manager_name})")
    else:
        route.append("Manager approval")
    if budget >= HIGH_SPEND_THRESHOLD or over_budget_cap:
        route.append("Finance")
    if over_budget_cap:
        route.append("Procurement director override")
    route.append(category["buyer"])
    route.append("SAP PR created")
    return route


def draft_submission_blockers(
    cost_center: str,
    budget: int = 0,
    *,
    duplicate_hits: list[dict[str, Any]] | None = None,
) -> list[str]:
    blockers: list[str] = []
    if cost_center == "MISSING":
        blockers.append("Cost center missing; route back to requester before SAP submission.")
    cap = COST_CENTER_BUDGET_CAPS.get(cost_center)
    if cap is not None and budget > cap:
        blockers.append(
            f"Cost center {cost_center} soft cap ${cap:,} exceeded; obtain finance override before SAP submission."
        )
    if duplicate_hits:
        blockers.append("Possible duplicate requisition detected; confirm uniqueness before SAP submission.")
    return blockers


def payload_submission_blockers(payload: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    required_fields = ["quantity", "required_date", "tool_name"]
    for field in required_fields:
        value = payload.get(field)
        if value is None or value == "":
            blockers.append(f"{field} is required before SAP submission.")

    cost_center = str(payload.get("cost_center", "")).strip()
    if not cost_center or cost_center == "MISSING":
        blockers.append("Cost center missing; route back to requester before SAP submission.")
    return blockers


def policy_catalog() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "high_spend_threshold": HIGH_SPEND_THRESHOLD,
        "critical_spend_threshold": CRITICAL_SPEND_THRESHOLD,
        "cost_center_budget_caps": COST_CENTER_BUDGET_CAPS,
        "category_rules": [
            {
                "material_group": rule["material_group"],
                "gl_account": rule["gl_account"],
                "vendor": rule["vendor"],
                "buyer": rule["buyer"],
                "keywords": rule["keywords"],
            }
            for rule in CATEGORY_RULES
        ],
        "gates": [
            "cost_center_required",
            "budget_cap_check",
            "duplicate_detection",
            "catalog_compliance",
            "approval_routing",
        ],
    }
