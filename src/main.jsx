import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000';

const sampleRequests = [
  'I need 3 Dell Latitude laptops for Chicago onboarding, cost center 4100, needed by July 15, budget $4200.',
  'Create a PR for 25 safety gloves and 10 hard hats for Dallas warehouse, plant 1200, needed next Friday.',
  'Need AWS training seats for 8 engineers, marketing project Phoenix, cost center 7780, target spend $9600.'
];

const employeeDirectory = {
  E10042: {
    employee_id: 'E10042',
    name: 'Avery Johnson',
    email: 'avery.johnson@company.example',
    department: 'IT Operations',
    business_unit: 'North America Shared Services',
    manager_name: 'Maya Chen',
    manager_email: 'maya.chen@company.example'
  },
  E22891: {
    employee_id: 'E22891',
    name: 'Priya Shah',
    email: 'priya.shah@company.example',
    department: 'Warehouse Operations',
    business_unit: 'Supply Chain',
    manager_name: 'Daniel Kim',
    manager_email: 'daniel.kim@company.example'
  },
  E37710: {
    employee_id: 'E37710',
    name: 'Sanket Bhangale',
    email: 'sanketbhangale3918@gmail.com',
    department: 'Procurement Engineering',
    business_unit: 'Digital Transformation',
    manager_name: 'Procurement Approver',
    manager_email: 'sanketbhangale3918@gmail.com'
  }
};

const emptyRequester = {
  employee_id: 'E37710',
  name: 'Sanket Bhangale',
  email: 'sanketbhangale3918@gmail.com',
  department: 'Procurement Engineering',
  business_unit: 'Digital Transformation',
  manager_name: 'Procurement Approver',
  manager_email: 'sanketbhangale3918@gmail.com'
};

const emptySapSession = {
  username: 'E37710',
  password: ''
};

const processSteps = [
  { id: 'identity', label: 'Identity', detail: 'Employee and manager resolved' },
  { id: 'intake', label: 'Intake', detail: 'Request captured' },
  { id: 'policy', label: 'Policy', detail: 'Accounting and risk checked' },
  { id: 'approval', label: 'Approval', detail: 'Manager decision in SAP Build' },
  { id: 'sap', label: 'SAP', detail: 'Workflow instance created' }
];

function App() {
  const [sapSession, setSapSession] = useState(emptySapSession);
  const [requester, setRequester] = useState(emptyRequester);
  const [requestText, setRequestText] = useState(sampleRequests[0]);
  const [draft, setDraft] = useState(null);
  const [sapStatus, setSapStatus] = useState(null);
  const [connectionResult, setConnectionResult] = useState(null);
  const [submitResult, setSubmitResult] = useState(null);
  const [activeStep, setActiveStep] = useState('identity');
  const [isDrafting, setIsDrafting] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [error, setError] = useState('');

  const identityReady = useMemo(() => isRequesterReady(requester), [requester]);
  const sapIssue = useMemo(() => getSapIssue(sapSession, sapStatus), [sapSession, sapStatus]);
  const sapReady = useMemo(() => !sapIssue, [sapIssue]);
  const blockers = useMemo(() => getDraftBlockers(draft), [draft]);
  const canSubmit = Boolean(draft && identityReady && sapReady && blockers.length === 0);
  const readiness = useMemo(
    () => getReadiness({ identityReady, sapReady, draft, blockers }),
    [identityReady, sapReady, draft, blockers]
  );

  useEffect(() => {
    let ignore = false;

    async function loadStatus() {
      try {
        const response = await fetch(`${API_BASE}/api/sap/config-status`);
        const data = await response.json();
        if (!ignore) setSapStatus({ ok: response.ok, ...data });
      } catch {
        if (!ignore) {
          setSapStatus({
            ok: false,
            configured: false,
            missing: ['backend'],
            message: 'Backend is not reachable.'
          });
        }
      }
    }

    loadStatus();
    return () => {
      ignore = true;
    };
  }, []);

  function updateRequester(field, value) {
    setRequester((current) => ({ ...current, [field]: value }));
    setDraft(null);
    setSubmitResult(null);
    setActiveStep('identity');
  }

  function updateSession(field, value) {
    setSapSession((current) => ({ ...current, [field]: value }));
    setConnectionResult(null);
  }

  function lookupEmployee() {
    const profile = employeeDirectory[requester.employee_id.trim().toUpperCase()];
    if (profile) {
      setRequester(profile);
      setSapSession((current) => ({ ...current, username: profile.employee_id }));
      setError('');
    } else {
      setError('Employee ID was not found in the local directory fixture.');
    }
    setActiveStep('identity');
  }

  async function createDraft() {
    setIsDrafting(true);
    setError('');
    setSubmitResult(null);
    setActiveStep('intake');
    try {
      const response = await fetch(`${API_BASE}/api/requisition/draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: requestText, requester })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `Backend returned ${response.status}`);
      setDraft(data);
      setActiveStep('policy');
    } catch (err) {
      setError(err.message || 'Could not build a requisition draft.');
    } finally {
      setIsDrafting(false);
    }
  }

  async function submitToSap() {
    if (!draft) return;
    setIsSubmitting(true);
    setSubmitResult(null);
    setActiveStep('sap');
    try {
      const response = await fetch(`${API_BASE}/api/requisition/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sap_payload: draft.sap_payload, sap_config: sapSession })
      });
      const data = await response.json();
      setSubmitResult({ ok: response.ok, ...data });
      if (response.ok) setActiveStep('approval');
    } catch {
      setSubmitResult({
        ok: false,
        status: 'connection_error',
        message: 'Could not reach the backend submit endpoint.'
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  async function testConnection() {
    setIsTesting(true);
    setConnectionResult(null);
    try {
      const response = await fetch(`${API_BASE}/api/sap/test-connection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sap_config: sapSession })
      });
      const data = await response.json();
      setConnectionResult({ ok: response.ok, ...data });
    } catch {
      setConnectionResult({
        ok: false,
        status: 'connection_error',
        message: 'Could not reach the backend test endpoint.'
      });
    } finally {
      setIsTesting(false);
    }
  }

  return (
    <main className="product-shell">
      <aside className="side-nav">
        <div className="sap-lockup">
          <strong>SAP</strong>
          <span>ReqOps</span>
        </div>
        <nav aria-label="Sections">
          {processSteps.map((step) => (
            <button
              key={step.id}
              className={activeStep === step.id ? 'nav-item active' : 'nav-item'}
              onClick={() => setActiveStep(step.id)}
            >
              <span>{step.label.slice(0, 2)}</span>
              {step.label}
            </button>
          ))}
        </nav>
      </aside>

      <section className="main-workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Enterprise requisition intake</p>
            <h1>Purchase request cockpit</h1>
          </div>
          <div className="header-actions">
            <StatusPill tone={identityReady ? 'good' : 'warn'} label={identityReady ? 'Employee resolved' : 'Identity needed'} />
            <StatusPill tone={sapReady ? 'good' : 'warn'} label={sapReady ? 'SAP ready' : 'SAP blocked'} />
          </div>
        </header>

        <section className="summary-strip" aria-label="Request readiness">
          {readiness.map((item) => (
            <article key={item.label} className={`summary-tile ${item.tone}`}>
              <span>{item.value}</span>
              <strong>{item.label}</strong>
              <p>{item.detail}</p>
            </article>
          ))}
        </section>

        <section className="work-grid">
          <section className="panel identity-panel">
            <PanelTitle eyebrow="Requester" title="Employee identity" />
            <div className="form-grid">
              <label className="input-field">
                <span>Employee ID</span>
                <div className="input-action">
                  <input
                    value={requester.employee_id}
                    onChange={(event) => updateRequester('employee_id', event.target.value)}
                    placeholder="E10042"
                  />
                  <button type="button" onClick={lookupEmployee}>
                    Lookup
                  </button>
                </div>
              </label>
              <TextField label="Employee name" value={requester.name} onChange={(value) => updateRequester('name', value)} />
              <TextField label="Corporate email" value={requester.email} onChange={(value) => updateRequester('email', value)} />
              <TextField label="Department" value={requester.department} onChange={(value) => updateRequester('department', value)} />
              <TextField label="Business unit" value={requester.business_unit} onChange={(value) => updateRequester('business_unit', value)} />
              <TextField label="Manager name" value={requester.manager_name} onChange={(value) => updateRequester('manager_name', value)} />
              <TextField label="Manager email" value={requester.manager_email} onChange={(value) => updateRequester('manager_email', value)} />
            </div>
            <div className="integration-note">
              <strong>Company integration contract</strong>
              <p>Replace the local directory fixture with your company's SSO or HR directory API. The SAP payload already carries employee and approver fields.</p>
            </div>
          </section>

          <section className="panel session-panel">
            <PanelTitle eyebrow="Connection" title="SAP Build runtime" />
            <SapBackendStatus sapStatus={sapStatus} />
            <div className="form-grid compact">
              <TextField label="SAP principal" value={sapSession.username} onChange={(value) => updateSession('username', value)} />
            </div>
            {sapIssue ? <p className="notice warn">{sapIssue}</p> : null}
            <button className="secondary-button" onClick={testConnection} disabled={!sapReady || isTesting}>
              {isTesting ? 'Testing connection...' : 'Test SAP connection'}
            </button>
            {connectionResult ? <ResultBox result={connectionResult} /> : null}
          </section>
        </section>

        <section className="request-grid">
          <section className="panel request-builder">
            <PanelTitle eyebrow="Request" title="Business need" />
            <textarea
              value={requestText}
              onChange={(event) => setRequestText(event.target.value)}
              placeholder="Describe the purchase, quantity, cost center, location, needed-by date, and budget."
              aria-label="Requisition request"
            />
            <div className="quick-row">
              {sampleRequests.map((sample) => (
                <button key={sample} type="button" onClick={() => setRequestText(sample)}>
                  {sample.split(',')[0]}
                </button>
              ))}
            </div>
            <button className="primary-button" onClick={createDraft} disabled={!identityReady || isDrafting}>
              {isDrafting ? 'Building controlled draft...' : 'Generate controlled draft'}
            </button>
            {error ? <p className="notice error">{error}</p> : null}
          </section>

          <DraftPanel
            draft={draft}
            blockers={blockers}
            canSubmit={canSubmit}
            isSubmitting={isSubmitting}
            submitResult={submitResult}
            onSubmit={submitToSap}
            sapReady={sapReady}
            identityReady={identityReady}
          />
        </section>
      </section>
    </main>
  );
}

function DraftPanel({ draft, blockers, canSubmit, isSubmitting, submitResult, onSubmit, sapReady, identityReady }) {
  if (!draft) {
    return (
      <section className="panel draft-panel empty-panel">
        <PanelTitle eyebrow="Draft" title="Requisition review" />
        <div className="empty-copy">
          <strong>No draft generated</strong>
          <p>The review packet will show accounting, approval routing, risk flags, and the exact SAP Build context.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="panel draft-panel">
      <div className="draft-heading">
        <PanelTitle eyebrow={`Draft ${draft.requisition_id}`} title={draft.title} />
        <StatusPill tone={riskTone(draft.risk.level)} label={`${draft.risk.level} risk`} />
      </div>

      <div className="metrics-grid">
        <Metric label="Total estimate" value={formatCurrency(draft.total_estimated_value)} />
        <Metric label="Delivery date" value={draft.delivery_date} />
        <Metric label="Cost center" value={draft.account_assignment.cost_center} />
        <Metric label="GL account" value={draft.account_assignment.gl_account} />
        <Metric label="Material group" value={draft.item.material_group} />
        <Metric label="Preferred source" value={draft.sourcing.vendor} />
      </div>

      <div className="section-block">
        <h3>Line items</h3>
        <div className="line-list">
          {draft.items.map((item, index) => (
            <div key={`${item.description}-${index}`} className="line-row">
              <span>{String((index + 1) * 10).padStart(5, '0')}</span>
              <strong>{item.description}</strong>
              <em>Qty {item.quantity}</em>
            </div>
          ))}
        </div>
      </div>

      <div className="section-block">
        <h3>Approval route</h3>
        <div className="route-list">
          {draft.approval_route.map((step) => (
            <span key={step}>{step}</span>
          ))}
        </div>
      </div>

      <div className="alert-stack">
        {draft.risk.flags.map((flag) => (
          <div key={flag} className="alert-line">
            <span />
            {flag}
          </div>
        ))}
        {blockers.map((blocker) => (
          <div key={blocker} className="alert-line blocker">
            <span />
            {blocker}
          </div>
        ))}
      </div>

      {!identityReady ? <p className="notice warn">Resolve employee identity before SAP submission.</p> : null}
      {!sapReady ? <p className="notice warn">SAP connection is not ready.</p> : null}

      <button className="primary-button" onClick={onSubmit} disabled={!canSubmit || isSubmitting}>
        {isSubmitting ? 'Creating SAP workflow...' : 'Create SAP approval workflow'}
      </button>

      {submitResult ? <ResultBox result={submitResult} /> : null}

      <div className="payload-header">
        <h3>SAP Build context</h3>
        <span>Employee and approver fields are included for routing.</span>
      </div>
      <pre>{JSON.stringify(draft.sap_payload, null, 2)}</pre>
    </section>
  );
}

function SapBackendStatus({ sapStatus }) {
  if (!sapStatus) {
    return (
      <div className="backend-status">
        <strong>Checking backend</strong>
        <p>Reading SAP trigger configuration.</p>
      </div>
    );
  }

  if (!sapStatus.configured) {
    return (
      <div className="backend-status warning">
        <strong>Backend configuration incomplete</strong>
        <p>Missing {formatMissing(sapStatus.missing)}.</p>
      </div>
    );
  }

  return (
    <div className="backend-status ready">
      <strong>SAP trigger configured</strong>
      <p>{sapStatus.target_host || 'Configured SAP host'} · {sapStatus.api_path}</p>
    </div>
  );
}

function PanelTitle({ eyebrow, title }) {
  return (
    <div className="panel-title">
      <p className="eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
    </div>
  );
}

function TextField({ label, value, onChange }) {
  return (
    <label className="input-field">
      <span>{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value || '-'}</strong>
    </div>
  );
}

function StatusPill({ tone = 'neutral', label }) {
  return <span className={`status-pill ${tone}`}>{label}</span>;
}

function ResultBox({ result }) {
  return (
    <div className={result.ok ? 'result-box success' : 'result-box warning'}>
      <strong>{formatStatus(result.status)}</strong>
      <p>{result.message || 'Response received from backend.'}</p>
      {result.http_status ? <small>HTTP {result.http_status}</small> : null}
      {result.sap_response ? <pre>{JSON.stringify(result.sap_response, null, 2)}</pre> : null}
      {result.sap_error ? <pre>{result.sap_error}</pre> : null}
      {result.sent_payload_preview ? <pre>{JSON.stringify(result.sent_payload_preview, null, 2)}</pre> : null}
    </div>
  );
}

function isRequesterReady(requester) {
  return [
    requester.employee_id,
    requester.name,
    requester.email,
    requester.department,
    requester.manager_name,
    requester.manager_email
  ].every((value) => String(value || '').trim());
}

function getSapIssue(session, sapStatus) {
  if (sapStatus && !sapStatus.configured) return `Missing ${formatMissing(sapStatus.missing)}.`;
  if (!sapStatus) return 'Backend status is still loading.';
  if (!session.username.trim()) return 'SAP user ID is required.';
  if (sapStatus.auth_type !== 'api_key_bearer' && !session.password) return 'SAP password is required.';
  return '';
}

function getDraftBlockers(draft) {
  if (!draft) return [];
  const blockers = draft.submission_blockers ? [...draft.submission_blockers] : [];
  if (!draft.sap_payload.approver_email) blockers.push('Approver email is required for manager routing.');
  if (!draft.sap_payload.requester_employee_id) blockers.push('Requester employee ID is required.');
  return blockers;
}

function getReadiness({ identityReady, sapReady, draft, blockers }) {
  return [
    {
      label: 'Requester',
      value: identityReady ? 'Ready' : 'Missing',
      detail: identityReady ? 'Employee, department, and manager are resolved.' : 'Complete employee profile fields.',
      tone: identityReady ? 'good' : 'warn'
    },
    {
      label: 'Policy',
      value: draft ? draft.risk.level : 'Pending',
      detail: draft ? `${draft.risk.flags.length} control signal(s) found.` : 'Generate a draft to run checks.',
      tone: draft ? riskTone(draft.risk.level) : 'neutral'
    },
    {
      label: 'Approval',
      value: draft && blockers.length === 0 ? 'Routable' : 'Blocked',
      detail: draft ? 'Manager approver is included in SAP context.' : 'No SAP context yet.',
      tone: draft && blockers.length === 0 ? 'good' : 'warn'
    },
    {
      label: 'SAP',
      value: sapReady ? 'Online' : 'Blocked',
      detail: sapReady ? 'Backend trigger is configured.' : 'SAP session or backend config needs attention.',
      tone: sapReady ? 'good' : 'warn'
    }
  ];
}

function riskTone(level) {
  const normalized = String(level || '').toLowerCase();
  if (normalized === 'low') return 'good';
  if (normalized === 'medium') return 'warn';
  if (normalized === 'high') return 'bad';
  return 'neutral';
}

function formatCurrency(value) {
  const amount = Number(value || 0);
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0
  }).format(amount);
}

function formatMissing(missing = []) {
  if (!missing.length) return 'backend SAP environment variables';
  if (missing.length === 1) return missing[0];
  return `${missing.slice(0, -1).join(', ')} and ${missing[missing.length - 1]}`;
}

function formatStatus(status = '') {
  return String(status || 'status').replaceAll('_', ' ');
}

createRoot(document.getElementById('root')).render(<App />);
