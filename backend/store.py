"""In-memory requisition ledger for draft history, status, and duplicate checks.

Production deployments should replace this with a durable store (Postgres, Redis,
or SAP Build workflow instance queries). The local ledger is enough for demo
and Runbook service-graph evidence of a dedicated history service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any


SERVICE_NAME = "sap-requisition-ledger"
SERVICE_VERSION = "2.0.0"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RequisitionStore:
    def __init__(self, max_records: int = 200) -> None:
        self._lock = Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self.max_records = max_records

    def upsert_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        req_id = str(draft.get("requisition_id", "")).strip()
        if not req_id:
            raise ValueError("requisition_id is required")

        record = {
            "requisition_id": req_id,
            "status": "drafted",
            "title": draft.get("title"),
            "total_estimated_value": draft.get("total_estimated_value"),
            "cost_center": (draft.get("account_assignment") or {}).get("cost_center"),
            "material_group": (draft.get("item") or {}).get("material_group"),
            "requester_employee_id": (draft.get("requester") or {}).get("employee_id"),
            "line_item_count": len(draft.get("items") or []),
            "risk_level": (draft.get("risk") or {}).get("level"),
            "submission_blockers": list(draft.get("submission_blockers") or []),
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
            "sap_instance": None,
            "events": [{"at": _utcnow(), "event": "draft_created"}],
        }

        with self._lock:
            existing = self._records.get(req_id)
            if existing:
                record["created_at"] = existing.get("created_at", record["created_at"])
                record["events"] = list(existing.get("events") or []) + record["events"]
            self._records[req_id] = record
            if req_id in self._order:
                self._order.remove(req_id)
            self._order.insert(0, req_id)
            self._trim_locked()
            return dict(record)

    def mark_submitted(self, req_id: str, sap_result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(req_id)
            if not record:
                return None
            record["status"] = "submitted"
            record["updated_at"] = _utcnow()
            record["sap_instance"] = (sap_result or {}).get("sap_response")
            record["events"] = list(record.get("events") or []) + [
                {"at": _utcnow(), "event": "submitted_to_sap"}
            ]
            return dict(record)

    def mark_blocked(self, req_id: str, blockers: list[str]) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(req_id)
            if not record:
                return None
            record["status"] = "blocked"
            record["submission_blockers"] = list(blockers)
            record["updated_at"] = _utcnow()
            record["events"] = list(record.get("events") or []) + [
                {"at": _utcnow(), "event": "submission_blocked", "blockers": list(blockers)}
            ]
            return dict(record)

    def get(self, req_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(req_id)
            return dict(record) if record else None

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            ids = self._order[: max(0, limit)]
            return [dict(self._records[req_id]) for req_id in ids if req_id in self._records]

    def find_duplicates(
        self,
        *,
        cost_center: str,
        title: str,
        requester_employee_id: str,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        title_key = " ".join(str(title or "").lower().split())
        hits: list[dict[str, Any]] = []
        with self._lock:
            for req_id in self._order[:limit]:
                record = self._records.get(req_id)
                if not record:
                    continue
                same_cc = str(record.get("cost_center", "")) == str(cost_center)
                same_requester = str(record.get("requester_employee_id", "")) == str(requester_employee_id)
                other_title = " ".join(str(record.get("title") or "").lower().split())
                similar_title = bool(title_key) and (title_key in other_title or other_title in title_key)
                if same_cc and same_requester and similar_title:
                    hits.append(
                        {
                            "requisition_id": record["requisition_id"],
                            "status": record.get("status"),
                            "title": record.get("title"),
                            "created_at": record.get("created_at"),
                        }
                    )
        return hits

    def stats(self) -> dict[str, Any]:
        with self._lock:
            by_status: dict[str, int] = {}
            for record in self._records.values():
                status = str(record.get("status", "unknown"))
                by_status[status] = by_status.get(status, 0) + 1
            return {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "total_records": len(self._records),
                "by_status": by_status,
            }

    def _trim_locked(self) -> None:
        while len(self._order) > self.max_records:
            old_id = self._order.pop()
            self._records.pop(old_id, None)


# Process-wide ledger shared by the HTTP handlers.
REQUISITION_STORE = RequisitionStore()
