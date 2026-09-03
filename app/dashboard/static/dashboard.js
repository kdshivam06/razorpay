const API = "";
const fmt = new Intl.NumberFormat("en-IN");
const charts = {};
let dashboardState = null;
let selectedQueueFilter = "all";

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
    tbody.innerHTML = `<tr class="empty"><td colspan="4">No pending human-review tasks for this view.</td></tr>`;
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
      </tr>`
    )
    .join("");

  tbody.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => {
      tbody.querySelectorAll("tr").forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
      renderInspector(filtered[Number(row.dataset.index)]);
    });
  });
  renderInspector(filtered[0]);
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

    const [wf, sc, segs, ca, queue] = await Promise.all([
      getJSON("/api/dashboard/waterfall"),
      getJSON("/api/dashboard/scorecard"),
      getJSON("/api/dashboard/uplift_segments"),
      getJSON("/api/dashboard/contacts_avoided"),
      getJSON("/api/dashboard/exception_queue"),
    ]);

    const liveState = { wf, sc, segs, ca, queue };
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
