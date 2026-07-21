# SAP Requisition Command Center v2

A React + Python prototype that turns plain-language purchase requests into structured SAP purchase requisition drafts, with a modular backend built for policy gating and Runbook service-graph ingestion.

## Architecture (v2)

| Service | Module | Responsibility |
| --- | --- | --- |
| `sap-requisition-gateway` | `backend/server.py` | HTTP API, orchestration |
| `sap-requisition-nl-parser` | `backend/parser.py` | Natural-language intake parsing |
| `sap-requisition-policy-engine` | `backend/policy.py` | Cost-center caps, risk, approvals |
| `sap-requisition-ledger` | `backend/store.py` | Draft history, status, duplicates |
| `sap-requisition-sap-client` | `backend/sap_client.py` | SAP Build / S/4HANA submission |

## Run

Install frontend dependencies:

```bash
npm install
```

Start the Python backend:

```bash
python3 backend/server.py
```

Start the React frontend in another terminal:

```bash
npm run dev
```

Open the Vite URL shown in the terminal, usually `http://127.0.0.1:5173`.

## Backend endpoints

- `GET /api/health` — gateway health, version, architecture, ledger stats
- `GET /api/workflow` — workflow stages and service map
- `GET /api/policy/rules` — published policy catalog (caps, categories, gates)
- `GET /api/requisitions?limit=20` — recent ledger entries
- `GET /api/requisition/{id}/status` — single requisition status timeline
- `POST /api/requisition/validate` — policy pre-check without allocating a PR id
- `POST /api/requisition/draft` — parse + policy + ledger write
- `POST /api/requisition/submit` — submit to SAP Build / S/4HANA
- `GET /api/sap/config-status`
- `POST /api/sap/test-connection`

Example body:

```json
{
  "message": "I need 3 Dell Latitude laptops for Chicago onboarding, cost center 4100, needed by July 15, budget $4200."
}
```

## v2 policy gates

- Cost center required before SAP submission
- Soft budget caps by cost center (`4100`, `7780`, `1200`, `1000`)
- Duplicate detection against recent ledger drafts (same requester + cost center + similar title)
- Multi-line `line_items` array in SAP payload when more than one item is parsed
- Finance / procurement director override routing when caps are exceeded

## SAP submission

Draft generation is local. A requisition will not appear in SAP until the backend calls a real SAP API or SAP Build Process Automation trigger.

Technical SAP Build values are backend configuration, not public login fields. Copy `.env.example` to `.env` and set:

- `SAP_BASE_URL`, for example your SAP API trigger gateway host
- `SAP_API_PATH`, the path portion of the API trigger URL from Control Tower > Environments > Triggers > View
- `SAP_BUILD_DEFINITION_ID` from Control Tower > Environments > Triggers > View
- `SAP_AUTH_TYPE`, usually `api_key_bearer` for SAP Build API triggers
- `SAP_BEARER_TOKEN` and `SAP_API_KEY` from the deployed trigger/service credentials
- Optional `SAP_CLIENT` and `SAP_FETCH_CSRF`

The React app asks public users only for SAP user ID (and password when not using api_key_bearer). The backend merges that user identity with the server-side trigger configuration before submitting.

Do not enter a monitoring URL such as `/monitoring/workflow-instances/...`; that page only displays an existing process instance. To start a workflow, use the process trigger endpoint.

Adapt `backend/sap_client.py` in `sap_submit` if your tenant requires a different payload shape.

## Production Integration Checklist

- Replace the local employee directory fixture in `src/main.jsx` with the company identity source (SSO / HR directory).
- Replace `backend/store.py` in-memory ledger with Postgres, Redis, or SAP Build workflow instance queries.
- Keep bearer tokens, API keys, destinations, and trigger IDs server-side.
- Replace local parsing heuristics with company master data services for cost center, GL, material group, plant, catalog/vendor, budget, and duplicate PR checks.

If the requester message does not include a cost center, or exceeds a cost-center soft cap / duplicate gate, the draft includes `submission_blockers` and the app disables SAP submission.
