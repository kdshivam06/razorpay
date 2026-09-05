const API = "";
const fmt = new Intl.NumberFormat("en-IN");
const charts = {};
let dashboardState = null;
let selectedQueueFilter = "all";
let selectedCaseId = null;

const palette = {
  emerald: "#087f5b",
  mint: "#8ce3bf",
  gold: "#c58918",
  coral: "#ce4a3b",
  indigo: "#4654a3",
  ink: "#17211d",
  muted: "#66746d",
};

const SAMPLE_STATE = {
  wf: {
    revenue_at_risk_paise: 124000000,
    expected_natural_recovery_paise: 45800000,
    gross_recovery_opportunity_paise: 98000000,
    incremental_recovery_paise: 52200000,
    communication_cost_paise: 600000,
    incremental_net_recovery_paise: 51600000,
  },
  sc: {
    incremental_recovery_paise: 52200000,
    net_recovery_paise: 51600000,
    recovery_lift_pp: 17,
    contacts_avoided: 1160,
    duplicate_exposure_prevented_paise: 18400000,
    human_escalations: 42,
    compliance_violations: 0,
    fraud_blocks: 18,
    nprc_paise: 26600,
    breakdown: {
      recovered: 196,
      pending: 188,
      unrecoverable: 34,
      blocked: 26,
      human_review: 42,
    },
    lift: {
      treatment_payment_rate: 0.54,
      control_payment_rate: 0.37,
      lift_pp: 17,
      treatment_cases: 491,
      control_cases: 55,
    },
  },
  segs: [
    { segment: "SURE_THING", case_count: 132, revenue_at_risk_paise: 31800000, incremental_recovery_paise: 2100000 },
    { segment: "PERSUADABLE", case_count: 218, revenue_at_risk_paise: 58400000, incremental_recovery_paise: 38600000 },
    { segment: "LOST_CAUSE", case_count: 116, revenue_at_risk_paise: 22800000, incremental_recovery_paise: 7400000 },
    { segment: "SLEEPING_DOG", case_count: 80, revenue_at_risk_paise: 11000000, incremental_recovery_paise: -900000 },
  ],
  ca: {
    total: 1160,
    money_prevented_paise: 18400000,
    by_reason: {
      "high natural probability": 438,
      "low uplift": 286,
      "customer fatigue": 214,
      dispute: 118,
      "already paid": 104,
    },
  },
  queue: [
    {
      case_id: "case_syn_00187",
      priority: "p1",
      action: "HUMAN_ESCALATION",
      reason: "High-value mandate failure with low confidence classification.",
      amount_paise: 3400000,
    },
    {
      case_id: "case_syn_00342",
      priority: "p2",
      action: "REQUEST_PAYMENT_METHOD_UPDATE",
      reason: "Expired card recovery is allowed, but customer contact fatigue is near limit.",
      amount_paise: 1180000,
    },
    {
      case_id: "case_syn_00491",
      priority: "p0",
      action: "BLOCK",
      reason: "Dispute and fraud signals conflict; no automated outreach permitted.",
      amount_paise: 820000,
    },
  ],
  dataset: {
    dataset_name: "sample_state",
    record_count: 546,
    held_out_labels_matched: 546,
    estimated_records: 0,
    audit_traces: 546,
    message_count: 184,
    human_escalations: 42,
  },
  messages: [
    {
      notification_id: "notif_demo_001",
      channel: "sms",
      template_key: "payment_failed_sms",
      recipient: "+91XXXXXX1234",
      state: "SUCCESS",
      rendered_body: "[Demo Merchant] Your payment of ₹8,400 for order OBL_001 could not be processed. Complete payment: https://rzp.io/i/demo",
    },
  ],
};

const rupee = (paise) =>
  paise == null
    ? "-"
    : "₹" + (paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });

function compactINR(paise) {
  if (paise == null) return "-";
  const value = paise / 100;
  const abs = Math.abs(value);
  if (abs >= 10000000) return `₹${(value / 10000000).toFixed(2)}Cr`;
  if (abs >= 100000) return `₹${(value / 100000).toFixed(2)}L`;
  if (abs >= 1000) return `₹${(value / 1000).toFixed(1)}K`;
  return rupee(paise);
}

async function getJSON(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

async function postText(path, text, contentType = "text/plain") {
  const res = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": contentType },
    body: text,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`POST ${path} -> ${res.status}: ${detail}`);
  }
  return res.json();
}

function destroyChart(id) {
  if (charts[id]) {
    charts[id].destroy();
    delete charts[id];
  }
}

function basePlugins() {
  return {
    legend: {
      labels: {
        color: palette.muted,
        boxWidth: 10,
        boxHeight: 10,
        font: { weight: 700 },
      },
    },
    tooltip: {
      backgroundColor: palette.ink,
      titleColor: "#ffffff",
      bodyColor: "#ffffff",
      padding: 12,
      cornerRadius: 8,
    },
  };
}

function chartOptions(extra = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: basePlugins(),
    scales: {
      x: {
        grid: { display: false },
        ticks: { color: palette.muted, font: { weight: 700 } },
      },
      y: {
        grid: { color: "rgba(102,116,109,0.18)" },
        ticks: { color: palette.muted, callback: (v) => compactINR(v) },
      },
    },
    ...extra,
  };
}

function setStatus(state, label) {
  const status = document.getElementById("apiStatus");
  if (!status) return;
  status.className = `status-pill ${state}`;
  status.innerHTML = `<span></span>${label}`;
}

function metricCard(label, value, sub, accent) {
  return `
    <div class="metric-card" style="--accent:${accent}">
      <div class="label">${label}</div>
      <div class="value">${value}</div>
      <div class="sub">${sub}</div>
    </div>`;
}

function kpi(label, value, cls = "") {
  return `<div class="kpi ${cls}"><div class="label">${label}</div><div class="value">${value}</div></div>`;
}

function renderTopMetrics(wf, sc) {
  const lift = sc.lift || {};
  document.getElementById("topMetrics").innerHTML = [
    metricCard("Revenue at Risk", compactINR(wf.revenue_at_risk_paise), "Monitored exposure", palette.indigo),
    metricCard("Net Recovery", compactINR(wf.incremental_net_recovery_paise), "Incremental after cost", palette.emerald),
    metricCard("Treatment Lift", `${sc.recovery_lift_pp ?? lift.lift_pp ?? 0} pp`, `${lift.treatment_cases ?? 0} treated / ${lift.control_cases ?? 0} control`, palette.gold),
    metricCard("Contacts Avoided", fmt.format(sc.contacts_avoided ?? 0), "Suppressed unnecessary outreach", palette.coral),
  ].join("");
}

function renderDatasetProof(dataset = {}) {
  const el = document.getElementById("datasetProof");
  if (!el) return;
  const matched = dataset.held_out_labels_matched ?? 0;
  const records = dataset.record_count ?? 0;
  const estimated = dataset.estimated_records ?? 0;
  el.innerHTML = [
    proofItem("Dataset", escapeHTML(dataset.dataset_name || "not loaded")),
    proofItem("Rows scored", fmt.format(records)),
    proofItem("Held-out labels", `${fmt.format(matched)} matched`),
    proofItem("Fallback estimates", fmt.format(estimated)),
    proofItem("Audit traces", fmt.format(dataset.audit_traces ?? 0)),
    proofItem("Messages", fmt.format(dataset.message_count ?? 0)),
    proofItem("Human review", fmt.format(dataset.human_escalations ?? 0)),
  ].join("");
}

function proofItem(label, value) {
  return `<div class="proof-item"><span>${label}</span><strong>${value}</strong></div>`;
}

function renderWaterfall(wf) {
  const labels = ["At risk", "Natural", "Gross", "Incremental", "Cost", "Net"];
  const values = [
    wf.revenue_at_risk_paise,
    wf.expected_natural_recovery_paise,
    wf.gross_recovery_opportunity_paise,
    wf.incremental_recovery_paise,
    -Math.abs(wf.communication_cost_paise ?? 0),
    wf.incremental_net_recovery_paise,
  ];

  destroyChart("waterfallChart");
  charts.waterfallChart = new Chart(document.getElementById("waterfallChart"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Expected value",
          data: values,
          borderRadius: 8,
          borderSkipped: false,
          backgroundColor: [palette.indigo, palette.gold, palette.mint, palette.emerald, palette.coral, palette.ink],
        },
      ],
    },
    options: chartOptions({ plugins: { ...basePlugins(), legend: { display: false } } }),
  });

  document.getElementById("waterfallKpis").innerHTML = [
    kpi("Natural Recovery", compactINR(wf.expected_natural_recovery_paise)),
    kpi("Incremental Recovery", compactINR(wf.incremental_recovery_paise), "good"),
    kpi("Communication Cost", compactINR(wf.communication_cost_paise)),
  ].join("");
}

function renderScorecard(sc) {
  const items = [
    ["Incremental Recovery", compactINR(sc.incremental_recovery_paise), "good"],
    ["Net Recovery", compactINR(sc.net_recovery_paise), "good"],
    ["Recovery Lift", `${sc.recovery_lift_pp ?? 0} pp`, "good"],
    ["Duplicate Exposure", compactINR(sc.duplicate_exposure_prevented_paise), "good"],
    ["NRPC", rupee(sc.nprc_paise), ""],
    ["Human Escalations", fmt.format(sc.human_escalations ?? 0), ""],
    ["Fraud Blocks", fmt.format(sc.fraud_blocks ?? 0), ""],
    ["Compliance Violations", fmt.format(sc.compliance_violations ?? 0), sc.compliance_violations ? "bad" : "good"],
  ];
  document.getElementById("scorecard").innerHTML = items
    .map(([label, value, cls]) => kpi(label, value, cls))
    .join("");

  const b = sc.breakdown || {};
  const breakdown = [
    ["Recovered", b.recovered || 0],
    ["Pending", b.pending || 0],
    ["Unrecoverable", b.unrecoverable || 0],
    ["Blocked", b.blocked || 0],
    ["Human Review", b.human_review || 0],
  ];
  document.getElementById("scorecardKpis").innerHTML = breakdown
    .map(([label, value]) => `<div class="breakdown-item"><div class="label">${label}</div><strong>${fmt.format(value)}</strong></div>`)
    .join("");
}

function renderSegments(segs) {
  const normalized = segs?.length ? segs : [
    { segment: "SURE_THING", case_count: 0, revenue_at_risk_paise: 0, incremental_recovery_paise: 0 },
    { segment: "PERSUADABLE", case_count: 0, revenue_at_risk_paise: 0, incremental_recovery_paise: 0 },
    { segment: "LOST_CAUSE", case_count: 0, revenue_at_risk_paise: 0, incremental_recovery_paise: 0 },
    { segment: "SLEEPING_DOG", case_count: 0, revenue_at_risk_paise: 0, incremental_recovery_paise: 0 },
  ];

  destroyChart("segmentChart");
  charts.segmentChart = new Chart(document.getElementById("segmentChart"), {
    type: "bar",
    data: {
      labels: normalized.map((s) => segmentLabel(s.segment)),
      datasets: [
        {
          label: "Cases",
          data: normalized.map((s) => s.case_count),
          borderRadius: 8,
          borderSkipped: false,
          backgroundColor: [palette.emerald, palette.gold, palette.coral, palette.indigo],
        },
        {
          label: "Incremental value",
          data: normalized.map((s) => s.incremental_recovery_paise),
          type: "line",
          yAxisID: "value",
          borderColor: palette.ink,
          backgroundColor: palette.ink,
          tension: 0.35,
        },
      ],
    },
    options: chartOptions({
      scales: {
        x: chartOptions().scales.x,
        y: {
          beginAtZero: true,
          grid: { color: "rgba(102,116,109,0.18)" },
          ticks: { color: palette.muted },
        },
        value: {
          beginAtZero: true,
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: palette.muted, callback: (v) => compactINR(v) },
        },
      },
    }),
  });

  const controls = document.getElementById("segmentControls");
  controls.innerHTML = normalized
    .map((s, index) => `<button type="button" class="${index === 0 ? "active" : ""}" data-segment="${s.segment}">${segmentShort(s.segment)}</button>`)
    .join("");
  controls.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      controls.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      renderSegmentReadout(normalized.find((s) => s.segment === button.dataset.segment));
    });
  });
  renderSegmentReadout(normalized[0]);
}

function renderSegmentReadout(segment) {
  const el = document.getElementById("segmentReadout");
  if (!segment) return;
  const copy = {
    SURE_THING: "High natural-payment probability. The best intervention is often restraint.",
    PERSUADABLE: "Strongest incremental opportunity. Recovery actions should concentrate here.",
    LOST_CAUSE: "Low natural payment and low uplift. Avoid burning contact budget.",
    SLEEPING_DOG: "Intervention can reduce recovery odds. Quiet handling protects revenue and trust.",
  };
  el.innerHTML = `
    <strong>${segmentLabel(segment.segment)} - ${fmt.format(segment.case_count)} cases</strong>
    <p>${copy[segment.segment] || "Segment ready for review."} Incremental value: ${compactINR(segment.incremental_recovery_paise)}.</p>`;
}

function renderContacts(ca) {
  const reasons = Object.keys(ca.by_reason || {});
  const values = reasons.map((r) => ca.by_reason[r]);
  destroyChart("contactsChart");
  charts.contactsChart = new Chart(document.getElementById("contactsChart"), {
    type: "doughnut",
    data: {
      labels: reasons.length ? reasons : ["No suppressed contacts yet"],
      datasets: [
        {
          data: values.length ? values : [1],
          backgroundColor: [palette.emerald, palette.indigo, palette.gold, palette.coral, palette.mint],
          borderColor: "#ffffff",
          borderWidth: 4,
          hoverOffset: 8,
        },
      ],
    },
    options: chartOptions({
      cutout: "68%",
      scales: {},
      plugins: {
        ...basePlugins(),
        legend: { position: "bottom", labels: basePlugins().legend.labels },
      },
    }),
  });

  document.getElementById("contactsKpis").innerHTML = [
    kpi("Contacts Avoided", fmt.format(ca.total ?? 0), "good"),
    kpi("Money Prevented", compactINR(ca.money_prevented_paise), "good"),
  ].join("");
}

function renderQueue(items) {
  const filtered = selectedQueueFilter === "all"
    ? items
    : items.filter((item) => priorityClass(item.priority) === selectedQueueFilter);
  const tbody = document.querySelector("#exceptionTable tbody");
  const inspector = document.getElementById("caseInspector");

  if (!filtered.length) {
    tbody.innerHTML = `<tr class="empty"><td colspan="5">No pending human-review tasks for this view.</td></tr>`;
    inspector.innerHTML = `
      <span class="mini-label">Selected case</span>
      <strong>Queue clear</strong>
      <p>Guardrails are not asking for human attention in the selected priority band.</p>`;
    return;
  }

  tbody.innerHTML = filtered
    .map(
      (item, index) => `<tr data-index="${index}" class="${index === 0 ? "selected" : ""}">
        <td>${escapeHTML(item.case_id)}</td>
        <td><span class="chip ${priorityClass(item.priority)}">${escapeHTML(item.priority)}</span></td>
        <td>${escapeHTML(item.action || "Review")}</td>
        <td>${escapeHTML(item.reason || "")}</td>
        <td><button type="button" class="icon-button inspect-btn" data-case-id="${escapeHTML(item.case_id)}" aria-label="Inspect case ${escapeHTML(item.case_id)}"><i data-lucide="search"></i></button></td>
      </tr>`
    )
    .join("");

  tbody.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest(".inspect-btn")) return;
      tbody.querySelectorAll("tr").forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
      renderInspector(filtered[Number(row.dataset.index)]);
    });
  });

  tbody.querySelectorAll(".inspect-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openInspectorModal(btn.dataset.caseId);
    });
  });

  renderInspector(filtered[0]);
}

function renderMessages(messages = []) {
  const el = document.getElementById("messageOutbox");
  if (!el) return;
  const visible = messages.slice(0, 8);
  if (!visible.length) {
    el.innerHTML = `
      <div class="message-empty">
        <strong>No demo messages sent yet</strong>
        <p>Upload or load a batch with approved SMS, email, WhatsApp, or payment-link actions.</p>
      </div>`;
    return;
  }
  el.innerHTML = visible
    .map(
      (item) => `
        <article class="message-item">
          <div class="message-meta">
            <span class="chip ${String(item.channel || "").toLowerCase()}">${escapeHTML(item.channel || "message")}</span>
            <strong>${escapeHTML(item.state || "SUCCESS")}</strong>
            <small>${escapeHTML(item.notification_id || "")}</small>
          </div>
          <p>${escapeHTML(item.rendered_body || "")}</p>
          <footer>${escapeHTML(item.template_key || "template")} -> ${escapeHTML(item.recipient || "demo recipient")}</footer>
        </article>`
    )
    .join("");
}

function renderInspector(item) {
  document.getElementById("caseInspector").innerHTML = `
    <span class="mini-label">Selected case</span>
    <strong>${escapeHTML(item.case_id)}</strong>
    <p>${escapeHTML(item.reason || "Awaiting reviewer context.")}</p>
    <dl>
      <div><dt>Priority</dt><dd>${escapeHTML(item.priority)}</dd></div>
      <div><dt>Proposed action</dt><dd>${escapeHTML(item.action || "Review")}</dd></div>
      <div><dt>Amount</dt><dd>${compactINR(item.amount_paise)}</dd></div>
    </dl>`;
}

function openInspectorModal(caseId) {
  selectedCaseId = caseId;
  const modal = document.getElementById("inspectorModal");
  const drawer = document.getElementById("inspectorDrawer");
  const title = document.getElementById("inspectorCaseId");
  const body = document.getElementById("inspectorBody");

  title.textContent = caseId;
  body.innerHTML = '<div class="loading-placeholder">Loading decision packet…</div>';
  modal.hidden = false;
  requestAnimationFrame(() => {
    modal.classList.add("open");
    drawer.classList.add("open");
  });
  fetchDecisionPacket(caseId);
}

function closeInspectorModal() {
  const modal = document.getElementById("inspectorModal");
  const drawer = document.getElementById("inspectorDrawer");
  modal.classList.remove("open");
  drawer.classList.remove("open");
  setTimeout(() => {
    modal.hidden = true;
  }, 200);
  selectedCaseId = null;
}

async function fetchDecisionPacket(caseId) {
  try {
    const data = await getJSON(`/api/cases/${encodeURIComponent(caseId)}/decision-packet`);
    renderDecisionPacket(data);
  } catch (err) {
    document.getElementById("inspectorBody").innerHTML = `
      <div class="error-banner">
        Failed to load decision packet: ${escapeHTML(err.message)}
      </div>`;
  }
}

function renderDecisionPacket(data) {
  const body = document.getElementById("inspectorBody");
  if (!data) {
    body.innerHTML = '<div class="error-banner">No decision packet data returned</div>';
    return;
  }

  const requiresHuman = data.recommendation?.requires_human === true;
  const action = data.recommendation?.action || "NO_ACTION";
  const confidence = data.recommendation?.confidence ?? 0;
  const reasoning = data.recommendation?.reasoning || "No reasoning provided";

  const ingestion = data.ingestion || {};
  const diagnosis = data.diagnosis || {};
  const policyGates = data.policy_gates || [];
  const settlement = data.settlement_projection || {};
  const channelDrafts = data.channel_drafts || {};

  body.innerHTML = `
    <!-- Header banner -->
    <div class="inspector-header-banner" style="--accent:${requiresHuman ? "var(--coral)" : "var(--emerald)"}">
      <div class="header-main">
        <div class="header-amount">${compactINR(data.amount)}</div>
        <div class="header-meta">
          <span class="chip ${requiresHuman ? "chip-high" : "chip-low"}">${escapeHTML(data.status || "UNKNOWN")}</span>
          <span class="quiet">Trace: ${escapeHTML(data.trace_id || "—")}</span>
        </div>
      </div>
      <div class="header-badge ${requiresHuman ? "badge-human" : "badge-auto"}">
        <i data-lucide="${requiresHuman ? "user-check" : "zap"}"></i>
        <span>${requiresHuman ? "Human Required" : "Auto-Resolve"}</span>
      </div>
    </div>

    <div class="inspector-grid">
      <!-- Ingestion Signal Card -->
      <article class="surface card">
        <header class="card-header">
          <i data-lucide="radio"></i>
          <strong>Ingestion Signal</strong>
        </header>
        <dl class="fact-list">
          <div><dt>Raw failure reason</dt><dd>${escapeHTML(ingestion.raw_failure_reason || "—")}</dd></div>
          <div><dt>Decline code</dt><dd><code>${escapeHTML(ingestion.decline_code || "—")}</code></dd></div>
          <div><dt>Source</dt><dd>${escapeHTML(ingestion.source || "—")}</dd></div>
        </dl>
      </article>

      <!-- Diagnosis Card -->
      <article class="surface card">
        <header class="card-header">
          <i data-lucide="stethoscope"></i>
          <strong>Diagnosis</strong>
        </header>
        <dl class="fact-list">
          <div><dt>Fault attribution</dt><dd>${escapeHTML(diagnosis.fault_attribution || "—")}</dd></div>
          <div><dt>Taxonomy code</dt><dd><code>${escapeHTML(diagnosis.taxonomy_code || "—")}</code></dd></div>
          <div><dt>Taxonomy label</dt><dd>${escapeHTML(diagnosis.taxonomy_label || "—")}</dd></div>
          <div class="pending-field">
            <dt>Rationale</dt>
            <dd class="pending-badge">${escapeHTML(diagnosis.rationale_text?.pending || "E.3 — diagnosis rationale engine")}</dd>
          </div>
        </dl>
      </article>

      <!-- Policy Gate Checklist Card -->
      <article class="surface card policy-gates-card">
        <header class="card-header">
          <i data-lucide="scale"></i>
          <strong>Policy Gate Checklist</strong>
        </header>
        <div class="gate-list" id="policyGateList">
          ${renderPolicyGates(policyGates)}
        </div>
      </article>

      <!-- Settlement Projection Card -->
      <article class="surface card">
        <header class="card-header">
          <i data-lucide="calculator"></i>
          <strong>Settlement Projection</strong>
        </header>
        <dl class="fact-list">
          <div><dt>Gross</dt><dd>${compactINR(settlement.gross)}</dd></div>
          <div><dt>MDR fee</dt><dd>${compactINR(settlement.mdr_fee)}</dd></div>
          <div><dt>GST on fee</dt><dd>${compactINR(settlement.gst_on_fee)}</dd></div>
          <div class="total-row"><dt>Net yield</dt><dd>${compactINR(settlement.net_yield)}</dd></div>
          <div><dt>Expected date</dt><dd>${escapeHTML(settlement.expected_date || "—")}</dd></div>
        </dl>
      </article>

      <!-- Recommendation Banner -->
      <article class="surface card recommendation-banner ${requiresHuman ? "requires-human" : "auto-resolve"}">
        <header class="card-header">
          <i data-lucide="${requiresHuman ? "user-check" : "zap"}"></i>
          <strong>Recommendation</strong>
        </header>
        <div class="recommendation-main">
          <div class="action-badge ${requiresHuman ? "human" : "auto"}">${escapeHTML(action)}</div>
          <div class="recommendation-details">
            <div class="confidence">
              <span class="label">Confidence</span>
              <div class="score-meter" style="--score:${Math.max(0, Math.min(1, confidence)) * 100}%"><span></span></div>
              <span class="value">${(confidence * 100).toFixed(0)}%</span>
            </div>
            <p class="reasoning">${escapeHTML(reasoning)}</p>
          </div>
        </div>
      </article>

      <!-- Channel Drafts (E.6 - placeholder) -->
      <article class="surface card empty-section">
        <header class="card-header">
          <i data-lucide="message-square"></i>
          <strong>Channel Drafts <span class="coming-soon">(E.6)</span></strong>
        </header>
        <div class="draft-grid">
          <div class="draft-placeholder">
            <i data-lucide="smartphone"></i>
            <span>SMS</span>
            <small class="pending-badge">${escapeHTML(channelDrafts.sms?.pending || "E.6 — channel template engine")}</small>
          </div>
          <div class="draft-placeholder">
            <i data-lucide="message-circle"></i>
            <span>WhatsApp</span>
            <small class="pending-badge">${escapeHTML(channelDrafts.whatsapp?.pending || "E.6 — channel template engine")}</small>
          </div>
          <div class="draft-placeholder">
            <i data-lucide="mail"></i>
            <span>Email</span>
            <small class="pending-badge">${escapeHTML(channelDrafts.email?.pending || "E.6 — channel template engine")}</small>
          </div>
          <div class="draft-placeholder">
            <i data-lucide="mic"></i>
            <span>Voice script</span>
            <small class="pending-badge">${escapeHTML(channelDrafts.voice_script?.pending || "E.6 — channel template engine")}</small>
          </div>
        </div>
      </article>

      <!-- Action Panel (E.7 - placeholder) -->
      <article class="surface card empty-section">
        <header class="card-header">
          <i data-lucide="play-circle"></i>
          <strong>Action Panel <span class="coming-soon">(E.7)</span></strong>
        </header>
        <div class="empty-placeholder">
          <i data-lucide="clock"></i>
          <p>Action execution, confirmation, and audit trail will appear here in E.7</p>
        </div>
      </article>
    </div>
  `;

  if (window.lucide) window.lucide.createIcons();
}

function renderPolicyGates(gates) {
  if (!gates || !gates.length) {
    return '<div class="no-gates">No policy gates recorded</div>';
  }
  return gates.map(gate => `
    <div class="gate-item ${gate.result === "PASS" ? "pass" : gate.result === "FAIL" ? "fail" : "unknown"}">
      <div class="gate-info">
        <span class="gate-name">${escapeHTML(gate.gate_name)}</span>
        ${gate.reason ? `<span class="gate-reason">${escapeHTML(gate.reason)}</span>` : ""}
      </div>
      <span class="gate-pill ${gate.result === "PASS" ? "pill-pass" : gate.result === "FAIL" ? "pill-fail" : "pill-unknown"}">
        ${gate.result === "PASS" ? "PASS" : gate.result === "FAIL" ? "FAIL" : gate.result}
      </span>
    </div>
  `).join("");
}

function initControls() {
  document.querySelectorAll(".mode-switch button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".mode-switch button").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      setMode(button.dataset.mode);
    });
  });

  document.querySelectorAll("#queueFilters button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("#queueFilters button").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      selectedQueueFilter = button.dataset.priority;
      if (dashboardState) renderQueue(dashboardState.queue);
    });
  });

  document.getElementById("refreshButton")?.addEventListener("click", loadDashboard);
  document.getElementById("batchFile")?.addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    document.getElementById("fileName").textContent = file ? file.name : "No file selected";
  });
  document.getElementById("uploadButton")?.addEventListener("click", uploadSelectedBatch);
  document.getElementById("syntheticButton")?.addEventListener("click", loadSyntheticBatch);
  document.getElementById("realtimeButton")?.addEventListener("click", injectRealtimeEvent);

  // Modal/drawer controls
  document.getElementById("closeInspector")?.addEventListener("click", closeInspectorModal);
  document.getElementById("inspectorModal")?.addEventListener("click", (e) => {
    if (e.target === document.getElementById("inspectorModal")) closeInspectorModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeInspectorModal();
  });
}

function setMode(mode) {
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    const modes = panel.dataset.panel.split(" ");
    panel.style.display = modes.includes(mode) ? "" : "none";
  });
}

async function loadDashboard() {
  const refresh = document.getElementById("refreshButton");
  try {
    document.body.classList.add("loading");
    refresh?.classList.add("spinning");
    setStatus("", "Syncing");

    const [wf, sc, segs, ca, queue, dataset, messages] = await Promise.all([
      getJSON("/api/dashboard/waterfall"),
      getJSON("/api/dashboard/scorecard"),
      getJSON("/api/dashboard/uplift_segments"),
      getJSON("/api/dashboard/contacts_avoided"),
      getJSON("/api/dashboard/exception_queue"),
      getJSON("/api/dashboard/dataset"),
      getJSON("/api/dashboard/messages"),
    ]);

    const liveState = { wf, sc, segs, ca, queue, dataset, messages };
    const state = hasDashboardSignal(liveState) ? liveState : SAMPLE_STATE;
    dashboardState = state;
    if (state === SAMPLE_STATE) {
      setStatus("", "Demo seed");
    }
    renderTopMetrics(state.wf, state.sc);
    renderWaterfall(state.wf);
    renderScorecard(state.sc);
    renderSegments(state.segs);
    renderContacts(state.ca);
    renderQueue(state.queue);
    renderDatasetProof(state.dataset);
    renderMessages(state.messages);
    document.getElementById("pitchLine").textContent =
      `${state === SAMPLE_STATE ? "Demo seed: " : ""}Recovered ${compactINR(state.wf.incremental_net_recovery_paise)} net incremental value while avoiding ${fmt.format(state.sc.contacts_avoided ?? 0)} unnecessary contacts.`;
    document.getElementById("lastUpdated").textContent = `Updated ${new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`;
    if (state !== SAMPLE_STATE) setStatus("ready", "Live");
  } catch (err) {
    setStatus("error", "API offline");
    renderOffline(err);
  } finally {
    document.body.classList.remove("loading");
    refresh?.classList.remove("spinning");
    if (window.lucide) window.lucide.createIcons();
  }
}

async function uploadSelectedBatch() {
  const input = document.getElementById("batchFile");
  const file = input?.files?.[0];
  if (!file) {
    setStatus("error", "Choose CSV");
    return;
  }
  try {
    document.body.classList.add("loading");
    setStatus("", "Uploading");
    const text = await file.text();
    await postText(`/api/dashboard/upload?filename=${encodeURIComponent(file.name)}`, text, "text/csv");
    await loadDashboard();
  } catch (err) {
    setStatus("error", "Upload failed");
    renderOffline(err);
  } finally {
    document.body.classList.remove("loading");
  }
}

async function loadSyntheticBatch() {
  try {
    document.body.classList.add("loading");
    setStatus("", "Loading batch");
    await postText("/api/dashboard/load-synthetic", "", "text/plain");
    await loadDashboard();
  } catch (err) {
    setStatus("error", "Batch failed");
    renderOffline(err);
  } finally {
    document.body.classList.remove("loading");
  }
}

async function injectRealtimeEvent() {
  const id = Date.now().toString().slice(-8);
  const event = {
    event_id: `evt_live_${id}`,
    case_id: `case_live_${id}`,
    event_type: "payment.failed",
    customer_id: `cus_live_${id}`,
    obligation_id: `obl_live_${id}`,
    obligation_type: "payment",
    amount_paise: 1845000,
    outstanding_amount_paise: 1845000,
    currency: "INR",
    payment_method: "upi",
    failure_reason: "checkout_abandoned",
    root_cause: "checkout_abandoned",
    created_at: Math.floor(Date.now() / 1000),
    persona: "P7",
    channel_preference: "WHATSAPP",
    contact_count_7d: 0,
  };
  try {
    document.body.classList.add("loading");
    setStatus("", "Streaming");
    await postText("/api/dashboard/realtime-event", JSON.stringify(event), "application/json");
    await loadDashboard();
  } catch (err) {
    setStatus("error", "Stream failed");
    renderOffline(err);
  } finally {
    document.body.classList.remove("loading");
  }
}

function hasDashboardSignal(state) {
  const wf = state.wf || {};
  const sc = state.sc || {};
  return Boolean(
    wf.revenue_at_risk_paise ||
    wf.incremental_net_recovery_paise ||
    sc.contacts_avoided ||
    state.queue?.length ||
    state.segs?.some((segment) => segment.case_count || segment.incremental_recovery_paise)
  );
}

function renderOffline(err) {
  document.getElementById("topMetrics").innerHTML = metricCard("Dashboard API", "Offline", err.message, palette.coral);
  document.getElementById("scorecard").innerHTML = `<div class="error-banner">${escapeHTML(err.message)}</div>`;
  document.getElementById("waterfallKpis").innerHTML = "";
  document.getElementById("scorecardKpis").innerHTML = "";
  renderDatasetProof({});
  renderMessages([]);
  renderWaterfall({
    revenue_at_risk_paise: 0,
    expected_natural_recovery_paise: 0,
    gross_recovery_opportunity_paise: 0,
    incremental_recovery_paise: 0,
    communication_cost_paise: 0,
    incremental_net_recovery_paise: 0,
  });
  renderSegments([]);
  renderContacts({ total: 0, by_reason: {}, money_prevented_paise: 0 });
  renderQueue([]);
}

function segmentLabel(value) {
  return String(value || "").split("_").map(capitalize).join(" ");
}

function segmentShort(value) {
  return String(value || "")
    .replace("SURE_THING", "Sure")
    .replace("PERSUADABLE", "Persuade")
    .replace("LOST_CAUSE", "Lost")
    .replace("SLEEPING_DOG", "Sleep");
}

function priorityClass(priority) {
  const raw = String(priority || "").toLowerCase();
  if (["p0", "p1", "p2", "p3", "p4"].includes(raw)) return raw;
  if (raw.includes("high")) return "high";
  if (raw.includes("medium")) return "medium";
  if (raw.includes("low")) return "low";
  return "medium";
}

function capitalize(value) {
  return value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

document.addEventListener("DOMContentLoaded", () => {
  initControls();
  setMode("executive");
  if (window.lucide) window.lucide.createIcons();
  loadDashboard();
});
