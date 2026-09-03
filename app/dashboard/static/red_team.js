const API = "";

async function getJSON(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

function setStatus(state, label) {
  const status = document.getElementById("apiStatus");
  if (!status) return;
  status.className = `status-pill ${state}`;
  status.innerHTML = `<span></span>${label}`;
}

function titleize(value) {
  return String(value || "")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function triggerAttack(name, button) {
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="attack-icon"><i data-lucide="loader-circle"></i></span><span><span class="attack-title">Running ${escapeHTML(titleize(name))}</span><span class="attack-meta">Safety trace in progress</span></span><i data-lucide="chevron-right"></i>`;
  setStatus("", "Running");
  document.getElementById("result").className = "result empty";
  document.getElementById("result").innerHTML = `<div><i data-lucide="loader-circle"></i><p>Running guardrail trace...</p></div>`;
  if (window.lucide) window.lucide.createIcons();

  try {
    const response = await fetch(`${API}/api/red-team/attack/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.status);
    renderResult(data);
    setStatus(data.blocked ? "ready" : "error", data.blocked ? "Blocked" : "Review");
  } catch (err) {
    setStatus("error", "API offline");
    document.getElementById("result").className = "result";
    document.getElementById("result").innerHTML =
      `<h3>Error</h3><div class="reason">${escapeHTML(err.message)}</div>`;
  } finally {
    button.disabled = false;
    button.innerHTML = original;
    if (window.lucide) window.lucide.createIcons();
  }
}

function renderResult(data) {
  const detectedClass = data.detected ? "detected" : "failed";
  const blockedClass = data.blocked ? "blocked" : "failed";
  document.getElementById("result").className = "result";
  document.getElementById("result").innerHTML = `
    <h3>${escapeHTML(titleize(data.attack))}</h3>
    <div class="flow">
      <div class="step attack">
        <small>Step 1</small>
        <span>Attack</span>
      </div>
      <div class="step ${detectedClass}">
        <small>Step 2</small>
        <span>${data.detected ? "Detected" : "Not detected"}</span>
      </div>
      <div class="step ${blockedClass}">
        <small>Step 3</small>
        <span>${data.blocked ? "Blocked" : "Not blocked"}</span>
      </div>
    </div>
    <div class="reason">${escapeHTML(data.reason || "No reason returned.")}</div>`;
}

async function loadAttacks() {
  try {
    setStatus("", "Syncing");
    const attacks = await getJSON("/api/red-team/attacks");
    const container = document.getElementById("attackButtons");
    container.innerHTML = attacks
      .map(
        (attack) => `<button class="attack" type="button" data-attack="${escapeHTML(attack)}">
          <span class="attack-icon"><i data-lucide="shield-alert"></i></span>
          <span>
            <span class="attack-title">${escapeHTML(titleize(attack))}</span>
            <span class="attack-meta">Detector, policy gate, and audit reason</span>
          </span>
          <i data-lucide="chevron-right"></i>
        </button>`
      )
      .join("");
    container.querySelectorAll("button.attack").forEach((button) => {
      button.addEventListener("click", () => triggerAttack(button.dataset.attack, button));
    });
    setStatus("ready", "Ready");
  } catch (err) {
    setStatus("error", "API offline");
    document.getElementById("attackButtons").innerHTML =
      `<div class="error-banner">${escapeHTML(err.message)}</div>`;
  } finally {
    if (window.lucide) window.lucide.createIcons();
  }
}

document.addEventListener("DOMContentLoaded", loadAttacks);
