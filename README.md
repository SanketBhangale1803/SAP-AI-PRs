# SAP Requisition Command Center

A React + Python prototype that turns plain-language purchase requests into structured SAP purchase requisition drafts.

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

- `GET /api/health`
- `GET /api/workflow`
- `POST /api/requisition/draft`
- `POST /api/requisition/submit`

Example body:

```json
{
  "message": "I need 3 Dell Latitude laptops for Chicago onboarding, cost center 4100, needed by July 15, budget $4200."
}
```

## SAP submission

Draft generation is local. A requisition will not appear in SAP until the backend calls a real SAP API or SAP Build Process Automation trigger.

Technical SAP Build values are backend configuration, not public login fields. Copy `.env.example` to `.env` and set:

- `SAP_BASE_URL`, for example your SAP API trigger gateway host
- `SAP_API_PATH`, the path portion of the API trigger URL from Control Tower > Environments > Triggers > View
- `SAP_BUILD_DEFINITION_ID` from Control Tower > Environments > Triggers > View
- `SAP_AUTH_TYPE`, usually `api_key_bearer` for SAP Build API triggers
- `SAP_BEARER_TOKEN` and `SAP_API_KEY` from the deployed trigger/service credentials
- Optional `SAP_CLIENT` and `SAP_FETCH_CSRF`

The React app now asks public users only for SAP user ID and password. The backend merges that user identity with the server-side trigger configuration before submitting.

For SAP Build API triggers, the app defaults to:

- API path: the copied trigger URL path, for example `/public/unified/v1/triggers/api/<triggerUid>`
- Payload mode: `SAP Build trigger`
- Auth type: `Bearer token + API key`, configured server-side
- CSRF fetch: off

Do not enter a monitoring URL such as `/monitoring/workflow-instances/...`; that page only displays an existing process instance. To start a workflow, use the process trigger endpoint. To create a purchase requisition directly, use the S/4HANA purchase requisition API endpoint.

Many SAP systems also require company-specific payload fields, destination service routing, OAuth, principal propagation, or API gateway mappings. Adapt `backend/server.py` in `sap_submit` if your tenant requires a different payload shape.

## SAP Build Process Inputs

The frontend submits `sap_payload` as a flat JSON object that matches the SAP Build Process Inputs shown in Process Details > Variables:

```json
{
  "account_assignment_category": "K",
  "cost_center": "4100",
  "document_type": "NB",
  "estimated_net_price": 1400,
  "gl_account": "641200",
  "item_number": "00010",
  "material_group": "IT-HARDWARE",
  "plant": "1100",
  "purchase_requisition": "PR-85949EA8",
  "quantity": 3,
  "required_date": "2026-07-15",
  "short_text": "Dell latitude laptops",
  "tool_name": "Dell latitude laptops",
  "unit_of_measure": "EA"
}
```

For SAP Build API trigger endpoints such as `/public/unified/v1/triggers/api/<triggerUid>`, the backend sends the process inputs inside SAP's trigger envelope:

```json
{
  "definitionId": "YOUR_TRIGGER_DEFINITION_ID",
  "context": {
    "startEvent": {
      "account_assignment_category": "K",
      "cost_center": "4100",
      "document_type": "NB",
      "estimated_net_price": 1400,
      "gl_account": "653100",
      "item_number": "00010",
      "material_group": "SERVICES-SOFTWARE",
      "plant": "1000",
      "purchase_requisition": "PR-D0513453",
      "quantity": 8,
      "required_date": "2026-06-20",
      "short_text": "Engineers",
      "tool_name": "Engineers",
      "unit_of_measure": "EA"
    }
  }
}
```

Get both `SAP_API_PATH` and `SAP_BUILD_DEFINITION_ID` from SAP Build Lobby > Control Tower > Environments > your environment > Triggers > View. `SAP_API_PATH` is the path from the copied trigger URL, including the real trigger UID. `SAP_BUILD_DEFINITION_ID` is the `definitionID` from that same view. Neither value is the process name, the trigger display label, or a monitoring URL. Keep `SAP_BUILD_CONTEXT_MODE=startEvent` when your script reads values with `$.context.startEvent.tool_name`.

Use this SAP Build script task to read every process input consistently:

```javascript
function processRequisition() {
    var input = $.context.startEvent || {};

    var requiredFields = ["quantity", "required_date", "tool_name"];
    var missingFields = [];

    requiredFields.forEach(function (field) {
        if (input[field] === undefined || input[field] === null || input[field] === "") {
            missingFields.push(field);
        }
    });

    if (missingFields.length > 0) {
        return {
            status: "Error",
            message: "Missing required process input(s): " + missingFields.join(", ")
        };
    }

    var requisition = {
        account_assignment_category: input.account_assignment_category,
        cost_center: input.cost_center,
        document_type: input.document_type || "NB",
        estimated_net_price: Number(input.estimated_net_price || 0),
        gl_account: input.gl_account,
        item_number: input.item_number,
        material_group: input.material_group,
        plant: input.plant,
        purchase_requisition: input.purchase_requisition,
        quantity: Number(input.quantity),
        required_date: input.required_date,
        short_text: input.short_text,
        tool_name: input.tool_name,
        unit_of_measure: input.unit_of_measure || "EA"
    };

    $.context.requisition = requisition;

    return {
        status: "Success",
        message: "Data reached SAP pipeline successfully",
        purchase_requisition: requisition.purchase_requisition,
        tool_name: requisition.tool_name,
        quantity: requisition.quantity,
        required_date: requisition.required_date
    };
}

processRequisition();
```

## Production Integration Checklist

This repo now separates the prototype fallback from the real enterprise integration points:

- Replace the local employee directory fixture in `src/main.jsx` with the company identity source, usually SSO claims, SAP IAS/IPS, SuccessFactors, Microsoft Entra ID, or a company employee profile API.
- Pass a stable employee identifier, requester email, department, manager name, and manager email into the SAP Build context.
- Add matching process inputs in SAP Build for `requester_employee_id`, `requester_name`, `requester_email`, `approver_name`, `approver_email`, `cost_center`, and `estimated_net_price`.
- Add a SAP Build approval/user task before the script/end step. Set the recipient from the approver email field if your tenant supports dynamic recipients, or map employee ID to the right SAP user in a script/destination.
- Keep bearer tokens, API keys, destinations, and trigger IDs server-side. Do not collect real user passwords in the browser for production; use SSO/session principal propagation where the company platform supports it.
- Replace the local parsing heuristics with company master data services for cost center, GL account, material group, plant, catalog/vendor, budget, and duplicate PR checks.

If the requester message does not include a cost center, the draft includes `submission_blockers` and the app disables SAP submission. The backend also returns `requester_correction_required` before calling SAP, so incomplete requests are routed back locally instead of producing an SAP HTTP 400.

## Getting SAP credentials

1. Get your SAP user ID from your company SAP administrator. SAP says SAP for Me access requires an S-user generated by your company administrator, or an SAP Universal ID linked to an S-user: https://support.sap.com/content/s4m/help/access.html
2. If you need to manage linked S/P-user identities, create or open SAP Universal ID Account Manager: https://www.sap.com/account/universal-id.html
3. For direct SAP S/4HANA purchase requisition submission, find the exact API package and path in SAP Business Accelerator Hub: https://hub.sap.com/
4. For SAP Build Process Automation, ask the tenant owner for the deployed API trigger URL, trigger definition ID, OAuth bearer token, and API key. SAP documents API triggers here: https://help.sap.com/docs/build-process-automation/sap-build-process-automation/execute-api-trigger-07376e021794424db824b19c3e6ad831
5. Put those technical values in backend `.env`. Public users should only enter their SAP user ID and password in the browser.
