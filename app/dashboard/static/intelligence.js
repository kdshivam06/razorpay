const API = "";
const fmt = new Intl.NumberFormat("en-IN");
let cases = [];
let activePlaybook = "all";
let activeSegment = "all";

const rupee = (paise) =>
  paise == null
    ? "-"
    : "₹" + (paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });

function setStatus(state, label) {
  const status = document.getElementById("apiStatus");
  if (!status) return;
  status.className = `status-pill ${state}`;
  status.innerHTML = `<span></span>${label}`;
}

async function getJSON(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

async function loadCases() {
  const refresh = document.getElementById("refreshButton");
  try {
    refresh?.classList.add("spinning");
    setStatus("", "Syncing");
    cases = await getJSON("/api/dashboard/cases?limit=500");
    const dataset = await getJSON("/api/dashboard/dataset");
    document.getElementById("intelPitch").textContent =
      `${fmt.format(dataset.record_count || cases.length)} rows scored, ${fmt.format(dataset.audit_traces || cases.length)} audit traces, ${fmt.format(dataset.message_count || 0)} rendered messages, ${fmt.format(dataset.human_escalations || 0)} human escalations.`;
    renderCases();
    setStatus("ready", "Live");
  } catch (err) {
    setStatus("error", "API offline");
    document.querySelector("#caseTable tbody").innerHTML =
      `<tr class="empty"><td colspan="6">${escapeHTML(err.message)}</td></tr>`;
  } finally {
    refresh?.classList.remove("spinning");
    if (window.lucide) window.lucide.createIcons();
  }
}

function renderCases() {
  const tbody = document.querySelector("#caseTable tbody");
  const filtered = filteredCases();
  document.getElementById("caseCount").textContent = `${fmt.format(filtered.length)} cases`;
  if (!filtered.length) {
    tbody.innerHTML = `<tr class="empty"><td colspan="6">No cases match this filter.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered
    .map((item, index) => {
      const score = Math.round((item.predicted_recovery_probability || 0) * 100);
      return `<tr data-index="${index}" class="${index === 0 ? "selected" : ""}">
        <td>${escapeHTML(item.case_id)}</td>
        <td>${escapeHTML(cleanLabel(item.root_cause))}</td>
        <td><span class="chip ${segmentClass(item.uplift_segment)}">${escapeHTML(cleanLabel(item.uplift_segment))}</span></td>
        <td><div class="score-meter"><span style="width:${score}%"></span></div><strong>${score}%</strong></td>
        <td>${escapeHTML(cleanLabel(item.selected_action))}</td>
        <td><span class="chip ${item.policy_gate_result === "BLOCKED" ? "p0" : "p4"}">${escapeHTML(item.policy_gate_result)}</span></td>
      </tr>`;
    })
    .join("");

  tbody.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => {
      tbody.querySelectorAll("tr").forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
      renderDetail(filtered[Number(row.dataset.index)]);
    });
  });
  renderDetail(filtered[0]);
}

function renderDetail(item) {
  if (!item) return;
  const natural = Math.round((item.natural_payment_probability || 0) * 100);
  const recovered = Math.round((item.predicted_recovery_probability || 0) * 100);
  document.getElementById("caseDetail").innerHTML = `
    <div class="surface-header">
      <div>
        <p class="eyebrow">Decision trace</p>
        <h2>${escapeHTML(item.case_id)}</h2>
      </div>
      <i data-lucide="${item.policy_gate_result === "BLOCKED" ? "ban" : "send"}"></i>
    </div>
    <div class="decision-stack">
      ${traceStep("Root cause", cleanLabel(item.root_cause), "scan-search")}
      ${traceStep("Person type", cleanLabel(item.uplift_segment), "users")}
      ${traceStep("Natural pay", `${natural}%`, "timer")}
      ${traceStep("After action", `${recovered}%`, "trending-up")}
      ${traceStep("Recommended", cleanLabel(item.selected_action), "send-horizontal")}
      ${traceStep("Policy", item.policy_gate_result, item.policy_gate_result === "BLOCKED" ? "shield-x" : "shield-check")}
    </div>
    <div class="action-plan">
      <span class="mini-label">Playbook</span>
      <strong>${escapeHTML(item.playbook?.name || "Recovery workflow")}</strong>
      <p>${escapeHTML(item.playbook?.path || "")}</p>
    </div>
    <div class="action-plan">
      <span class="mini-label">Why this action</span>
      <p>${escapeHTML(item.why || "Selected by uplift, economics, and policy gates.")}</p>
    </div>
    <div class="action-plan next-step">
      <span class="mini-label">AI recovery plan</span>
      <strong>${escapeHTML(item.next_step || "Execute the selected bounded workflow.")}</strong>
      <p>${escapeHTML(item.automation_proof?.payment_link || item.automation_proof?.voice_call || item.automation_proof?.reminder || "Audit trace and idempotency proof recorded.")}</p>
    </div>
    ${renderHumanInstruction(item.human_instruction)}
    ${renderPtp(item.ptp)}
    ${renderConversation(item.conversation)}
    <dl class="intel-facts">
      <div><dt>Amount at risk</dt><dd>${rupee(item.amount_paise)}</dd></div>
      <div><dt>Model top action</dt><dd>${escapeHTML(cleanLabel(item.top_model_action))} (${Math.round((item.top_model_uplift || 0) * 100)} pp uplift)</dd></div>
      <div><dt>Message</dt><dd>${item.message_sent ? "Rendered in outbox" : "Not sent or suppressed"}</dd></div>
      <div><dt>Failed checks</dt><dd>${escapeHTML((item.policy_checks_failed || []).join("; ") || "None")}</dd></div>
      <div><dt>Outcome</dt><dd>${escapeHTML(cleanLabel(item.outcome))}</dd></div>
      <div><dt>Confidence</dt><dd>${Math.round((item.audit_summary?.confidence_probability || 0) * 100)}% ${escapeHTML(item.audit_summary?.confidence_tier || "")}</dd></div>
    </dl>`;
  if (window.lucide) window.lucide.createIcons();
}

function renderHumanInstruction(instruction = {}) {
  const required = instruction.required === true;
  const checklist = instruction.checklist || [];
  return `
    <div class="human-box ${required ? "required" : "auto"}">
      <div>
        <span class="mini-label">${required ? "Human intervention" : "Human intervention"}</span>
        <strong>${required ? "Required" : "Not required"}</strong>
        <p>${escapeHTML(instruction.instruction || "")}</p>
      </div>
      <ul>${checklist.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
    </div>`;
}

function renderPtp(ptp = {}) {
  if (!ptp || ptp.status === "not_applicable") {
    return `<div class="action-plan"><span class="mini-label">Promise to pay</span><p>No PTP signal for this case.</p></div>`;
  }
  return `
    <div class="ptp-card">
      <span class="mini-label">Promise to pay</span>
      <strong>${escapeHTML(cleanLabel(ptp.status))}</strong>
      <p>${ptp.promise_date ? `Promise date ${escapeHTML(ptp.promise_date)} for ${rupee(ptp.promise_amount_paise)}.` : "Customer response tracked for next action."}</p>
      <dl class="intel-facts slim">
        <div><dt>Reliability</dt><dd>${Math.round((ptp.reliability || 0) * 100)}%</dd></div>
        <div><dt>Reminder</dt><dd>${ptp.reminder_scheduled ? `Scheduled on ${escapeHTML(ptp.reminder_channel || "preferred channel")}` : "Not scheduled"}</dd></div>
      </dl>
    </div>`;
}

function renderConversation(conversation = []) {
  if (!conversation.length) return "";
  return `
    <div class="conversation-card">
      <span class="mini-label">Conversation proof</span>
      ${conversation.map((turn) => `
        <div class="turn">
          <strong>${escapeHTML(turn.speaker || "System")}</strong>
          <span>${escapeHTML(turn.channel || "event")}</span>
          <p>${escapeHTML(turn.text || "")}</p>
        </div>`).join("")}
    </div>`;
}

function traceStep(label, value, icon) {
  return `<div class="trace-step">
    <i data-lucide="${icon}"></i>
    <span>${label}</span>
    <strong>${escapeHTML(value)}</strong>
  </div>`;
}

function filteredCases() {
  const q = document.getElementById("caseSearch").value.trim().toLowerCase();
  return cases.filter((item) => {
    const playbookOk = activePlaybook === "all" || item.playbook?.name === activePlaybook;
    const segmentOk = activeSegment === "all" || item.uplift_segment === activeSegment;
    const haystack = [
      item.case_id,
      item.root_cause,
      item.uplift_segment,
      item.selected_action,
      item.policy_gate_result,
      item.playbook?.name,
    ]
      .join(" ")
      .toLowerCase();
    return playbookOk && segmentOk && (!q || haystack.includes(q));
  });
}

function initControls() {
  document.getElementById("refreshButton")?.addEventListener("click", loadCases);
  document.getElementById("caseSearch")?.addEventListener("input", renderCases);
  document.querySelectorAll(".playbook-card").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".playbook-card").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      activePlaybook = button.dataset.playbook;
      renderCases();
    });
  });
  document.querySelectorAll("#segmentFilter button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("#segmentFilter button").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      activeSegment = button.dataset.segment;
      renderCases();
    });
  });
}

function cleanLabel(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function segmentClass(value) {
  return {
    PERSUADABLE: "p4",
    SURE_THING: "sms",
    LOST_CAUSE: "p2",
    SLEEPING_DOG: "p0",
  }[value] || "p3";
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
  if (window.lucide) window.lucide.createIcons();
  loadCases();
});
