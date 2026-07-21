"""SAP Requisition Command Center API (v2).

Architecture (Runbook service graph):
  - sap-requisition-gateway      HTTP API in this module
  - sap-requisition-nl-parser    Natural-language intake parsing
  - sap-requisition-policy-engine Cost-center caps, risk, approvals
  - sap-requisition-ledger       Draft history, status, duplicates
  - sap-requisition-sap-client   SAP Build / S/4HANA submission
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

# Allow `python3 backend/server.py` from the repo root.
sys.path.insert(0, os.path.dirname(__file__))

from parser import parse_request
from policy import (
    COST_CENTER_BUDGET_CAPS,
    approval_route,
    draft_submission_blockers,
    evaluate_risk,
    infer_category,
    payload_submission_blockers,
    policy_catalog,
)
from sap_client import merge_sap_config, sap_config_status, sap_submit, sap_test_connection
from store import REQUISITION_STORE


HOST = "127.0.0.1"
PORT = 8000
SERVICE_NAME = "sap-requisition-gateway"
SERVICE_VERSION = "2.0.0"

PUBLIC_REQUESTER_FIELDS = {
    "employee_id",
    "name",
    "email",
    "department",
    "business_unit",
    "manager_name",
    "manager_email",
}


def load_dotenv() -> None:
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_dotenv()


def parse_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(encoded)


def compact_money(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def normalize_requester(requester: dict[str, Any] | None) -> dict[str, str]:
    requester = requester or {}
    return {field: str(requester.get(field, "")).strip() for field in PUBLIC_REQUESTER_FIELDS}


def build_line_items(
    items: list[dict[str, Any]], category: dict[str, str], unit_price: float
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        enriched.append(
            {
                "item_number": f"{(index + 1) * 10:05d}",
                "description": item["description"],
                "quantity": item["quantity"],
                "material_group": category["material_group"],
                "unit_price": unit_price,
                "gl_account": category["gl_account"],
                "unit_of_measure": "EA",
            }
        )
    return enriched


def build_sap_payload(
    *,
    req_id: str,
    enriched_items: list[dict[str, Any]],
    cost_center: str,
    plant: str,
    delivery: str,
    category: dict[str, str],
    unit_price: float,
    requester_profile: dict[str, str],
) -> dict[str, Any]:
    first_item = enriched_items[0]
    payload: dict[str, Any] = {
        "account_assignment_category": "K",
        "cost_center": cost_center,
        "document_type": "NB",
        "estimated_net_price": compact_money(unit_price),
        "gl_account": category["gl_account"],
        "item_number": first_item["item_number"],
        "material_group": category["material_group"],
        "plant": plant,
        "purchase_requisition": req_id,
        "quantity": first_item["quantity"],
        "required_date": delivery,
        "requester_business_unit": requester_profile["business_unit"],
        "requester_department": requester_profile["department"],
        "requester_email": requester_profile["email"],
        "requester_employee_id": requester_profile["employee_id"],
        "requester_name": requester_profile["name"],
        "approver_email": requester_profile["manager_email"],
        "approver_name": requester_profile["manager_name"],
        "short_text": first_item["description"],
        "tool_name": first_item["description"],
        "unit_of_measure": "EA",
        "line_item_count": len(enriched_items),
    }
    # Multi-line support for SAP Build scripts that iterate $.context.startEvent.line_items
    if len(enriched_items) > 1:
        payload["line_items"] = [
            {
                "item_number": row["item_number"],
                "tool_name": row["description"],
                "short_text": row["description"],
                "quantity": row["quantity"],
                "estimated_net_price": compact_money(row["unit_price"]),
                "material_group": row["material_group"],
                "gl_account": row["gl_account"],
                "unit_of_measure": row["unit_of_measure"],
            }
            for row in enriched_items
        ]
    return payload


def build_draft(message: str, requester: dict[str, Any] | None = None) -> dict[str, Any]:
    requester_profile = normalize_requester(requester)
    parsed = parse_request(message)
    category = infer_category(message)
    items = parsed["items"]
    budget = parsed["budget"]
    cost_center = parsed["cost_center"]
    plant = parsed["plant"]
    delivery = parsed["delivery_date"]

    title = " + ".join(item["description"] for item in items[:3])
    if len(items) > 3:
        title = f"{title} + {len(items) - 3} more"

    duplicate_hits = REQUISITION_STORE.find_duplicates(
        cost_center=cost_center,
        title=title,
        requester_employee_id=requester_profile["employee_id"],
    )
    level, flags = evaluate_risk(
        message,
        budget,
        cost_center,
        category["vendor"],
        duplicate_hits=duplicate_hits,
    )
    over_budget_cap = bool(
        cost_center in COST_CENTER_BUDGET_CAPS and budget > COST_CENTER_BUDGET_CAPS[cost_center]
    )
    submission_blockers = draft_submission_blockers(
        cost_center, budget, duplicate_hits=duplicate_hits
    )
    req_id = f"PR-{uuid.uuid4().hex[:8].upper()}"
    unit_price = round(budget / sum(item["quantity"] for item in items), 2) if budget else 0
    enriched_items = build_line_items(items, category, unit_price)
    payload = build_sap_payload(
        req_id=req_id,
        enriched_items=enriched_items,
        cost_center=cost_center,
        plant=plant,
        delivery=delivery,
        category=category,
        unit_price=unit_price,
        requester_profile=requester_profile,
    )

    draft = {
        "requisition_id": req_id,
        "service_version": SERVICE_VERSION,
        "title": title,
        "item": {
            "description": enriched_items[0]["description"],
            "quantity": enriched_items[0]["quantity"],
            "material_group": category["material_group"],
            "unit_price": unit_price,
        },
        "items": enriched_items,
        "total_estimated_value": budget,
        "delivery_date": delivery,
        "account_assignment": {
            "cost_center": cost_center,
            "plant": plant,
            "gl_account": category["gl_account"],
            "budget_cap": COST_CENTER_BUDGET_CAPS.get(cost_center),
        },
        "sourcing": {
            "vendor": category["vendor"],
            "buyer": category["buyer"],
            "catalog_compliance": category["vendor"] != "Buyer review required",
        },
        "risk": {"level": level, "flags": flags},
        "duplicates": duplicate_hits,
        "submission_blockers": submission_blockers,
        "approval_route": approval_route(
            budget,
            category,
            cost_center,
            requester_profile,
            over_budget_cap=over_budget_cap,
        ),
        "requester": requester_profile,
        "parsed": parsed,
        "sap_payload": payload,
    }
    REQUISITION_STORE.upsert_draft(draft)
    return draft


def validate_request(message: str, requester: dict[str, Any] | None = None) -> dict[str, Any]:
    """Policy pre-check without allocating a requisition ID or writing the ledger."""
    requester_profile = normalize_requester(requester)
    parsed = parse_request(message)
    category = infer_category(message)
    title = " + ".join(item["description"] for item in parsed["items"][:3])
    duplicate_hits = REQUISITION_STORE.find_duplicates(
        cost_center=parsed["cost_center"],
        title=title,
        requester_employee_id=requester_profile["employee_id"],
    )
    level, flags = evaluate_risk(
        message,
        parsed["budget"],
        parsed["cost_center"],
        category["vendor"],
        duplicate_hits=duplicate_hits,
    )
    blockers = draft_submission_blockers(
        parsed["cost_center"], parsed["budget"], duplicate_hits=duplicate_hits
    )
    return {
        "valid": len(blockers) == 0,
        "parsed": parsed,
        "category": {
            "material_group": category["material_group"],
            "gl_account": category["gl_account"],
            "vendor": category["vendor"],
            "buyer": category["buyer"],
        },
        "risk": {"level": level, "flags": flags},
        "duplicates": duplicate_hits,
        "submission_blockers": blockers,
        "budget_cap": COST_CENTER_BUDGET_CAPS.get(parsed["cost_center"]),
        "requester": requester_profile,
    }


class RequestHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        write_json(self, 200, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/health":
            write_json(
                self,
                200,
                {
                    "status": "ok",
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "architecture": [
                        "sap-requisition-gateway",
                        "sap-requisition-nl-parser",
                        "sap-requisition-policy-engine",
                        "sap-requisition-ledger",
                        "sap-requisition-sap-client",
                    ],
                    "ledger": REQUISITION_STORE.stats(),
                },
            )
            return

        if path == "/api/sap/config-status":
            write_json(self, 200, sap_config_status())
            return

        if path == "/api/policy/rules":
            write_json(self, 200, policy_catalog())
            return

        if path == "/api/requisitions":
            limit = int((query.get("limit") or ["20"])[0])
            write_json(
                self,
                200,
                {
                    "items": REQUISITION_STORE.list_recent(limit=limit),
                    "stats": REQUISITION_STORE.stats(),
                },
            )
            return

        if path.startswith("/api/requisition/") and path.endswith("/status"):
            req_id = path[len("/api/requisition/") : -len("/status")]
            record = REQUISITION_STORE.get(req_id)
            if not record:
                write_json(self, 404, {"error": "Requisition not found", "requisition_id": req_id})
                return
            write_json(self, 200, record)
            return

        if path == "/api/workflow":
            write_json(
                self,
                200,
                {
                    "version": SERVICE_VERSION,
                    "stages": [
                        "identity",
                        "intake",
                        "parse",
                        "policy_gate",
                        "duplicate_check",
                        "sap_payload",
                        "approval",
                        "status_loop",
                    ],
                    "services": {
                        "gateway": SERVICE_NAME,
                        "parser": "sap-requisition-nl-parser",
                        "policy": "sap-requisition-policy-engine",
                        "ledger": "sap-requisition-ledger",
                        "sap_client": "sap-requisition-sap-client",
                    },
                    "recommended_integrations": [
                        "Microsoft Teams or Slack intake",
                        "SAP S/4HANA purchase requisition API",
                        "Ariba catalog lookup",
                        "Cost center and GL master data",
                        "Email status notifications",
                        "Durable requisition ledger (Postgres/Redis)",
                    ],
                },
            )
            return

        write_json(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path == "/api/requisition/validate":
            body = parse_body(self)
            message = str(body.get("message", "")).strip()
            requester = body.get("requester")
            if not message:
                write_json(self, 400, {"error": "message is required"})
                return
            if requester is not None and not isinstance(requester, dict):
                write_json(self, 400, {"error": "requester must be an object"})
                return
            write_json(self, 200, validate_request(message, requester))
            return

        if path == "/api/requisition/draft":
            body = parse_body(self)
            message = str(body.get("message", "")).strip()
            requester = body.get("requester")
            if not message:
                write_json(self, 400, {"error": "message is required"})
                return
            if requester is not None and not isinstance(requester, dict):
                write_json(self, 400, {"error": "requester must be an object"})
                return
            write_json(self, 200, build_draft(message, requester))
            return

        if path == "/api/requisition/submit":
            body = parse_body(self)
            payload = body.get("sap_payload")
            config = body.get("sap_config")
            if not isinstance(payload, dict):
                write_json(self, 400, {"error": "sap_payload object is required"})
                return
            if config is not None and not isinstance(config, dict):
                write_json(self, 400, {"error": "sap_config must be an object"})
                return

            req_id = str(payload.get("purchase_requisition", "")).strip()
            blockers = payload_submission_blockers(payload)
            if blockers and req_id:
                REQUISITION_STORE.mark_blocked(req_id, blockers)

            status, result = sap_submit(payload, config)
            if req_id:
                if status < 300 and result.get("status") == "submitted":
                    REQUISITION_STORE.mark_submitted(req_id, result)
                elif result.get("blockers"):
                    REQUISITION_STORE.mark_blocked(req_id, list(result.get("blockers") or []))
            write_json(self, status, result)
            return

        if path == "/api/sap/test-connection":
            body = parse_body(self)
            config = body.get("sap_config")
            if not isinstance(config, dict):
                write_json(self, 400, {"error": "sap_config object is required"})
                return
            status, result = sap_test_connection(config)
            write_json(self, status, result)
            return

        write_json(self, 404, {"error": "Not found"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)
    print(f"{SERVICE_NAME} v{SERVICE_VERSION} running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
