// RecoveryOS dashboard — fetches the §14 panels from /api/dashboard/* (D.1).

const API = "";
const rupee = (paise) =>
  paise == null ? "—" : "₹" + (paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });

const fmt = new Intl.NumberFormat("en-IN");

async function getJSON(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function moneyLabel(paise) {
  if (paise == null) return "—";
  const lakhs = paise / 100000;
  return "₹" + lakhs.toFixed(1) + "L";
}

// Waterfall: §14.1 — stacked columns are hard to read in one pass; a stepped
// bar chart of each stage keeps every number legible (clarity > polish).
function renderWaterfall(wf) {
  const labels = [
    "Revenue at Risk",
    "Expected Natural Recovery",
    "Gross Recovery Opportunity",
    "Incremental Recovery",
    "Communication Cost",
    "Incremental Net Recovery",
  ];
  const values = [
    wf.revenue_at_risk_paise,
    wf.expected_natural_recovery_paise,
    wf.gross_recovery_opportunity_paise,
    wf.incremental_recovery_paise,
    -wf.communication_cost_paise,
    wf.incremental_net_recovery_paise,
  ];
  new Chart(document.getElementById("waterfallChart"), {
    type: "bar",
    data: { labels, datasets: [{ label: "₹ (paise)", data: values, backgroundColor: "#4fd1c5" }] },
    options: {
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: (v) => moneyLabel(v * 100) } } },
    },
  });

  const kpis = [
    ["Incremental Net Recovery", rupee(wf.incremental_net_recovery_paise)],
    ["Gross Recovery Opportunity", moneyLabel(wf.gross_recovery_opportunity_paise)],
    ["Communication Cost", moneyLabel(wf.communication_cost_paise)],
  ];
  document.getElementById("waterfallKpis").innerHTML = kpis
    .map(([l, v]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div></div>`)
    .join("");
}

function renderScorecard(sc) {
  const items = [
    ["Incremental Recovery", rupee(sc.incremental_recovery_paise), "good"],
    ["Net Recovery", rupee(sc.net_recovery_paise), "good"],
    ["Recovery Lift", (sc.recovery_lift_pp ?? 0) + " pp", "good"],
    ["Contacts Avoided", fmt.format(sc.contacts_avoided ?? 0), "good"],
    ["Duplicate Exposure Prevented", moneyLabel(sc.duplicate_exposure_prevented_paise), "good"],
    ["NRPC", rupee(sc.nprc_paise), ""],
    ["Human Escalations", fmt.format(sc.human_escalations ?? 0), ""],
    ["Fraud Blocks", fmt.format(sc.fraud_blocks ?? 0), ""],
    ["Compliance Violations", fmt.format(sc.compliance_violations ?? 0), sc.compliance_violations ? "bad" : ""],
  ];
  document.getElementById("scorecard").innerHTML = items
    .map(([l, v, cls]) => `<div class="kpi ${cls}"><div class="label">${l}</div><div class="value">${v}</div></div>`)
    .join("");

  const b = sc.breakdown || {};
  const bd = [
    ["Recovered", b.recovered || 0],
    ["Pending", b.pending || 0],
    ["Unrecoverable", b.unrecoverable || 0],
    ["Blocked", b.blocked || 0],
    ["Human Review", b.human_review || 0],
  ];
  document.getElementById("scorecardKpis").innerHTML = bd
    .map(([l, v]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${fmt.format(v)}</div></div>`)
    .join("");
}

function renderSegments(segs) {
  new Chart(document.getElementById("segmentChart"), {
    type: "bar",
    data: {
      labels: segs.map((s) => s.segment),
      datasets: [
        {
          label: "Cases",
          data: segs.map((s) => s.case_count),
          backgroundColor: ["#4fd1c5", "#ffd591", "#ff8a80", "#b197fc"],
        },
      ],
    },
    options: { plugins: { legend: { display: false } } },
  });
}

function renderContacts(ca) {
  const reasons = Object.keys(ca.by_reason || {});
  new Chart(document.getElementById("contactsChart"), {
    type: "doughnut",
    data: {
      labels: reasons,
      datasets: [
        {
          data: reasons.map((r) => ca.by_reason[r]),
          backgroundColor: ["#4fd1c5", "#74c0fc", "#ffd591", "#ff8a80", "#b197fc"],
        },
      ],
    },
    options: { plugins: { legend: { position: "bottom" } } },
  });
  const kpis = [
    ["Contacts Avoided", fmt.format(ca.total ?? 0)],
    ["Money Prevented", moneyLabel(ca.money_prevented_paise)],
  ];
  document.getElementById("contactsKpis").innerHTML = kpis
    .map(([l, v]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div></div>`)
    .join("");
}

function renderQueue(items) {
  const tbody = document.querySelector("#exceptionTable tbody");
  if (!items.length) {
    tbody.innerHTML = `<tr class="empty"><td colspan="4">No pending human-review tasks.</td></tr>`;
    return;
  }
  tbody.innerHTML = items
    .map(
      (i) => `<tr>
        <td>${i.case_id}</td>
        <td><span class="chip ${i.priority}">${i.priority}</span></td>
        <td>${i.action || ""}</td>
        <td>${i.reason}</td>
      </tr>`
    )
    .join("");
}

async function loadDashboard() {
  try {
    document.body.classList.add("loading");
    const [wf, sc, segs, ca, queue] = await Promise.all([
      getJSON("/api/dashboard/waterfall"),
      getJSON("/api/dashboard/scorecard"),
      getJSON("/api/dashboard/uplift_segments"),
      getJSON("/api/dashboard/contacts_avoided"),
      getJSON("/api/dashboard/exception_queue"),
    ]);
    renderWaterfall(wf);
    renderScorecard(sc);
    renderSegments(segs);
    renderContacts(ca);
    renderQueue(queue);
    setText("pitchLine", `We recovered ${moneyLabel(wf.incremental_net_recovery_paise)} with fewer unnecessary customer contacts.`);
  } catch (err) {
    document.getElementById("scorecard").innerHTML =
      `<div class="kpi"><div class="label">Could not reach dashboard API</div><div class="value">${err.message}</div></div>`;
  } finally {
    document.body.classList.remove("loading");
  }
}

document.addEventListener("DOMContentLoaded", loadDashboard);
