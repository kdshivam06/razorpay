const API = "";
const fmt = new Intl.NumberFormat("en-IN");
let cases = [];
let activePlaybook = "all";
let activeSegment = "all";
let selectedCase = null;
let selectedPacket = null;

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
      const item = filtered[Number(row.dataset.index)];
      renderDetail(item);
      openCaseModal(item);
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

async function openCaseModal(item) {
  selectedCase = item;
  selectedPacket = null;
  const modal = document.getElementById("caseModal");
  const drawer = document.getElementById("caseDrawer");
  const title = document.getElementById("caseModalTitle");
  const body = document.getElementById("caseModalBody");
  title.textContent = item.case_id;
  body.innerHTML = `<div class="loading-placeholder">Loading decision packet and action controls...</div>`;
  modal.hidden = false;
  requestAnimationFrame(() => {
    modal.classList.add("open");
    drawer.classList.add("open");
  });

  try {
    selectedPacket = await getJSON(`/api/cases/${encodeURIComponent(item.case_id)}/decision-packet`);
  } catch (err) {
    selectedPacket = null;
  }
  renderCaseModal();
}

function closeCaseModal() {
  const modal = document.getElementById("caseModal");
  const drawer = document.getElementById("caseDrawer");
  modal.classList.remove("open");
  drawer.classList.remove("open");
  setTimeout(() => {
    modal.hidden = true;
  }, 180);
}

function renderCaseModal(extra = "") {
  const item = selectedCase;
  const packet = selectedPacket || {};
  const body = document.getElementById("caseModalBody");
  if (!item || !body) return;

  const diagnosis = packet.diagnosis || {};
  const recommendation = packet.recommendation || {};
  const drafts = packet.channel_drafts || {};
  const ptp = item.ptp || {};
  const paymentLink = item.automation_proof?.payment_link || `https://rzp.io/i/demo-${item.case_id.slice(-6)}`;
  const recoveryScore = Math.round((item.predicted_recovery_probability || 0) * 100);
  const naturalScore = Math.round((item.natural_payment_probability || 0) * 100);
  const requiresHuman = item.human_instruction?.required === true || recommendation.requires_human === true;

  body.innerHTML = `
    <div class="case-modal-hero">
      <div>
        <p class="eyebrow">Transaction decision packet</p>
        <h2>${escapeHTML(item.case_id)}</h2>
        <p>${escapeHTML(packet.case_narrative || item.why || "AI scored this case and selected the safest recovery path.")}</p>
      </div>
      <div class="case-modal-money">
        <span>Amount at risk</span>
        <strong>${rupee(item.amount_paise)}</strong>
        <a href="${escapeHTML(paymentLink)}" target="_blank" rel="noopener noreferrer">Razorpay demo link</a>
      </div>
    </div>

    <div class="modal-kpi-grid">
      ${modalKpi("Failure cause", cleanLabel(diagnosis.taxonomy_label || item.root_cause), "scan-search")}
      ${modalKpi("Natural pay", `${naturalScore}%`, "timer")}
      ${modalKpi("Predicted recovery", `${recoveryScore}%`, "trending-up")}
      ${modalKpi("Person type", cleanLabel(item.uplift_segment), "users")}
      ${modalKpi("AI action", cleanLabel(item.selected_action), "bot")}
      ${modalKpi("Human needed", requiresHuman ? "Yes" : "No", requiresHuman ? "user-check" : "zap")}
    </div>

    <section class="modal-section">
      <div class="section-title"><i data-lucide="brain-circuit"></i><strong>AI overview: what to do and what not to do</strong></div>
      <div class="ai-do-dont">
        <div>
          <span class="mini-label">Do</span>
          <p>${escapeHTML(item.next_step || "Execute the selected bounded recovery workflow.")}</p>
        </div>
        <div>
          <span class="mini-label">Do not</span>
          <p>${escapeHTML(doNotCopy(item))}</p>
        </div>
      </div>
    </section>

    <section class="modal-section">
      <div class="section-title"><i data-lucide="message-square-text"></i><strong>Outbound actions and exact drafts</strong></div>
      <div class="quick-actions">
        <button type="button" class="command-button small modal-action" data-action="SEND_SMS" data-channel="sms">Send SMS</button>
        <button type="button" class="command-button small modal-action" data-action="SEND_EMAIL" data-channel="email">Send Email</button>
        <button type="button" class="command-button small modal-action" data-action="SEND_WHATSAPP" data-channel="whatsapp">Send WhatsApp</button>
        <button type="button" class="command-button small modal-payment-link">Create Razorpay Link</button>
        <button type="button" class="command-button small modal-voice">AI Voice Call</button>
        <button type="button" class="command-button small modal-action" data-action="CREATE_PTP">Create PTP</button>
      </div>
      <div class="modal-action-result" id="modalActionResult">${extra}</div>
      <div class="draft-grid">${renderModalDrafts(drafts)}</div>
    </section>

    <section class="modal-section">
      <div class="section-title"><i data-lucide="phone-call"></i><strong>Conversation, voice call, and customer reply</strong></div>
      ${renderConversation(item.conversation)}
      ${renderUserReply(item)}
    </section>

    <section class="modal-section">
      <div class="section-title"><i data-lucide="calendar-check"></i><strong>Promise-to-pay and reminder deadline</strong></div>
      ${renderPtp(ptp)}
    </section>

    <section class="modal-section">
      <div class="section-title"><i data-lucide="shield-check"></i><strong>Policy and audit proof</strong></div>
      ${renderHumanInstruction(item.human_instruction)}
      <dl class="intel-facts">
        <div><dt>Policy</dt><dd>${escapeHTML(item.policy_gate_result)}</dd></div>
        <div><dt>Failed checks</dt><dd>${escapeHTML((item.policy_checks_failed || []).join("; ") || "None")}</dd></div>
        <div><dt>Trace key</dt><dd>${escapeHTML(item.audit_summary?.trace_id || "-")}</dd></div>
        <div><dt>Models</dt><dd>${escapeHTML(Object.values(item.audit_summary?.model_versions || {}).join(", "))}</dd></div>
      </dl>
    </section>`;
  if (window.lucide) window.lucide.createIcons();
}

function modalKpi(label, value, icon) {
  return `<div class="trace-step"><i data-lucide="${icon}"></i><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`;
}

function doNotCopy(item) {
  if (item.policy_gate_result === "BLOCKED") return "Do not contact or retry; this case is blocked by policy.";
  if (item.uplift_segment === "SURE_THING") return "Do not spend contact budget on a likely natural payer.";
  if (item.uplift_segment === "SLEEPING_DOG") return "Do not nudge; intervention can reduce recovery probability.";
  if (item.selected_action === "SEND_PAYMENT_LINK") return "Do not send duplicate links before reconciliation checks.";
  return "Do not let free-form LLM text change amount, due date, policy gate, or execution path.";
}

function renderModalDrafts(drafts = {}) {
  const rows = [
    ["sms", "SMS", "smartphone"],
    ["whatsapp", "WhatsApp", "message-circle"],
    ["email", "Email", "mail"],
    ["voice_script", "AI Voice Script", "mic"],
  ];
  return rows.map(([key, label, icon]) => {
    const draft = pickDraft(drafts[key]);
    return `<article class="draft-editor-block">
      <div class="draft-editor-head"><i data-lucide="${icon}"></i><strong>${label}</strong></div>
      ${draft.subject ? `<label class="draft-field"><span>Subject</span><input readonly value="${escapeHTML(draft.subject)}" /></label>` : ""}
      <textarea class="draft-textarea" rows="${key === "voice_script" ? 5 : 4}" readonly>${escapeHTML(draft.body || "Draft unavailable until decision packet is loaded.")}</textarea>
    </article>`;
  }).join("");
}

function pickDraft(raw) {
  if (!raw || raw.pending) return {};
  if (typeof raw === "string") return { body: raw };
  if (typeof raw.en === "string") return { body: raw.en };
  if (raw.en?.body || raw.en?.subject) return raw.en;
  if (typeof raw["hi-en"] === "string") return { body: raw["hi-en"] };
  if (raw["hi-en"]?.body || raw["hi-en"]?.subject) return raw["hi-en"];
  return {};
}

function renderUserReply(item) {
  const ptp = item.ptp || {};
  const reply = ptp.status === "promised"
    ? `Customer reply: "Main ${ptp.promise_date} tak ${rupee(ptp.promise_amount_paise)} pay kar dunga. Reminder bhej dena."`
    : ptp.status === "unable_to_pay"
      ? 'Customer reply: "Abhi pay nahi ho paayega. Please support team se baat karwa do."'
      : item.message_sent
        ? 'Customer reply: "Please send payment link again."'
        : "No customer reply yet because outreach was suppressed or waiting.";
  return `<div class="reply-card"><span class="mini-label">Latest user reply</span><p>${escapeHTML(reply)}</p></div>`;
}

async function runModalAction(action, channel) {
  const slot = document.getElementById("modalActionResult");
  if (!selectedCase || !slot) return;
  slot.className = "modal-action-result";
  slot.textContent = "Re-running policy gates...";
  try {
    const res = await fetch(`/api/cases/${encodeURIComponent(selectedCase.case_id)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        channel: channel || null,
        actor: "demo.ops",
        reason: "Hackathon demo operator approved AI-suggested action",
      }),
    });
    const data = await res.json();
    if (res.status === 409) {
      slot.classList.add("blocked");
      slot.textContent = `Blocked by policy: ${(data.detail?.policy_blocked_reasons || [data.detail]).join("; ")}`;
      return;
    }
    if (!res.ok) throw new Error(data.detail || `action failed (${res.status})`);
    slot.classList.add("success");
    slot.textContent = `${data.detail}${data.external_ref ? ` | ref ${data.external_ref}` : ""}`;
  } catch (err) {
    slot.classList.add("blocked");
    slot.textContent = `Failed: ${err.message}`;
  }
}

async function createModalPaymentLink() {
  const slot = document.getElementById("modalActionResult");
  if (!selectedCase || !slot) return;
  slot.className = "modal-action-result";
  slot.textContent = "Creating Razorpay Test Mode payment link...";
  try {
    const res = await fetch(`/api/cases/${encodeURIComponent(selectedCase.case_id)}/payment-link`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: "demo.payment_link" }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `payment link failed (${res.status})`);
    slot.classList.add("success");
    slot.innerHTML = `<a href="${escapeHTML(data.payment_url || data.short_url)}" target="_blank" rel="noopener noreferrer">Open Razorpay payment link</a><span>${escapeHTML(data.source)} | ${rupee(data.amount_paise)} | ${escapeHTML(data.short_url || "")}</span>`;
  } catch (err) {
    slot.classList.add("blocked");
    slot.textContent = `Failed: ${err.message}`;
  }
}

async function createModalVoice() {
  const slot = document.getElementById("modalActionResult");
  if (!selectedCase || !slot) return;
  slot.className = "modal-action-result";
  slot.textContent = "Generating AI voice nudge...";
  try {
    const res = await fetch(`/api/cases/${encodeURIComponent(selectedCase.case_id)}/voice-nudge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ register: "hi-en", actor: "demo.voice" }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `voice failed (${res.status})`);
    slot.classList.add("success");
    slot.innerHTML = `<audio controls src="${escapeHTML(data.audio_url)}" preload="none"></audio><span>${escapeHTML(data.call_state)} | ${escapeHTML(data.voice)} | ${Number(data.duration_seconds || 0).toFixed(1)}s</span>`;
  } catch (err) {
    slot.classList.add("blocked");
    slot.textContent = `Failed: ${err.message}`;
  }
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
  document.getElementById("caseModalClose")?.addEventListener("click", closeCaseModal);
  document.getElementById("caseModal")?.addEventListener("click", (event) => {
    if (event.target === document.getElementById("caseModal")) closeCaseModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeCaseModal();
  });
  document.getElementById("caseModalBody")?.addEventListener("click", (event) => {
    const action = event.target.closest(".modal-action");
    if (action) runModalAction(action.dataset.action, action.dataset.channel);
    if (event.target.closest(".modal-payment-link")) createModalPaymentLink();
    if (event.target.closest(".modal-voice")) createModalVoice();
  });
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
