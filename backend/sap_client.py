"""SAP Build / S/4HANA submission client for purchase requisitions."""

from __future__ import annotations

import json
import os
import re
from base64 import b64encode
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from policy import payload_submission_blockers


SERVICE_NAME = "sap-requisition-sap-client"
SERVICE_VERSION = "2.0.0"


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


def merge_sap_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = backend_sap_config()
    config = config or {}

    for field in PUBLIC_CONFIG_FIELDS:
        if field in config:
            merged[field] = config.get(field)

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
        "client_service": SERVICE_NAME,
        "client_version": SERVICE_VERSION,
    }


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
        return (422, {"status": "sap_config_invalid", "message": issue})
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
        return (422, {"status": "sap_config_invalid", "message": issue})
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
