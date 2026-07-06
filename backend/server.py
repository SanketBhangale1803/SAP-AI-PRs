from __future__ import annotations

import json
import os
import re
import uuid
from base64 import b64encode
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen


HOST = "127.0.0.1"
PORT = 8000


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


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def backend_sap_config() -> dict[str, Any]:
    return {
        "baseUrl": os.getenv("SAP_BASE_URL", "").strip(),
        "apiPath": os.getenv("SAP_API_PATH", "").strip(),
        "sapClient": os.getenv("SAP_CLIENT", "").strip(),
        "authType": os.getenv("SAP_AUTH_TYPE", "api_key_bearer").strip() or "api_key_bearer",
        "bearerToken": os.getenv("SAP_BEARER_TOKEN", "").strip(),
        "apiKey": os.getenv("SAP_API_KEY", "").strip(),
        "apiKeyHeader": os.getenv("SAP_API_KEY_HEADER", "api-key").strip() or "api-key",
        "definitionId": os.getenv("SAP_BUILD_DEFINITION_ID", "").strip(),
        "contextMode": os.getenv("SAP_BUILD_CONTEXT_MODE", "startEvent").strip() or "startEvent",
        "contextFields": os.getenv("SAP_BUILD_CONTEXT_FIELDS", "").strip(),
        "payloadMode": os.getenv("SAP_PAYLOAD_MODE", "sap_build_trigger").strip() or "sap_build_trigger",
        "fetchCsrf": env_bool("SAP_FETCH_CSRF", False),
    }


PUBLIC_CONFIG_FIELDS = {"username", "password"}
PUBLIC_REQUESTER_FIELDS = {
    "employee_id",
    "name",
    "email",
    "department",
    "business_unit",
    "manager_name",
    "manager_email",
}


def merge_sap_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = backend_sap_config()
    config = config or {}

    # Keep tenant URL, trigger ID, bearer token, and API key server-side. The UI
    # supplies only the end user's identity fields.
    for field in PUBLIC_CONFIG_FIELDS:
        if field in config:
            merged[field] = config.get(field)

    # Backward compatibility for older local builds that still send technical
    # fields. Environment variables remain authoritative when present.
    for field in [
        "baseUrl",
        "apiPath",
        "sapClient",
        "authType",
        "bearerToken",
        "apiKey",
        "apiKeyHeader",
        "definitionId",
        "contextMode",
        "contextFields",
        "payloadMode",
        "fetchCsrf",
    ]:
        if not merged.get(field) and config.get(field):
            merged[field] = config.get(field)

    return merged


def sap_config_status() -> dict[str, Any]:
    config = backend_sap_config()
    is_trigger = is_sap_build_api_trigger(config)
    missing: list[str] = []
    if not config.get("baseUrl"):
        missing.append("SAP_BASE_URL")
    if is_trigger:
        if not config.get("apiPath"):
            missing.append("SAP_API_PATH")
        if not config.get("definitionId"):
            missing.append("SAP_BUILD_DEFINITION_ID")
        if config.get("authType") == "api_key_bearer":
            if not config.get("bearerToken"):
                missing.append("SAP_BEARER_TOKEN")
            if not config.get("apiKey"):
                missing.append("SAP_API_KEY")

    return {
        "configured": not missing,
        "missing": missing,
        "target_host": urlparse(config.get("baseUrl", "")).netloc if config.get("baseUrl") else "",
        "api_path": config.get("apiPath", ""),
        "payload_mode": config.get("payloadMode", ""),
        "auth_type": config.get("authType", ""),
        "uses_backend_trigger": is_trigger,
    }


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


def build_auth_header(config: dict[str, Any]) -> str:
    auth_type = str(config.get("authType", "basic")).lower()
    if auth_type in {"bearer", "api_key_bearer"}:
        token = str(config.get("bearerToken", "")).strip()
        return f"Bearer {token}" if token else ""

    username = str(config.get("username", ""))
    password = str(config.get("password", ""))
    if not username or not password:
        return ""
    encoded = b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def build_api_headers(config: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = str(config.get("apiKey", "")).strip()
    if api_key:
        header_name = str(config.get("apiKeyHeader", "api-key")).strip() or "api-key"
        headers[header_name] = api_key
    user_id = str(config.get("username", "")).strip()
    if user_id and str(config.get("authType", "")).lower() == "api_key_bearer":
        headers["X-User-ID"] = user_id
    return headers


def append_sap_client(url: str, config: dict[str, Any]) -> str:
    sap_client = str(config.get("sapClient", "")).strip()
    if not sap_client:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({'sap-client': sap_client})}"


def is_sap_build_api_trigger(config: dict[str, Any]) -> bool:
    path = str(config.get("apiPath", "")).strip().lower()
    payload_mode = str(config.get("payloadMode", "auto")).strip().lower()
    if payload_mode == "sap_build_trigger":
        return True
    if payload_mode == "raw":
        return False
    return "/triggers/api/" in path or path.endswith("/triggers/api")


def submit_url(config: dict[str, Any]) -> str:
    base_url = str(config.get("baseUrl", "")).strip().rstrip("/")
    path = str(config.get("apiPath", "")).strip()
    if not path:
        path = "/sap/opu/odata/sap/API_PURCHASEREQ_PROCESS_SRV/A_PurchaseRequisitionHeader"
    return append_sap_client(urljoin(f"{base_url}/", path.lstrip("/")), config)


def config_issue(config: dict[str, Any]) -> str:
    base_url = str(config.get("baseUrl", "")).strip()
    path = str(config.get("apiPath", "")).strip()
    if not base_url:
        return ""

    try:
        parsed_base = urlparse(base_url)
    except ValueError:
        return "Base URL is invalid."

    if parsed_base.path and parsed_base.path != "/":
        return "Base URL should contain only scheme and host. Put API paths in the API path field."
    if is_sap_build_api_trigger(config) and not path:
        return "SAP Build API trigger submission requires SAP_API_PATH from Control Tower > Environments > Triggers > View."
    if re.search(r"^https?://", path, re.IGNORECASE):
        return "API path should be a path, not a full URL."
    if any(fragment in path for fragment in ["/monitoring/", "/workflow-instances/", "monitorworkflow"]):
        return "This is a SAP Build monitoring endpoint. It can read workflow instances, but it cannot submit a new requisition."
    if is_sap_build_api_trigger(config) and not str(config.get("definitionId", "")).strip():
        return "SAP Build API trigger submission requires the trigger definition ID from Control Tower > Environments > Triggers > View."
    if is_sap_build_api_trigger(config) and str(config.get("authType", "")).lower() == "bearer":
        return 'SAP Build API triggers require an API key with the OAuth bearer token. Choose "Bearer token + API key".'
    if (
        is_sap_build_api_trigger(config)
        and str(config.get("authType", "")).lower() == "api_key_bearer"
        and not str(config.get("bearerToken", "")).strip()
    ):
        return "SAP Build API trigger submission requires a backend OAuth bearer token."
    if (
        is_sap_build_api_trigger(config)
        and str(config.get("authType", "")).lower() == "api_key_bearer"
        and not str(config.get("apiKey", "")).strip()
    ):
        return "SAP Build API trigger submission requires the API key from the trigger details."
    return ""


def classify_sap_http_error(exc: HTTPError, fallback_status: str, body: str = "") -> tuple[str, str]:
    lower_body = body.lower()
    if exc.code == 400 and "triggeruid" in lower_body and "invalid" in lower_body:
        return (
            "sap_trigger_uid_invalid",
            "SAP Build rejected the trigger UID in the API path. Set SAP_API_PATH to the exact path from Control Tower > Environments > Triggers > View; do not use the placeholder /public/unified/v1/triggers/api/incomingRequest unless that is the copied trigger URL.",
        )
    if exc.code == 400 and "triggeruuid" in lower_body and "invalid" in lower_body:
        return (
            "sap_trigger_definition_invalid",
            "SAP Build rejected the trigger definition ID. Set SAP_BUILD_DEFINITION_ID to the API trigger definition ID from Control Tower > Environments > Triggers > View, not the process name or monitoring URL.",
        )
    if exc.code == 401:
        return (
            "sap_auth_failed",
            "SAP rejected the credentials. Check user ID, password/token, auth type, and whether this endpoint allows that auth method.",
        )
    if exc.code == 403:
        return (
            "sap_authorization_failed",
            "SAP accepted the identity but the user is not authorized for this endpoint or operation.",
        )
    if exc.code == 404:
        return (
            "sap_endpoint_not_found",
            "SAP could not find this endpoint. Check whether the API path is a callable trigger/API path, not a monitoring page.",
        )
    if exc.code == 400:
        return (
            "sap_payload_rejected",
            "SAP reached the endpoint but rejected the request payload. Check required fields and payload shape.",
        )
    return (
        fallback_status,
        "SAP rejected the request. Check endpoint, auth method, user authorization, CSRF policy, and payload mapping.",
    )


def sap_test_connection(config: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    config = merge_sap_config(config)
    base_url = str(config.get("baseUrl", "")).strip().rstrip("/")
    authorization = build_auth_header(config)

    if not base_url:
        return (
            409,
            {
                "status": "not_configured",
                "message": "Backend SAP target is not configured. Set SAP_BASE_URL before testing the connection.",
            },
        )
    issue = config_issue(config)
    if issue:
        return (
            422,
            {
                "status": "sap_config_invalid",
                "message": issue,
            },
        )
    if not authorization:
        return (
            409,
            {
                "status": "not_configured",
                "message": "Enter SAP user credentials or configure backend SAP_AUTH_TYPE credentials before testing the connection.",
            },
        )

    url = submit_url(config)
    headers = {
        "Accept": "application/json",
        "Authorization": authorization,
        **build_api_headers(config),
    }
    if config.get("fetchCsrf", True):
        headers["X-CSRF-Token"] = "Fetch"

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=20) as response:
            return (
                200,
                {
                    "status": "sap_connection_ok",
                    "message": "SAP endpoint responded to the authenticated request.",
                    "http_status": response.status,
                    "csrf_token_available": bool(response.headers.get("X-CSRF-Token")),
                },
            )
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1200]
        status, message = classify_sap_http_error(exc, "sap_connection_rejected", body)
        # 405 usually means the endpoint exists but does not allow GET, which is enough to prove auth was not the first blocker.
        if exc.code == 405:
            return (
                200,
                {
                    "status": "sap_endpoint_reachable",
                    "message": "SAP endpoint exists, but it does not allow GET. Try submit if the payload and method are correct.",
                    "http_status": exc.code,
                },
            )
        return (
            502,
                {
                    "status": status,
                    "message": message,
                    "http_status": exc.code,
                    "sap_error": body,
                },
            )
    except URLError:
        return (
            502,
            {
                "status": "sap_connection_error",
                "message": "Could not connect to the SAP base URL from the Python backend.",
            },
        )


def sap_submit(payload: dict[str, Any], config: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    config = merge_sap_config(config)
    base_url = str(config.get("baseUrl", "")).strip().rstrip("/")
    authorization = build_auth_header(config)

    if not base_url:
        return (
            409,
            {
                "status": "not_configured",
                "message": "Backend SAP target is not configured. Set SAP_BASE_URL before submitting.",
                "required_fields": ["SAP_BASE_URL"],
            },
        )
    issue = config_issue(config)
    if issue:
        return (
            422,
            {
                "status": "sap_config_invalid",
                "message": issue,
            },
        )
    if not authorization:
        return (
            409,
            {
                "status": "not_configured",
                "message": "Enter SAP user credentials or configure backend SAP_AUTH_TYPE credentials before submitting.",
                "required_fields": ["username/password or SAP_BEARER_TOKEN"],
            },
        )

    blockers = payload_submission_blockers(payload)
    if blockers:
        return (
            422,
            {
                "status": "requester_correction_required",
                "message": "Route this request back before SAP submission.",
                "blockers": blockers,
            },
        )

    url = submit_url(config)
    outbound_payload = build_outbound_payload(payload, config)
    encoded = json.dumps(outbound_payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": authorization,
        **build_api_headers(config),
    }

    if config.get("fetchCsrf", True):
        csrf_headers = {
            "Accept": "application/json",
            "Authorization": authorization,
            "X-CSRF-Token": "Fetch",
            **build_api_headers(config),
        }
        csrf_request = Request(url, headers=csrf_headers, method="GET")
        try:
            with urlopen(csrf_request, timeout=20) as response:
                csrf_token = response.headers.get("X-CSRF-Token")
                cookie = response.headers.get("Set-Cookie")
                if csrf_token:
                    headers["X-CSRF-Token"] = csrf_token
                if cookie:
                    headers["Cookie"] = cookie
        except HTTPError as exc:
            if exc.code not in {404, 405}:
                status, message = classify_sap_http_error(exc, "sap_auth_or_csrf_error")
                return (
                    502,
                    {
                        "status": status,
                        "message": message,
                        "http_status": exc.code,
                    },
                )
        except URLError:
            return (
                502,
                {
                    "status": "sap_connection_error",
                    "message": "Could not connect to the SAP base URL from the Python backend.",
                },
            )

    request = Request(url, data=encoded, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            parsed_body = json.loads(response_body) if response_body else {}
            return (
                response.status,
                {
                    "status": "submitted",
                    "message": "SAP accepted the request. Check the SAP response for the created PR or workflow instance number.",
                    "sap_response": parsed_body,
                },
            )
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1200]
        status, message = classify_sap_http_error(exc, "sap_rejected", body)
        return (
            502,
            {
                "status": status,
                "message": message,
                "http_status": exc.code,
                "sap_error": body,
                "sent_payload_shape": describe_payload_shape(outbound_payload),
                "sent_payload_preview": outbound_payload,
            },
        )
    except URLError:
        return (
            502,
            {
                "status": "sap_connection_error",
                "message": "Could not connect to the SAP base URL from the Python backend.",
            },
        )


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


def risk_flags(message: str, budget: int, cost_center: str, vendor: str) -> tuple[str, list[str]]:
    flags: list[str] = []
    lower = message.lower()
    if cost_center == "MISSING":
        flags.append("Cost center missing; route back to requester before SAP submission.")
    if vendor == "Buyer review required":
        flags.append("No preferred source found; buyer should confirm vendor or catalog item.")
    if budget >= 5000:
        flags.append("Spend exceeds $5,000; manager and finance approval required.")
    if any(term in lower for term in ["urgent", "asap", "rush"]):
        flags.append("Urgent request; capture business justification and delivery constraints.")
    if any(term in lower for term in ["laptop", "monitor", "iphone"]):
        flags.append("Check duplicate open PRs and asset standards before ordering.")
    if not flags:
        flags.append("Ready for guided submission; no major policy gaps detected.")

    if len(flags) >= 3 or budget >= 10000:
        level = "High"
    elif len(flags) == 2 or budget >= 5000 or cost_center == "MISSING":
        level = "Medium"
    else:
        level = "Low"
    return level, flags


def approval_route(
    budget: int, category: dict[str, str], cost_center: str, requester: dict[str, Any] | None = None
) -> list[str]:
    manager_name = str((requester or {}).get("manager_name", "")).strip()
    route = ["Requester", "Cost center owner" if cost_center != "MISSING" else "Requester correction"]
    if manager_name:
        route.append(f"Manager approval ({manager_name})")
    else:
        route.append("Manager approval")
    if budget >= 5000:
        route.append("Finance")
    route.append(category["buyer"])
    route.append("SAP PR created")
    return route


def compact_money(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def build_outbound_payload(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if is_sap_build_api_trigger(config):
        context_payload = payload
        context_fields = [
            field.strip()
            for field in str(config.get("contextFields", "")).split(",")
            if field.strip()
        ]
        if context_fields:
            context_payload = {field: payload.get(field) for field in context_fields}
        context_mode = str(config.get("contextMode", "startEvent")).strip().lower()
        context = context_payload if context_mode == "direct" else {"startEvent": context_payload}
        return {
            "definitionId": str(config.get("definitionId", "")).strip(),
            "context": context,
        }
    return payload


def describe_payload_shape(payload: dict[str, Any]) -> dict[str, Any]:
    description: dict[str, Any] = {"top_level_keys": list(payload.keys())}
    context = payload.get("context")
    if isinstance(context, dict):
        description["context_keys"] = list(context.keys())
        start_event = context.get("startEvent")
        if isinstance(start_event, dict):
            description["start_event_keys"] = list(start_event.keys())
    return description


def draft_submission_blockers(cost_center: str) -> list[str]:
    blockers: list[str] = []
    if cost_center == "MISSING":
        blockers.append("Cost center missing; route back to requester before SAP submission.")
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


def normalize_requester(requester: dict[str, Any] | None) -> dict[str, str]:
    requester = requester or {}
    return {
        field: str(requester.get(field, "")).strip()
        for field in PUBLIC_REQUESTER_FIELDS
    }


def build_draft(message: str, requester: dict[str, Any] | None = None) -> dict[str, Any]:
    requester_profile = normalize_requester(requester)
    category = infer_category(message)
    items = extract_items(message)
    quantity = items[0]["quantity"]
    budget = extract_budget(message)
    cost_center = extract_cost_center(message)
    plant = extract_plant(message)
    delivery = extract_delivery_date(message)
    level, flags = risk_flags(message, budget, cost_center, category["vendor"])
    submission_blockers = draft_submission_blockers(cost_center)
    req_id = f"PR-{uuid.uuid4().hex[:8].upper()}"

    title = " + ".join(item["description"] for item in items[:3])
    if len(items) > 3:
        title = f"{title} + {len(items) - 3} more"
    unit_price = round(budget / sum(item["quantity"] for item in items), 2) if budget else 0
    enriched_items = [
        {
            "description": item["description"],
            "quantity": item["quantity"],
            "material_group": category["material_group"],
            "unit_price": unit_price,
        }
        for item in items
    ]

    first_item = enriched_items[0]
    payload = {
        "account_assignment_category": "K",
        "cost_center": cost_center,
        "document_type": "NB",
        "estimated_net_price": compact_money(unit_price),
        "gl_account": category["gl_account"],
        "item_number": "00010",
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
    }

    return {
        "requisition_id": req_id,
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
        },
        "sourcing": {
            "vendor": category["vendor"],
            "buyer": category["buyer"],
            "catalog_compliance": category["vendor"] != "Buyer review required",
        },
        "risk": {"level": level, "flags": flags},
        "submission_blockers": submission_blockers,
        "approval_route": approval_route(budget, category, cost_center, requester_profile),
        "requester": requester_profile,
        "sap_payload": payload,
    }


class RequestHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        write_json(self, 200, {"ok": True})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            write_json(self, 200, {"status": "ok", "service": "sap-requisition-backend"})
            return

        if path == "/api/sap/config-status":
            write_json(self, 200, sap_config_status())
            return

        if path == "/api/workflow":
            write_json(
                self,
                200,
                {
                    "stages": [
                        "intake",
                        "parse",
                        "policy_gate",
                        "sap_payload",
                        "approval",
                        "status_loop",
                    ],
                    "recommended_integrations": [
                        "Microsoft Teams or Slack intake",
                        "SAP S/4HANA purchase requisition API",
                        "Ariba catalog lookup",
                        "Cost center and GL master data",
                        "Email status notifications",
                    ],
                },
            )
            return

        write_json(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
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
            status, result = sap_submit(payload, config)
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
    print(f"SAP requisition backend running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
