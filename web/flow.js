/* Reltix flow: voice call → one-tap decision → partner order → status updates → write-back.
   Loaded on /tenant (call + decision) and /reltix (partner inbox + manager log); each part renders only if its elements are on the page.
   Adds to app.js (uses its globals: state, byId, esc, money, prettyDate, energyContract,
   electricityMeter, daysUntil, chosenOffer, refreshState, setRole, renderOffers). */

(() => {
"use strict";

const AGENT_ID = "agent_2201m7wp3v2rfrtb1pqbq4rt1a80"; // ElevenLabs agent "Mia"

const call = {
  conversation: null,
  muted: false,
  startedAt: 0,          // when the call connected (for the call timer)
  status: "idle",        // idle | connecting | connected | ended
  mode: "listening",     // speaking | listening
  transcript: [],        // { who: "Mia" | "You", text }
  issueId: null,
  sessionId: null,
  eventNumber: 0,
  pendingEvent: null,
  recovery: null,
};
const resident = { declined: false, consent: false, busy: false, error: "", submissionId: null, selectedVersion: null };
const complaintFormAttempt = { sessionId: null, eventId: null, text: null };
let contractPrivacyMessage = "";
let phonePrivacyMessage = "";

/* ---------- small helpers ---------- */

const post = async (path, body) => {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const error = new Error(data.error || `Request failed (${res.status})`); error.payload = data; throw error; }
  return data;
};
const logEvent = (type, text, channel = "voice") => post("/api/events", { type, text, channel, unit_id: UNIT }).catch(() => {});
const idleish = () => call.status === "idle" || call.status === "ended";
const newComplaintId = () => globalThis.crypto?.randomUUID?.() || `complaint-${Date.now()}-${Math.random().toString(16).slice(2)}`;
// Everything the resident tells Mia or the portal is stored once on the flat (main.py update_case), so all pages show it.
const saveCase = (patch, source = "voice") => post("/api/case", { unit_id: UNIT, source, ...patch }).then(() => refreshState()).catch(() => {});
const serverCase = () => state.case || {};
const offers = () => state.partner?.offers || [];
const latestOrder = () => (state.orders || []).slice(-1)[0] || null;
const firstName = () => (state.unit?.tenant?.name || "there").split(" ")[0];
const pickedOffer = () => offers().find((offer) => offer.offer_id === chosenOfferId) || null;
function renderPrivacyPreferences() {
  const checkbox = document.getElementById("contract-sharing-toggle");
  if (!checkbox || !state.unit) return;
  if (document.activeElement !== checkbox && !checkbox.disabled) checkbox.checked = !!state.unit.tenant?.contract_sharing_opt_in;
  const phoneToggle = document.getElementById("outbound-phone-toggle");
  if (phoneToggle && document.activeElement !== phoneToggle && !phoneToggle.disabled) phoneToggle.checked = !!state.unit.tenant?.outbound_phone_opt_in;
  const lastChange = [...(state.privacy_consents || [])].reverse().find((entry) => entry.purpose === "contract_sharing");
  const recorded = lastChange ? ` Last changed ${new Date(lastChange.at).toLocaleString("en-GB")}.`
    : " This sample record starts with its saved preference; no change has been recorded in this session.";
  document.getElementById("privacy-settings-message").textContent = contractPrivacyMessage || (checkbox.checked
    ? `Contract sharing is on. You can turn it off at any time.${recorded}`
    : `Contract sharing is off. Renewal checks and new offers are paused.${recorded}`);
  if (phoneToggle) document.getElementById("phone-privacy-message").textContent = phonePrivacyMessage ||
    (phoneToggle.checked ? "Outbound AI phone calls are enabled." : "Outbound AI phone calls are disabled.");
  const history = (state.privacy_consents || []).slice(-8).reverse();
  document.getElementById("privacy-consent-history").innerHTML = history.length
    ? history.map((entry) => {
      const purpose = { contract_sharing: "Contract sharing", outbound_phone: "Outbound AI calls", live_voice: "ElevenLabs live voice" }[entry.purpose] || entry.purpose;
      const categories = entry.data_categories?.length ? `<br><span class="muted small">Data: ${entry.data_categories.map(esc).join(", ")}</span>` : "";
      return `<div class="detail-row"><span>${esc(purpose)} · ${entry.granted ? "allowed" : "withdrawn"}${categories}</span><strong>${esc(new Date(entry.at).toLocaleString("en-GB"))}${entry.recipient ? ` · ${esc(entry.recipient)}` : ""}</strong></div>`;
    }).join("")
    : '<p class="muted small">No consent changes have been recorded yet. Some sample records begin with contract sharing enabled.</p>';
}

function consentDisclosureText(offer) {
  if (!offer) return "Choose a plan to see which information the supplier workspace will receive.";
  const details = offer.consent_disclosure || { required_fields: [], standard_fields: [] };
  const fields = details.required_fields.map((field) => field.label);
  const standard = details.standard_fields;
  const fieldText = fields.length ? fields.join(", ") : "no offer-specific resident or home fields";
  return `I agree to share ${fieldText} with ${esc(state.partner.partner)} in this local demo for ${esc(offer.product)} at ${money(offer.estimated_monthly_price_eur)}/month for ${Number(offer.contract_months || 0)} months. The simulated supplier workspace will also see: ${standard.map(esc).join(", ")}. Current supplier and contract end date are shared only if listed above.`;
}

/* Resident-initiated complaint call. Contract decisions are made in the portal. */
const clientTools = {
  save_note: ({ note }) => reportIssue({ text: String(note || "") }),
  handoff_to_human: ({ reason }) => reportIssue({ urgent: true, reason, text: call.issueId ? "" : String(reason || "Urgent problem") }),
};

// Problem reports (resident calls Mia): one ticket per call, later details and "urgent" update the same ticket.
async function reportIssue({ text = "", urgent = false, reason = "" }) {
  call.sessionId ||= newComplaintId();
  const normalized = JSON.stringify([text, urgent, reason]);
  if (!call.pendingEvent || call.pendingEvent.payload !== normalized) {
    call.pendingEvent = { payload: normalized, id: `${call.sessionId}-${++call.eventNumber}` };
  }
  try {
    const { issue } = await post("/api/issues", { unit_id: UNIT, source: "voice", issue_id: call.issueId, text, urgent, reason,
      call_session_id: call.sessionId, client_event_id: call.pendingEvent.id });
    call.issueId = issue.issue_id;
    call.pendingEvent = null;
    refreshState();
    return issue.urgent ? "Sent to the property team as urgent. A person will contact them soon." : "Problem sent to the property team.";
  } catch (error) { return `Couldn't save it: ${error.message}`; }
}

async function recoverDroppedCall() {
  try { await refreshState(); } catch (_) { /* Preserve the unfinished report path when the API is unavailable. */ }
  const saved = (state.issues || []).find((issue) => issue.call_session_id === call.sessionId);
  if (saved) {
    call.issueId = saved.issue_id;
    call.recovery = { state: "saved", issue: saved };
  } else {
    const draft = call.transcript.filter((line) => line.who === "You").map((line) => line.text).join(" ").slice(0, 300);
    const eventId = call.pendingEvent?.id || `${call.sessionId}-finish`;
    call.recovery = { state: "unfinished", draft, sessionId: call.sessionId, eventId };
  }
  renderComplaintRecovery();
}

function complaintFormIds(text) {
  if (!complaintFormAttempt.sessionId) {
    complaintFormAttempt.sessionId = newComplaintId();
  }
  if (complaintFormAttempt.text !== text) {
    complaintFormAttempt.eventId = `${complaintFormAttempt.sessionId}-${newComplaintId()}`;
    complaintFormAttempt.text = text;
  }
  return { sessionId: complaintFormAttempt.sessionId, eventId: complaintFormAttempt.eventId };
}

async function startCall() {
  const voiceConsent = byId("voice-consent-box");
  if (!voiceConsent?.checked) return;
  if (!window.ElevenLabsClient) {
    call.error = "Voice calling is unavailable right now. Please try again later.";
    return render();
  }
  voiceConsent.checked = false;
  byId("call-agent-button").disabled = true;
  try {
    await post("/api/privacy/consent", { unit_id: UNIT, purpose: "live_voice", granted: true });
  } catch (error) {
    call.error = `Could not record your consent, so the live call was not started: ${error.message}`;
    return render();
  }
  Object.assign(call, { status: "connecting", transcript: [], error: "", issueId: null, sessionId: newComplaintId(),
    eventNumber: 0, pendingEvent: null, recovery: null });
  render();
  const vars = { ...state.agent_variables, call_reason: "problem_report",
    first_message: `Hi ${firstName()}, this is Mia. What happened, and where?` };
  logEvent("call", `${state.unit.tenant.name} called Mia to report a problem.`);
  try {
    call.conversation = await ElevenLabsClient.Conversation.startSession({
      agentId: AGENT_ID,
      dynamicVariables: vars, // built by centrix (main.py agent_variables), same as for phone calls
      clientTools,
      onConnect: () => {
        call.status = "connected";
        call.startedAt = Date.now();
        render();
      },
      onDisconnect: () => {
        call.status = "ended"; call.conversation = null;
        render();
        recoverDroppedCall();
      },
      onModeChange: ({ mode }) => { call.mode = mode; renderCall(); },
      onMessage: ({ source, role, message }) => {
        const who = (source || role) === "user" ? "You" : "Mia";
        if (message) call.transcript.push({ who, text: message });
        renderCall();
      },
      onError: (message) => { call.error = String(message); renderCall(); },
    });
  } catch (error) {
    Object.assign(call, { status: "idle", error: `Couldn't start the call: ${error.message || error}. Please try again later.` });
    render();
  }
}

async function endCall() {
  await call.conversation?.endSession();
}


function renderCall() {
  const card = document.getElementById("call-card");
  if (!card || !state.unit) return;
  const last = call.transcript.slice(-4);
  const lines = last.map((l) => `<div class="call-line${l.who === "You" ? " you" : ""}"><b>${esc(l.who)}</b> ${esc(l.text)}</div>`).join("");
  let head, actions;
  if (call.status === "connecting") {
    head = `<strong>Connecting to Mia…</strong><span class="muted small">Allow the microphone. Headphones work best.</span>`;
    actions = "";
  } else if (call.status === "connected") {
    head = `<strong><span class="live-dot"></span> On a call with Mia</strong><span class="muted small">${call.muted ? "Your mic is muted" : call.mode === "speaking" ? "Mia is speaking…" : "Listening…"}</span>`;
    actions = `<button class="text-button" type="button" data-call="mute">${call.muted ? "Unmute" : "Mute"}</button><button class="button" type="button" data-call="end">Hang up</button>`;
  } else if (call.status === "ended") {
    head = "<strong>Complaint call ended</strong><span class='muted small'>See your reports and replies under Report a problem.</span>";
    actions = "";
  } else {
    head = "<strong>Mia, your reltix assistant</strong><span class='muted small'>Report a problem or ask the property team to contact your supplier.</span>";
    actions = "";
  }
  card.innerHTML = `
    <div class="call-head"><span class="call-icon"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/></svg></span><div>${head}</div><div class="call-actions">${actions}</div></div>
    ${lines ? `<div class="call-script">${lines}</div>` : ""}
    ${call.error ? `<p class="form-message error">${esc(call.error)}</p>` : ""}`;
  renderPhone();
}

function renderComplaintRecovery() {
  const box = document.getElementById("complaint-recovery");
  if (!box) return;
  if (!call.recovery) { box.innerHTML = ""; return; }
  if (call.recovery.state === "saved") {
    const issue = call.recovery.issue;
    box.innerHTML = `<div class="reading-summary" role="status"><strong>Saved complaint ${esc(issue.issue_id)}</strong><p>${esc(issue.text)}</p><span>Sent to the ${issue.owner === "partner" ? "supplier" : "property team"}.</span></div>`;
    return;
  }
  box.innerHTML = `<form id="finish-dropped-call" class="feed-form"><p class="form-message" role="status">Your complaint call ended before we found a saved report. Your report is unfinished. Review it and finish it here.</p>
    <label>Finish your report<textarea name="text" rows="3" maxlength="300" required>${esc(call.recovery.draft)}</textarea></label>
    <button class="button button-primary" type="submit">Finish report</button></form>`;
}

/* Full-screen phone call: looks and behaves like a normal incoming call (answer / decline, timer, mute, hang up). */
const ICON_PHONE = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/></svg>`;
const ICON_MIC = (off) => `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5"/>${off ? '<path d="M3 3l18 18"/>' : ""}</svg>`;
const clock = () => {
  const s = Math.max(0, Math.floor((Date.now() - call.startedAt) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
function renderPhone() {
  if (!document.getElementById("call-card")) return;
  let el = document.getElementById("phone-screen");
  const live = call.status === "connecting" || call.status === "connected";
  if (!live) { el?.remove(); document.body.classList.remove("in-call"); return; }
  if (!el) { el = document.createElement("div"); el.id = "phone-screen"; el.setAttribute("role", "dialog"); el.setAttribute("aria-label", "Phone call"); document.body.appendChild(el); }
  document.body.classList.add("in-call");
  const sub = call.status === "connecting" ? "Connecting…" : `<span id="phone-timer"></span> · ${call.muted ? "muted" : call.mode === "speaking" ? "Mia is speaking" : "listening"}`;
  const buttons = `
      <div class="phone-btn-wrap"><button class="phone-btn mute${call.muted ? " on" : ""}" type="button" data-call="mute" aria-label="${call.muted ? "Unmute" : "Mute"}" ${call.status !== "connected" ? "disabled" : ""}>${ICON_MIC(call.muted)}</button><span>${call.muted ? "Unmute" : "Mute"}</span></div>
      <div class="phone-btn-wrap"><button class="phone-btn decline" type="button" data-call="end" aria-label="Hang up">${ICON_PHONE}</button><span>End</span></div>`;
  const last = call.transcript.slice(-2).map((l) => `<p class="${l.who === "You" ? "you" : ""}"><b>${esc(l.who)}:</b> ${esc(l.text)}</p>`).join("");
  const cls = call.mode === "speaking" ? "speaking" : "";
  if (el.className !== cls) el.className = cls;
  // Each part updates on its own, so a tap on a button is never lost when the text above it changes.
  if (!el.firstChild) el.innerHTML = `<div class="phone-top"></div><div class="phone-lines"></div><div class="phone-bottom"></div>`;
  const set = (sel, html) => { const part = el.querySelector(sel); if (part.dataset.html !== html) { part.dataset.html = html; part.innerHTML = html; } };
  set(".phone-top", `
      <div class="call-orb" aria-hidden="true"><span class="ring r1"></span><span class="ring r2"></span><span class="sphere"></span></div>
      <div class="wave" aria-hidden="true">${"<i></i>".repeat(9)}</div>
      <h2>Mia · reltix</h2>
      <p class="phone-sub">${sub}</p>
      <p class="phone-about">Reporting a problem · speak normally</p>`);
  set(".phone-lines", last + (call.error ? `<p class="err">${esc(call.error)}</p>` : ""));
  set(".phone-bottom", `<div class="phone-buttons">${buttons}</div>`);
  const timer = document.getElementById("phone-timer");
  if (timer) timer.textContent = clock();
}
setInterval(() => { const t = document.getElementById("phone-timer"); if (t && call.status === "connected") t.textContent = clock(); }, 1000);

/* ---------- 2. resident decision: consent, sign, live status ---------- */

const RESIDENT_STEPS = [
  ["submitted", "Signed and sent"],
  ["received", "Supplier accepted"],
  ["pending", "Supplier checking"],
  ["confirmed", "Contract confirmed"],
  ["active", "New supply running"],
];

// Resident page: the problems they reported, so they can see it reached the property team.
function renderIssues() {
  const order = latestOrder();
  const shareable = order && !["rejected", "cancelled"].includes(order.status);
  const shareOption = document.getElementById("share-option");
  if (shareOption) { shareOption.hidden = !shareable; shareOption.querySelector("input").disabled = !shareable; }
  renderComplaintRecovery();
  for (const [id, audience] of [["issue-list", "tenant"], ["staff-issues", "reltix"], ["partner-issues", "partner"]]) {
    const box = document.getElementById(id);
    if (!box) continue;
    const items = (state.issues || []).filter(i => audience !== "partner" || i.shared_order_id).slice().reverse();
    const html = items.map(i => {
      const assignee = i.owner === "partner" || i.shared_order_id ? "partner" : "reltix";
      const to = assignee === "partner" ? "supplier" : "property team";
      const reply = i.reply ? `<p><strong>Reply from ${to}</strong> · ${esc(i.reply)}</p>` : "";
      const handoff = !i.reply && i.ownership_reason ? `<p class="muted small">The supplier order ended. The ${esc(to)} will reply. ${esc(i.ownership_reason)}</p>` :
        !i.reply ? `<p class="muted small">This complaint is with the ${esc(to)}.</p>` : "";
      const form = !i.reply && assignee === audience ? `<form class="feed-form" data-issue="${esc(i.issue_id)}" data-unit="${esc(UNIT)}" data-assignee="${audience}">
        <label>${assignee === "partner" ? "Your simulated phone reply" : "Reply to tenant"}<textarea name="reply" rows="2" maxlength="500" required></textarea></label>
        <button class="button" type="submit">Send reply</button><span role="status" class="form-message"></span></form>` : "";
      const share = !i.reply && audience === "tenant" && shareable && !i.shared_order_id ?
        `<button class="text-button" type="button" data-share-issue="${esc(i.issue_id)}">Share with ${esc(order.partner)} and request a call</button>` : "";
      const targetId = audience === "reltix" ? ` id="reply-${esc(i.issue_id)}"` : "";
      return `<details class="fold"${targetId}><summary>${i.urgent ? "Urgent complaint" : "Complaint"} · ${i.reply ? "Reply received" : "Awaiting " + to}</summary>
        <p>${esc(i.text)}</p>${handoff}${reply}${share}${form}</details>`;
    }).join("");
    if (box.dataset.html !== html && (!box.contains(document.activeElement) || box.dataset.unit !== UNIT)) {
      box.dataset.html = html; box.dataset.unit = UNIT; box.innerHTML = html;
      if (audience === "reltix" && location.hash !== openedReply) openLinkedReply();
    }
  }
}

let openedReply = "";
function openLinkedReply() {
  openedReply = location.hash;
  if (!openedReply.startsWith("#reply-")) return;
  const ticket = document.getElementById(openedReply.slice(1));
  if (!ticket) { openedReply = ""; return; }
  ticket.open = true;
  ticket.scrollIntoView({ block: "center" });
  ticket.querySelector("textarea")?.focus({ preventScroll: true });
}
window.addEventListener("hashchange", openLinkedReply);

function renderDecision() {
  const box = document.getElementById("decision");
  if (!box || !state.unit) return;
  const order = decisionOrder();
  const contract = energyContract();
  const history = (state.orders || []).filter(item => item !== order).slice().reverse();
  byId("renewal-history").hidden = !history.length;
  byId("renewal-history-list").innerHTML = history.map(item =>
    `<li>${esc(item.product)} · ${esc(item.partner)} · ${esc(STATUS_TEXT[item.status] || item.status)} · previous contract ended ${esc(prettyDate(item.previous_contract?.ends))}</li>`
  ).join("");
  const terminal = order && ["rejected", "cancelled"].includes(order.status);
  byId("renewal-options").hidden = !state.offer_available || Boolean(order && !terminal);

  if (order && !terminal) {
    const reached = new Set(order.status_history.map((s) => s.status));
    const result = order.status_history.find((s) => s.result)?.result;
    const steps = RESIDENT_STEPS.map(([key, label]) => `<li class="${reached.has(key) ? "done" : ""}">${reached.has(key) ? "✓" : "○"} ${esc(label)}</li>`).join("");
    const readingNeeded = order.status === "needs_input" || (order.status === "confirmed" && order.reading_required !== false && order.switch_reading?.value_kwh == null);
    const readDate = order.switch_reading_date;
    const needs = readingNeeded ? `<p class="notice notice-amber">Enter the reading taken on ${esc(prettyDate(readDate))} below. It will be passed to the supplier.</p>` : "";
    const unconfirmed = order.start_unconfirmed ? `<p class="notice notice-amber">The supplier hasn’t confirmed whether supply started. The property team is following up.</p>` : "";
    const mismatch = order.start_date_mismatch ? `<p class="notice notice-amber">The supplier proposed ${esc(prettyDate(order.start_date_mismatch.proposed))}, but the requested start is ${esc(prettyDate(order.start_date_mismatch.requested))}. Neither date is confirmed yet.</p>` : "";
    const meterNumberNeeded = !order.meter_number ? `<p class="notice notice-amber">Add the meter number to continue this handover.</p>` : "";
    const done = result
      ? `<p class="notice">Your new contract ${esc(result.contract_number)} starts on ${esc(prettyDate(result.start))}.</p>` : "";
    const last = order.status_history.at(-1);
    const pending = last?.status === "pending" ? '<p class="notice">The supplier is still checking the start date.</p>' : "";
    const reading = order.switch_reading;
    const sent = reading?.value_kwh != null ? `<p class="muted small">Switch reading sent · ${esc(reading.value_kwh)} kWh · ${esc(prettyDate(reading.read_at))}</p>` : "";
    box.innerHTML = `<div class="decision-box"><div class="section-label"><span>${esc(order.product)} · your switch</span><span class="count-label">${esc(order.order_id)}</span></div><ul class="step-list">${steps}</ul>${pending}${unconfirmed}${mismatch}${needs}${meterNumberNeeded}${done}${sent}</div>`;
    if (readingNeeded) { byId("reading-fold").open = true; byId("reading-date").value = readDate; }
    if (!order.meter_number) byId("reading-fold").open = true;
    return;
  }
  if (!state.offer_available) {
    const followup = order?.status === "cancelled" || order?.status === "rejected";
    box.innerHTML = `<p class="muted small">${followup ? "No eligible replacement offer is available. The property team will handle the case." : "No renewal offer is due for this flat."}</p>`;
    return;
  }

  if (resident.declined) {
    box.innerHTML = `<div class="decision-box"><p><strong>You’re keeping ${esc(contract.supplier)}.</strong> Nothing was shared with any supplier.</p><button class="text-button" type="button" data-decide="undo">Changed your mind? Review the offers</button></div>`;
    return;
  }

  const offer = pickedOffer();
  const rejected = order?.status === "rejected"
    ? `<p class="notice notice-amber">${esc(order.partner)} declined the order. Choose an eligible plan below; each new order needs your consent.</p>`
    : order?.status === "cancelled" ? `<p class="notice notice-amber">The supplier cancelled the confirmed order. Review another plan and give consent before placing a replacement.</p>` : "";
  const saving = offer && contract.current_monthly_price_eur ? contract.current_monthly_price_eur - offer.estimated_monthly_price_eur : 0;
  box.innerHTML = `
    <div class="decision-box">
      ${rejected}
      ${offer ? `<p class="saving">${saving > 0 ? `You save about <b>${money(saving)}</b> a month (${money(saving * 12)} a year) compared with today.` : `This plan costs ${money(-saving)} more per month than today.`}</p>` : `<p class="muted small">Pick a plan above to continue.</p>`}
      <label class="consent">
        <input type="checkbox" id="consent-box" ${resident.consent ? "checked" : ""} ${offer ? "" : "disabled"}>
        <span>${consentDisclosureText(offer)}</span>
      </label>
      <div class="decision-actions">
        <button class="button button-primary" type="button" data-decide="sign" ${offer && resident.consent && !resident.busy ? "" : "disabled"}>${resident.busy ? "Sending…" : "Confirm & sign"}</button>
        <button class="text-button" type="button" data-decide="decline">Keep my current contract</button>
      </div>
      ${resident.error ? `<p class="form-message error">${esc(resident.error)}</p>` : ""}
    </div>`;
}

async function sign() {
  const offer = pickedOffer();
  if (!offer) return;
  resident.submissionId ||= crypto.randomUUID();
  resident.busy = true; resident.error = ""; render();
  try {
    await post("/api/orders", { unit_id: UNIT, offer_id: offer.offer_id, meter_id: window.selectedMeterId?.(),
      offer_version: offer.offer_version, submission_id: resident.submissionId,
      consent_given: true, channel: "portal", signed_name: state.unit.tenant.name });
    resident.consent = false;
    resident.submissionId = null;
  } catch (error) {
    resident.error = error.message;
    if (error.payload?.stale_offer) {
      resident.consent = false;
      resident.submissionId = null;
      chosenOfferVersion = null;
      await refreshState();
    }
  }
  resident.busy = false;
  await refreshState();
}

function chooseOffer(id, version) {
  syncedChoice = id;
  resident.consent = false;
  resident.error = "";
  resident.selectedVersion = version;
  renderDecision();
  if (id) post("/api/case", { unit_id: UNIT, chosen_offer_id: id, offer_version: version, source: "portal" }).then(refreshState).catch((error) => {
    resident.consent = false;
    resident.error = error.message;
    renderDecision();
  });
}

function syncOfferVersion() {
  if (!chosenOfferId) return;
  const offer = offers().find((item) => item.offer_id === chosenOfferId);
  if (!offer) return;
  if (chosenOfferVersion && chosenOfferVersion !== offer.offer_version) {
    resident.consent = false;
    resident.error = "The plan terms changed. Review the revised price and give consent again.";
  }
  chosenOfferVersion = offer.offer_version;
  resident.selectedVersion = offer.offer_version;
}

/* ---------- 3. partner inbox ---------- */

const CALL_LINES = {
  submitted: (o) => [`Hello, this is the Reltix assistant with a new order.`, `${o.subject.tenant.name} at ${o.subject.address} would like your ${o.product} plan at ${money(o.estimated_monthly_price_eur)} a month, starting ${prettyDate(o.requested_start)}.`, `Can you take this order?`],
  received: (o) => [o.switch_reading_date ? `Can you confirm the start date, or do you need the meter reading taken on ${prettyDate(o.switch_reading_date)}?` : "Can you confirm the start date?"],
  needs_input: (o) => [`I’ve requested the meter reading${o.switch_reading_date ? " taken on " + prettyDate(o.switch_reading_date) : ""}. I’ll call you back as soon as I have it.`],
  confirmed: (o) => [`Great, I’ve told the customer it starts on ${prettyDate(o.requested_start)}.`, `I’ll check in on the start day to confirm the power is running.`],
  active: () => [`All done. The contract is filed with the property. Thank you!`],
  rejected: () => [`Understood. I’ll offer the customer another plan. Thank you.`],
};
const PARTNER_ANSWERS = {
  submitted: [["received", "Yes, I’ll take it", "button-primary"], ["pending", "Still checking", ""], ["rejected", "No, decline", ""]],
  received: [["confirmed", "Confirm start date", "button-primary"], ["pending", "Still checking", ""], ["needs_input", "I need the meter reading", ""]],
  pending: [["confirmed", "Confirm start date", "button-primary"], ["received", "Order is progressing", ""], ["rejected", "No, decline", ""]],
  confirmed: [["active", "Start day: power is running", "button-primary"], ["cancelled", "Cancel before activation", ""]],
};
const STATUS_TEXT = {
  submitted: "Customer chose your plan", received: "You accepted the order", needs_input: "You asked for the meter reading",
  confirmed: "You confirmed the contract", pending: "Checking start date", active: "Supply started", rejected: "You declined the order", cancelled: "Order cancelled",
  start_date_mismatch: "Supplier proposed a different start date",
};

function renderPartner() {
  if (!state.unit || !document.getElementById("order-card")) return;
  const order = latestOrder();
  if (!order) {
    byId("company-title").textContent = "No new orders";
    byId("partner-lead").innerHTML = `Hi <strong id="partner-name">${esc(state.partner.partner)}</strong>. When a customer picks one of your plans, our assistant calls you and the order appears here.`;
    byId("order-card").innerHTML = "";
    byId("call-script").innerHTML = `<div class="call-line"><b>Assistant</b> Nothing to report yet.</div>`;
    byId("partner-actions").innerHTML = "";
    delete byId("partner-actions").dataset.order;
    byId("job-timeline").innerHTML = "";
    byId("order-id-label").textContent = "";
    return;
  }
  const titles = { submitted: "Incoming supplier call", received: "Confirm the start date", pending: "Supplier checking the start date", needs_input: "Waiting for a reading",
    confirmed: "Start date confirmed", active: "Handover complete", rejected: "Order declined", cancelled: "Order cancelled" };
  byId("company-title").textContent = titles[order.status] || "Supplier order";
  byId("partner-lead").innerHTML = `Simulated supplier call · <strong id="partner-name">${esc(order.partner)}</strong>`;
  const reading = order.subject.meter.switch_reading?.value_kwh;
  const rows = [
    ["Customer", `${order.subject.tenant.name} · contact via centrix`],
    ["Address", order.subject.address],
    ["Plan", `${order.product} · ${money(order.estimated_monthly_price_eur)}/month`],
    ["Start", prettyDate(order.requested_start)],
    ["Meter", [order.subject.meter.number || "number needed", reading != null ? `${Number(reading).toLocaleString("en-GB")} kWh` : order.switch_reading_date && `Take the reading on ${prettyDate(order.switch_reading_date)}.`].filter(Boolean).join(" · ")],
    ["Old contract", `${order.previous_contract.supplier}, ends ${prettyDate(order.previous_contract.ends)} · you cancel it`],
    ["Consent", `${order.consent.channel} · ${new Date(order.consent.given_at).toLocaleString("en-GB")}`],
  ];
  if (order.start_date_mismatch) {
    rows.push(["Start date mismatch", `Requested ${prettyDate(order.start_date_mismatch.requested)} · supplier proposed ${prettyDate(order.start_date_mismatch.proposed)} · neither confirmed`]);
  }
  byId("order-card").innerHTML = `<div class="detail-list order-details">${rows.map(([k, v]) => `<div class="detail-row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("")}</div>`;
  byId("call-script").innerHTML = (CALL_LINES[order.status] || (() => []))(order).map((line) => `<div class="call-line"><b>Assistant</b> ${esc(line)}</div>`).join("");
  if (order.start_date_mismatch) {
    byId("call-script").insertAdjacentHTML("beforeend", `<div class="notice notice-amber">Start date mismatch: requested ${esc(prettyDate(order.start_date_mismatch.requested))}; supplier proposed ${esc(prettyDate(order.start_date_mismatch.proposed))}. Neither date is confirmed. The supplier can decline this order.</div>`);
  }
  const followup = (state.calls || []).find((item) => item.order_id === order.order_id && item.kind === "supplier_follow_up");
  if (followup && followup.status !== "done") {
    byId("call-script").insertAdjacentHTML("beforeend", `<div class="notice notice-amber">Supplier follow-up queued · ${esc(prettyDate(followup.due_date))}${followup.owner === "property team" ? " · property team owns the overdue callback" : " · supplier callback"}.</div>`);
  }
  const canUpdateStart = ["submitted", "received", "pending"].includes(order.status);
  const startControls = canUpdateStart ? `<label class="small">Supplier start date<input type="date" id="partner-start-date" value="${esc(order.requested_start)}"></label>
    <label class="consent"><input type="checkbox" id="partner-supply-started"><span>Supply actually began on this date (required for a past date)</span></label>` : "";
  const actions = (PARTNER_ANSWERS[order.status] || []).filter(([status]) => status !== "needs_input" || order.switch_reading_date);
  if (order.start_date_mismatch && ["received", "pending"].includes(order.status) && !actions.some(([status]) => status === "rejected")) {
    actions.push(["rejected", "Decline proposed date", ""]);
  }
  const panel = byId("partner-actions");
  if (panel.dataset.order !== order.order_id) {
    panel.innerHTML = '<span class="answer-row" data-status-actions></span><span class="answer-row" data-start-controls></span>';
    panel.dataset.order = order.order_id;
  }
  const buttons = panel.querySelector("[data-status-actions]");
  const controls = panel.querySelector("[data-start-controls]");
  const buttonsHtml = actions
    .map(([status, label, cls]) => `<button class="button ${cls}" type="button" data-partner="${status}">${esc(label)}</button>`).join("")
    + (order.status === "needs_input" ? `<span class="muted small">Waiting for the customer…</span>` : "");
  if (buttons.dataset.html !== buttonsHtml) {
    buttons.innerHTML = buttonsHtml;
    buttons.dataset.html = buttonsHtml;
  }
  if (controls.dataset.html !== startControls) {
    controls.innerHTML = startControls;
    controls.dataset.html = startControls;
  }
  byId("order-id-label").textContent = order.order_id;
  byId("job-timeline").innerHTML = order.status_history.slice().reverse().map((e) => `
    <li><strong>${esc(STATUS_TEXT[e.status] || e.status)}</strong><span class="muted">${new Date(e.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })} · ${esc(e.by === "centrix" ? "centrix" : "you")}${e.note ? ` · ${esc(e.note)}` : ""}${e.result ? ` · contract ${esc(e.result.contract_number)}` : ""}</span></li>`).join("");
}

async function partnerAnswer(status) {
  const order = latestOrder();
  byId("partner-actions").querySelector(".form-message")?.remove();
  const note = status === "needs_input" ? `Take the meter reading on ${prettyDate(order.switch_reading_date)}.`
    : status === "rejected" ? order?.start_date_mismatch
      ? `Cannot honor requested start ${order.start_date_mismatch.requested}; proposed ${order.start_date_mismatch.proposed}`
      : "not available for this postcode" : "";
  try {
    const extra = status === "confirmed" ? { start_date: byId("partner-start-date").value,
      supply_started: byId("partner-supply-started").checked } : status === "cancelled" ? { note: "cancelled before activation" } : {};
    await post(`/api/orders/${latestOrder().order_id}/status`, { status, note, ...extra });
  } catch (error) {
    alertInline(error.message);
  }
  await refreshState();
}
function alertInline(message) {
  byId("partner-actions").insertAdjacentHTML("beforeend", `<p class="form-message error">${esc(message)}</p>`);
}

/* ---------- staff: connection flow (centrix core ↔ resident, supplier, landlord) ---------- */
function renderNetwork() {
  const box = document.getElementById("network");
  if (!box || !state.unit) return;
  const c = serverCase();
  const order = latestOrder();
  const lastCall = (state.calls || []).slice(-1)[0];
  const chosen = offers().find((o) => o.offer_id === c.chosen_offer_id);
  const tenant = state.unit.tenant?.name || "No resident";
  const node = (id, title, line, on, tone) => ({ id, title, line, on, tone });
  const resident = node("resident", tenant, chosen ? `Chose ${chosen.product}` : lastCall ? `Call ${CALL_STATE[lastCall.status] || lastCall.status}` : (state.issues || []).length ? "Reported a problem" : "Waiting", !!(lastCall || chosen || (state.issues || []).length), "cyan");
  const supplier = node("supplier", state.partner?.partner || "Energy partner", order ? `Order ${order.status.replace("_", " ")}` : "No order yet", !!order, "emerald");
  const landlord = node("landlord", state.unit.owner?.name || "Landlord", order?.status === "active" ? "New contract filed" : (state.unit.consent_log || []).length ? "Consent on file" : "0 actions needed", order?.status === "active", "amber");
  const core = { title: "centrix", line: c.updated_at || order ? "Case open" : "Watching" };
  const html = `
    <svg class="net-lines" viewBox="0 0 600 300" preserveAspectRatio="none" aria-hidden="true">
      ${[["resident", 110, 70], ["supplier", 490, 70], ["landlord", 300, 255]].map(([id, x, y]) => {
        const n = { resident, supplier, landlord }[id];
        return `<line x1="300" y1="150" x2="${x}" y2="${y}" class="net-line ${n.on ? `on ${n.tone}` : ""}"/>`;
      }).join("")}
    </svg>
    <div class="net-node core"><span class="orb"></span><strong>${core.title}</strong><span>${esc(core.line)}</span></div>
    ${[resident, supplier, landlord].map((n) => `
      <div class="net-node ${n.id} ${n.on ? `on ${n.tone}` : ""}"><span class="net-kind">${{ resident: "Resident", supplier: "Supplier", landlord: "Landlord" }[n.id]}</span><strong>${esc(n.title)}</strong><span class="pill3d ${n.on ? n.tone : ""}">${esc(n.line)}</span></div>`).join("")}`;
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
  const sub = document.getElementById("network-sub");
  if (sub) sub.textContent = `Flat ${UNIT.replace("unit_", "").toUpperCase()}${state.following ? " · following live" : ""}`;
}

/* ---------- 4. property manager: what happened, consent, contracts ---------- */

const EVENT_TITLES = {
  detected: "Renewal window detected", call_queued: "Call queued for Mia",
  staff_review: "Staff review logged",
  call: "Call", choice: "Plan chosen on the call", case: "Resident file updated", reading: "Meter reading", link: "Link sent", declined: "Resident declined",
  callback: "Callback requested", handoff: "Needs a person", consent: "Consent given and signed", order: "Order sent to partner",
  received: "Partner accepted", needs_input: "Partner needs input", confirmed: "Contract confirmed", rejected: "Partner declined",
  active: "New supply running", filed: "Filed on the unit", cancelled: "Order cancelled", issue: "Problem reported",
  issue_reply: "Complaint answered", issue_shared: "Supplier follow-up requested", pending: "Supplier checking start date",
  supplier_follow_up: "Supplier callback queued", reading_requested: "Switch reading requested", meter_identifier: "Meter number added",
  start_date_mismatch: "Supplier proposed a different start date",
};

function renderManager() {
  if (!state.unit || !document.getElementById("case-timeline")) return;
  const events = state.events || [];
  const contract = energyContract();
  const remaining = daysUntil(contract.expiry_date);
  const detected = { type: "detected", at: null, channel: "centrix", text: `Contract with ${contract.supplier} ends ${prettyDate(contract.expiry_date)} (${remaining} days). Case opened ${contract.renewal_notice_days || 30} days before expiry.` };
  const all = !state.offer_available || events.some((e) => e.type === "detected") ? events : [detected, ...events];
  byId("landlord-title").textContent = `Flat ${UNIT.replace("unit_", "").toUpperCase()}${state.unit.tenant?.name ? ` · ${state.unit.tenant.name}` : ""}${state.following ? " · live" : ""}`;
  byId("case-count").textContent = events.length ? `${events.length} ${events.length === 1 ? "step" : "steps"} · 0 by you` : "Waiting for the resident";
  byId("case-timeline").innerHTML = all.slice().reverse().map((e) => `
    <li><strong>${esc(e.type === "detected" ? "Expiry detected" : EVENT_TITLES[e.type] || e.type)}</strong><span class="muted">${e.at ? `${new Date(e.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })} · ${esc(e.channel)} · ` : ""}${esc(e.text)}</span></li>`).join("");

  const order = latestOrder();
  const status = byId("landlord-renewal-status");
  if (order?.status === "active") status.textContent = `Switched to ${order.product}`;
  else if (order?.status === "confirmed") status.textContent = `Switching on ${prettyDate(order.requested_start)}`;
  else if (order?.status === "rejected") status.textContent = "Supplier declined · review alternatives";
  else if (order?.status === "cancelled") status.textContent = "Order cancelled · supply status needs review";
  else if (order?.start_unconfirmed) status.textContent = "Start date unconfirmed · callback owned by property team";
  else if (order && order.status !== "rejected") status.textContent = "Partner processing";
  else if (resident.declined || events.some((e) => e.type === "declined")) status.textContent = "Resident keeps contract";
  else status.textContent = "Resident decides";

  const c = serverCase();
  let told = document.getElementById("resident-said");
  if (!told) {
    told = document.createElement("div");
    told.id = "resident-said";
    document.querySelector("#landlord-view .status-list")?.insertAdjacentElement("afterend", told);
  }
  const chosen = offers().find((o) => o.offer_id === c.chosen_offer_id);
  const reading = electricityMeter().switch_reading || {};
  const facts = [
    chosen && ["Chose", chosen.product],
    c.preferred_channel && ["Prefers", c.preferred_channel.toUpperCase()],
    reading.value_kwh != null && ["Meter reading", `${Number(reading.value_kwh).toLocaleString("en-GB")} kWh (${reading.source || "portal"})`],
    c.callback && ["Call back", c.callback],
    c.declined && ["Declined", c.declined_reason || "yes"],
    c.needs_person && ["Needs a person", c.needs_person],
    ...(c.notes || []).slice(-5).map((n) => ["Note", `${n.text} (${n.source})`]),
    ...(state.issues || []).slice(-5).map((i) => [i.urgent ? "⚠ Urgent problem" : "Problem reported", `${i.text} (${new Date(i.reported_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })})`]),
  ].filter(Boolean);
  told.innerHTML = facts.length ? `<div class="section-label"><span>What the resident told us</span><span class="count-label">updated ${esc(new Date(c.updated_at || Date.now()).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }))}</span></div>
    <div class="detail-list told">${facts.map(([k, v]) => `<div class="detail-row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("")}</div>` : "";
  const log = state.unit.consent_log || [];
  byId("consent-count").textContent = `${log.length} ${log.length === 1 ? "entry" : "entries"}`;
  byId("consent-log").innerHTML = log.length
    ? log.map((c) => {
      const terms = c.offer_snapshot || {};
      return `<div class="detail-row"><span>${esc(new Date(c.given_at).toLocaleString("en-GB"))} · ${esc(c.channel)} · ${esc(c.signed_name || c.given_by || "tenant")} · ${esc(c.order_id)}</span><strong>Shared with ${esc(c.recipient || c.partner)}: ${esc((c.approved_fields || c.data_shared || []).join(", "))}. Approved ${esc(terms.product || "plan")} at ${money(terms.estimated_monthly_price_eur)} / month for ${esc(terms.contract_months)} months.</strong></div>`;
    }).join("")
    : `<p class="muted small">No personal data has been shared with a partner.</p>`;

  const energy = (state.unit.contracts || []).filter((c) => c.type === "energy_supply");
  const running = energy.find((c) => c.status === "active");
  if (running) {
    byId("landlord-provider").textContent = running.supplier;
    byId("landlord-price").textContent = `${running.product} · ${money(running.current_monthly_price_eur)} / month${running.start ? ` · from ${prettyDate(running.start)}` : ""}`;
  }
  byId("contract-count").textContent = `${energy.length}`;
  byId("contract-list").innerHTML = energy.map((c) => `<div class="detail-row"><span><span class="badge ${c.status === "active" ? "badge-green" : c.status === "ended" ? "" : "badge-amber"}">${esc(c.status)}</span> ${esc(c.supplier)}</span><strong>${esc(c.product)} · ${money(c.current_monthly_price_eur)}/mo · ${c.expiry_date ? `ends ${esc(prettyDate(c.expiry_date))}` : `starts ${esc(prettyDate(c.start))}`}</strong></div>`).join("");
}

/* ---------- 5. staff: today's automatic run across the portfolio ---------- */

let portfolio = null;
async function loadPortfolio() {
  if (!document.getElementById("portfolio")) return;
  try { portfolio = await (await fetch("/api/portfolio", { cache: "no-store" })).json(); } catch { return; }
  renderPortfolio();
}

const DECISION = { matched: ["badge-green", "Portal offer"], attention: ["badge-amber", "Needs a person"], skipped: ["", "Skipped"] };
const CALL_STATE = { queued: "queued", ringing: "ringing…", answered: "on the call", done: "called", declined: "declined → SMS", missed: "no answer → SMS" };

function renderPortfolio() {
  const box = document.getElementById("portfolio");
  if (!box || !portfolio) return;
  const run = portfolio.run;
  const calls = portfolio.calls || [];
  const followups = portfolio.supplier_followups || [];
  const reviews = portfolio.review_queue || [];
  const callFor = (uid) => calls.filter((c) => c.unit_id === uid).slice(-1)[0];
  const rows = (run?.results || []).map((r) => {
    const [cls, label] = DECISION[r.decision];
    const c = callFor(r.unit_id);
    const callCell = c ? `<span class="badge ${c.status === "done" || c.status === "answered" ? "badge-green" : "badge-soft"}">${esc(CALL_STATE[c.status] || c.status)}</span>` : "";
    const links = `<a href="/reltix?unit=${r.unit_id}">case</a>${r.tenant ? ` · <a href="/tenant?unit=${r.unit_id}">tenant</a> · <a href="/supplier?unit=${r.unit_id}">supplier</a> · <a href="/owner?unit=${r.unit_id}">owner</a>` : ""}`;
    return `<tr class="${r.unit_id === UNIT ? "current" : ""}">
      <td><strong>${esc(r.unit_id.replace("unit_", "").toUpperCase())}</strong><span class="muted small">${esc(r.address.split(",")[0])}</span></td>
      <td>${esc(r.tenant || "—")}<span class="muted small">${r.expiry ? `ends ${esc(prettyDate(r.expiry))} · ${r.days_left} d` : "no contract"}</span></td>
      <td><span class="badge ${cls}">${label}</span> ${callCell}<span class="muted small">${esc(r.reason)}</span></td>
      <td class="small">${links}</td></tr>`;
  }).join("");
  const k = portfolio.kpis || {};
  const tile = (icon, value, label, sub, tone = "") => `<div class="kpi tilt ${tone}"><span class="kpi-icon">${icon}</span><span class="kpi-label">${label}</span><strong>${value}</strong><span class="kpi-sub">${sub}</span></div>`;
  const kpis = document.getElementById("kpis");
  if (kpis && run) {
    const html = [
      tile("◎", run.scanned, "Flats checked", `${run.matched} matched the trigger today`),
      tile("☎", k.support_calls || 0, "Phone interactions", "Tenant reports and supplier replies", "cyan"),
      tile("✓", k.signed || 0, "Contracts signed", k.monthly_saving_eur ? `€${Math.round(k.monthly_saving_eur * 12)} saved per year` : `${k.active || 0} running`, "emerald"),
      tile("!", reviews.length, "Need a person", "Data and workflow exceptions in queue", "amber"),
    ].join("");
    if (kpis.dataset.html !== html) { kpis.dataset.html = html; kpis.innerHTML = html; }
  }
  const slot = document.getElementById("clock-slot");
  if (slot && !slot.querySelector("#demo-date")) slot.innerHTML = `<label class="clock"><span>Demo date</span><input type="date" id="demo-date" value="${esc(portfolio.today)}"></label>`;
  else if (slot && document.activeElement?.id !== "demo-date") slot.querySelector("#demo-date").value = portfolio.today;
  const html = `
    <div class="panel-head"><h2>Today’s run</h2><span class="muted small">${run ? `${esc(prettyDate(run.date))} · ${new Date(run.ran_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })} · ${esc(run.source)}` : ""}</span></div>
    <p class="muted small">Matched residents receive a renewal offer in their portal. Phone calls handle complaints and supplier follow-up.</p>
    <section class="review-queue" aria-label="Property team review queue">
      <div class="section-label"><span>Needs review</span><span class="count-label">${reviews.length} open</span></div>
      ${reviews.length ? reviews.map((item) => `<article class="review-item ${item.severity === "urgent" ? "urgent" : ""}">
        <div class="review-item-head"><div><strong>${esc(item.title)}</strong><span class="muted small">Flat ${esc(item.unit_id.replace("unit_", "").toUpperCase())} · ${esc(item.category.replaceAll("_", " "))}</span></div>
          <a class="text-button" href="/reltix?unit=${esc(item.unit_id)}${item.issue_id ? "#reply-" + esc(item.issue_id) : ""}">${item.issue_id ? "Reply to resident" : "Open case"}</a></div>
        <p>${esc(item.reason)}</p><span class="muted small">Source: ${esc(item.source || "Not recorded")}</span>
        ${item.issue_id ? "" : `<form class="review-form" data-review="${esc(item.review_id)}" data-unit="${esc(item.unit_id)}">
          <label class="sr-only" for="review-note-${esc(item.review_id)}">Review note</label>
          <input id="review-note-${esc(item.review_id)}" name="note" maxlength="300" minlength="5" required placeholder="What did you check or do?">
          <button class="button" type="submit">Record review</button><span role="status" class="form-message"></span>
        </form>`}
      </article>`).join("") : `<p class="muted small">No open review items. Data checks, coverage and workflow exceptions will appear here.</p>`}
    </section>
    ${run ? `<div class="run-table-wrap"><table class="run-table"><thead><tr><th>Flat</th><th>Resident</th><th>Decision</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="muted">No run yet.</p>`}
    ${followups.length ? `<details class="fold"><summary>Supplier callbacks · ${followups.length} open</summary>${followups.map((f) => `<div class="detail-row"><span>${esc(f.unit_id.replace("unit_", "").toUpperCase())} · ${esc(prettyDate(f.due_date))} · ${esc(f.owner === "property team" ? "Property team owns overdue callback" : "Supplier callback queued")}</span><strong><a href="/supplier?unit=${esc(f.unit_id)}">Open supplier workflow</a></strong></div>`).join("")}</details>` : ""}
    <p class="phone-hint small muted">Supplier phone replies are simulated in the energy partner view.</p>`;
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
}

async function phoneCall(callId) {
  const to = prompt("Mia will phone this number (international format, e.g. +32470123456):");
  if (!to) return;
  try { await post(`/api/calls/${callId}/phone`, { to_number: to }); alertBox("Calling… pick up your phone."); }
  catch (error) { alertBox(error.message); }
}
function alertBox(message) {
  document.getElementById("portfolio")?.insertAdjacentHTML("beforeend", `<p class="notice">${esc(message)}</p>`);
}

/* ---------- wiring ---------- */

let syncedChoice = null;
let syncedCycle = null;
function syncFromCase() {
  const c = serverCase();
  if (c.renewal_cycle && c.renewal_cycle !== syncedCycle) {
    syncedCycle = c.renewal_cycle;
    syncedChoice = chosenOfferId = chosenOfferVersion = null;
    Object.assign(resident, { declined: false, consent: false, error: "", submissionId: null, selectedVersion: null });
    renderOffers("tenant-offers", true);
  }
  if (c.chosen_offer_id && c.chosen_offer_id !== syncedChoice) {
    syncedChoice = c.chosen_offer_id;
    const offer = offers().find((o) => o.offer_id === c.chosen_offer_id);
    if (offer && chosenOfferId !== offer.offer_id) {
      chosenOfferId = offer.offer_id;
      chosenOfferVersion = offer.offer_version;
      renderOffers("tenant-offers", true);
    }
  }
  if (typeof c.declined === "boolean") resident.declined = c.declined;
}

function render() {
  syncFromCase();
  renderPrivacyPreferences();
  if (document.getElementById("call-card")) {
    const avatar = document.querySelector(".topbar-right .avatar");
    const name = state.unit?.tenant?.name;
    if (avatar && name) avatar.textContent = name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  }
  renderCall();
  renderComplaintRecovery();
  renderIssues();
  renderDecision();
  renderPartner();
  renderManager();
  renderNetwork();
}

document.addEventListener("submit", async event => {
  const form = event.target;
  if (form.id !== "problem-form" && form.id !== "finish-dropped-call" && !form.dataset.issue && !form.dataset.review) return;
  event.preventDefault();
  const button = form.querySelector("button");
  if (button.disabled) return;
  button.disabled = true;
  const data = new FormData(form);
  const feedback = form.querySelector('[role="status"]') || byId("issue-feedback");
  try {
    if (form.dataset.review) {
      const note = String(data.get("note") || "").trim();
      await post(`/api/reviews/${form.dataset.review}`, { unit_id: form.dataset.unit, note });
      await loadPortfolio();
    } else if (form.id === "problem-form") {
      const text = String(data.get("text") || "").trim();
      const ids = complaintFormIds(text);
      await post("/api/issues", { unit_id: UNIT, text: data.get("text"), source: "simulated phone",
        shared_order_id: data.has("share") ? latestOrder()?.order_id : null,
        call_session_id: ids.sessionId, client_event_id: ids.eventId });
      form.reset(); byId("complaint-demo").open = false;
      Object.assign(complaintFormAttempt, { sessionId: null, eventId: null, text: null });
      feedback.textContent = "Complaint recorded. The reply will appear here.";
    } else if (form.id === "finish-dropped-call") {
      const recovery = call.recovery;
      const { issue } = await post("/api/issues", { unit_id: UNIT, text: String(data.get("text") || "").trim(), source: "voice recovery",
        call_session_id: recovery.sessionId, client_event_id: recovery.eventId });
      call.issueId = issue.issue_id;
      call.recovery = { state: "saved", issue };
      renderComplaintRecovery();
    } else {
      await post("/api/issues/reply", { unit_id: form.dataset.unit, issue_id: form.dataset.issue, role: form.dataset.assignee, reply: data.get("reply") });
      form.reset(); button.blur();
      if (ROLE === "reltix") await loadPortfolio();
    }
    await refreshState();
  } catch (error) { feedback.textContent = error.message; }
  finally { button.disabled = false; }
});

document.addEventListener("click", async (event) => {
  const share = event.target.closest("[data-share-issue]");
  if (share) {
    try {
      await post("/api/issues", { unit_id: UNIT, issue_id: share.dataset.shareIssue, shared_order_id: latestOrder().order_id });
      share.blur(); await refreshState();
    } catch (error) { byId("issue-feedback").textContent = error.message; }
    return;
  }
  const disconnect = event.target.closest("[data-sim-disconnect]");
  if (disconnect) {
    const form = byId("problem-form");
    const text = String(form.elements.text.value || "").trim();
    if (!text) { byId("issue-feedback").textContent = "Describe the problem before ending the simulated call."; return; }
    const ids = complaintFormIds(text);
    if (disconnect.dataset.simDisconnect === "saved") {
      try {
        const { issue } = await post("/api/issues", { unit_id: UNIT, text, source: "simulated voice event",
          call_session_id: ids.sessionId, client_event_id: ids.eventId,
          shared_order_id: form.elements.share?.checked ? latestOrder()?.order_id : null });
        call.recovery = { state: "saved", issue };
        await refreshState();
      } catch (error) { byId("issue-feedback").textContent = error.message; return; }
    } else {
      call.recovery = { state: "unfinished", draft: text, ...ids };
    }
    byId("issue-feedback").textContent = "The complaint call disconnected.";
    renderComplaintRecovery();
    return;
  }
  const t = event.target.closest("[data-call],[data-decide],[data-partner],[data-run],[data-phone],#demo-reset");
  if (!t) return;
  if (t.dataset.call === "problem" && idleish()) startCall();
  if (t.dataset.call === "end") endCall();
  if (t.dataset.call === "mute") { call.muted = !call.muted; call.conversation?.setMicMuted(call.muted); renderCall(); }
  if (t.dataset.decide === "sign") sign();
  if (t.dataset.decide === "decline") {
    resident.declined = true; render();
    saveCase({ declined: true, reason: "kept current contract" }, "portal");
  }
  if (t.dataset.decide === "undo") { resident.declined = false; render(); saveCase({ declined: false }, "portal"); }
  if (t.dataset.partner) partnerAnswer(t.dataset.partner);
  if (t.dataset.run) { await post("/api/run"); await loadPortfolio(); await refreshState(); }
  if (t.dataset.phone) phoneCall(t.dataset.phone);
  if (t.id === "demo-reset") {
    if (!window.confirm("Reset all demo data for every sample unit? This clears demo orders, reports and consent history.")) return;
    await endCall();
    Object.assign(call, { status: "idle", transcript: [], issueId: null, error: "", muted: false });
    Object.assign(complaintFormAttempt, { sessionId: null, eventId: null, text: null });
    Object.assign(resident, { declined: false, consent: false, busy: false, error: "", submissionId: null, selectedVersion: null });
    chosenOfferId = null;
    chosenOfferVersion = null;
    Object.assign(call, { sessionId: null, eventNumber: 0, pendingEvent: null, recovery: null });
    await post("/api/reset");
    await refreshState();
    await loadPortfolio();
  }
});
document.addEventListener("change", async (event) => {
  if (event.target.id === "consent-box") { resident.consent = event.target.checked; renderDecision(); }
  if (event.target.id === "contract-sharing-toggle") {
    const toggle = event.target;
    const desired = toggle.checked;
    toggle.disabled = true;
    contractPrivacyMessage = "Saving your preference…";
    byId("privacy-settings-message").textContent = contractPrivacyMessage;
    try {
      const result = await post("/api/privacy/consent", { unit_id: UNIT, purpose: "contract_sharing", granted: desired });
      contractPrivacyMessage = result.contract_sharing_opt_in
        ? "Contract sharing is on. You can turn it off at any time."
        : "Contract sharing is off. Future renewal checks and new orders are paused.";
      await refreshState();
    } catch (error) {
      toggle.checked = !!state.unit?.tenant?.contract_sharing_opt_in;
      contractPrivacyMessage = `Could not save this preference: ${error.message}`;
    } finally {
      toggle.disabled = false;
      renderPrivacyPreferences();
    }
  }
  if (event.target.id === "outbound-phone-toggle") {
    const toggle = event.target;
    const desired = toggle.checked;
    toggle.disabled = true;
    phonePrivacyMessage = "Saving your preference…";
    byId("phone-privacy-message").textContent = phonePrivacyMessage;
    try {
      await post("/api/privacy/consent", { unit_id: UNIT, purpose: "outbound_phone", granted: desired });
      phonePrivacyMessage = desired ? "Outbound AI phone calls are enabled. You can turn this off at any time."
        : "Outbound AI phone calls are disabled.";
      await refreshState();
    } catch (error) {
      toggle.checked = !!state.unit?.tenant?.outbound_phone_opt_in;
      phonePrivacyMessage = `Could not save this preference: ${error.message}`;
    } finally {
      toggle.disabled = false;
      renderPrivacyPreferences();
    }
  }
  if (event.target.id === "voice-consent-box") byId("call-agent-button").disabled = !event.target.checked;
  if (event.target.id === "demo-date" && event.target.value) {
    await post("/api/clock", { today: event.target.value });
    await loadPortfolio(); await refreshState();
  }
});

// Keep every view live: partner answers show up for the resident and manager without reloading.
setInterval(() => {
  if (document.hidden || !state.unit) return;
  refreshState().catch(() => {});
  if (!document.getElementById("portfolio")?.contains(document.activeElement)) loadPortfolio();
}, 2500);
loadPortfolio();

window.flow = { render, renderPartner, chooseOffer, syncOfferVersion };
render();
})();
