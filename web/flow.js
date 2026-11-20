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
  sessionId: null,
  eventNumber: 0,
  pendingEvent: null,
  recovery: null,
};
const resident = { declined: false, consent: false, busy: false, error: "" };
const complaintFormAttempt = { sessionId: null, eventId: null, text: null };

/* ---------- small helpers ---------- */

const post = async (path, body) => {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
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
const currentContract = () => (state.unit?.contracts || []).find((c) => c.type === "energy_supply" && c.status !== "signed") || energyContract();
const pickedOffer = () => offers()[chosenOffer] || null;
const SHARED_FIELDS = ["my name", "address", "meter number", "switch reading", "and start date"];

/* Resident-initiated complaint call. Contract decisions are made in the portal. */
const clientTools = {
  save_note: ({ note }) => reportIssue({ text: String(note || "") }),
  handoff_to_human: ({ reason }) => reportIssue({ urgent: true, reason, text: call.issueId ? "" : String(reason || "Urgent problem") }),
};

// Problem reports (resident calls Mia): one ticket per call, later details and "urgent" update the same ticket.
async function reportIssue({ text = "", urgent = false, reason = "" }) {
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
  if (!complaintFormAttempt.sessionId || complaintFormAttempt.text !== text) {
    complaintFormAttempt.sessionId = newComplaintId();
    complaintFormAttempt.eventId = `${complaintFormAttempt.sessionId}-1`;
    complaintFormAttempt.text = text;
  }
  return { sessionId: complaintFormAttempt.sessionId, eventId: complaintFormAttempt.eventId };
}

async function startCall() {
  if (!window.ElevenLabsClient) {
    call.error = "Voice is unavailable. Open Simulate complaint call below.";
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
    Object.assign(call, { status: "idle", error: `Couldn't start the call: ${error.message || error}. You can use Simulate complaint call below.` });
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
    actions = '<a class="button" href="#complaint-title">Report a problem</a>';
  }
  card.innerHTML = `
    <div class="call-head"><span class="call-icon"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/></svg></span><div>${head}</div><div class="call-actions">${actions}</div></div>
    ${lines ? `<div class="call-script">${lines}</div>` : ""}
    ${call.error ? `<p class="form-message error">${esc(call.error)}</p>` : ""}`;
  renderPhone();
}

function renderComplaintRecovery() {
  const box = document.getElementById("complaint-recovery");
  if (!box || !call.recovery) return;
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
  ["confirmed", "Contract confirmed"],
  ["active", "New supply running"],
];

// Resident page: the problems they reported, so they can see it reached the property team.
function renderIssues() {
  const order = latestOrder();
  const shareable = order && !["rejected", "cancelled"].includes(order.status);
  const shareOption = document.getElementById("share-option");
  if (shareOption) { shareOption.hidden = !shareable; shareOption.querySelector("input").disabled = !shareable; }
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
      return `<details class="fold"><summary>${i.urgent ? "Urgent complaint" : "Complaint"} · ${i.reply ? "Reply received" : "Awaiting " + to}</summary>
        <p>${esc(i.text)}</p>${handoff}${reply}${share}${form}</details>`;
    }).join("");
    if (box.dataset.html !== html && (!box.contains(document.activeElement) || box.dataset.unit !== UNIT)) {
      box.dataset.html = html; box.dataset.unit = UNIT; box.innerHTML = html;
    }
  }
}

function renderDecision() {
  const box = document.getElementById("decision");
  if (!box || !state.unit) return;
  const order = latestOrder();
  const contract = currentContract();
  byId("renewal-options").hidden = !state.offer_available || Boolean(order && order.status !== "rejected");

  if (order && order.status !== "rejected") {
    const reached = new Set(order.status_history.map((s) => s.status));
    const result = order.status_history.find((s) => s.result)?.result;
    const steps = RESIDENT_STEPS.map(([key, label]) => `<li class="${reached.has(key) ? "done" : ""}">${reached.has(key) ? "✓" : "○"} ${esc(label)}</li>`).join("");
    const readingNeeded = order.status === "needs_input" || (order.status === "confirmed" && order.reading_required !== false && order.switch_reading?.value_kwh == null);
    const boundary = new Date(order.requested_start + "T00:00:00Z");
    boundary.setUTCDate(boundary.getUTCDate() - 1);
    const readDate = boundary.toISOString().slice(0, 10);
    const needs = readingNeeded ? `<p class="notice notice-amber">Enter the switch reading from ${esc(prettyDate(readDate))} below. It will be passed to the supplier.</p>` : "";
    const done = result
      ? `<p class="notice">Your new contract ${esc(result.contract_number)} starts on ${esc(prettyDate(result.start))}.</p>` : "";
    const last = order.status_history.at(-1);
    const pending = last?.status === "pending" ? '<p class="notice">The supplier is still checking the start date.</p>' : "";
    const reading = order.switch_reading;
    const sent = reading?.value_kwh != null ? `<p class="muted small">Switch reading sent · ${esc(reading.value_kwh)} kWh · ${esc(prettyDate(reading.read_at))}</p>` : "";
    box.innerHTML = `<div class="decision-box"><div class="section-label"><span>${esc(order.product)} · your switch</span><span class="count-label">${esc(order.order_id)}</span></div><ul class="step-list">${steps}</ul>${pending}${needs}${done}${sent}</div>`;
    if (readingNeeded) { byId("reading-fold").open = true; byId("reading-date").value = readDate; }
    return;
  }
  if (!state.offer_available) { box.innerHTML = '<p class="muted small">No renewal offer is due for this flat.</p>'; return; }

  if (resident.declined) {
    box.innerHTML = `<div class="decision-box"><p><strong>You’re keeping ${esc(contract.supplier)}.</strong> Nothing was shared with any supplier.</p><button class="text-button" type="button" data-decide="undo">Changed your mind? Review the offers</button></div>`;
    return;
  }

  const offer = pickedOffer();
  const rejected = order?.status === "rejected"
    ? `<p class="notice notice-amber">${esc(order.partner)} couldn’t take your last order${order.status_history.slice(-1)[0].note ? ` (${esc(order.status_history.slice(-1)[0].note)})` : ""}. Pick another plan below. Nothing else was shared.</p>` : "";
  const saving = offer && contract.current_monthly_price_eur ? contract.current_monthly_price_eur - offer.estimated_monthly_price_eur : 0;
  box.innerHTML = `
    <div class="decision-box">
      ${rejected}
      ${offer ? `<p class="saving">${saving > 0 ? `You save about <b>${money(saving)}</b> a month (${money(saving * 12)} a year) compared with today.` : `This plan costs ${money(-saving)} more per month than today.`}</p>` : `<p class="muted small">Pick a plan above to continue.</p>`}
      <label class="consent">
        <input type="checkbox" id="consent-box" ${resident.consent ? "checked" : ""} ${offer ? "" : "disabled"}>
        <span>I agree to share ${SHARED_FIELDS.join(", ")} with ${esc(state.partner.partner)} for this demo order.</span>
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
  resident.busy = true; resident.error = ""; render();
  try {
    await post("/api/orders", { unit_id: UNIT, offer_id: offer.offer_id, consent_given: true, channel: "portal", signed_name: state.unit.tenant.name });
    resident.consent = false;
  } catch (error) {
    resident.error = error.message;
  }
  resident.busy = false;
  await refreshState();
}

/* ---------- 3. partner inbox ---------- */

const CALL_LINES = {
  submitted: (o) => [`Hello, this is the Reltix assistant with a new order.`, `${o.subject.tenant.name} at ${o.subject.address} would like your ${o.product} plan at ${money(o.estimated_monthly_price_eur)} a month, starting ${prettyDate(o.requested_start)}.`, `Can you take this order?`],
  received: () => [`Is the start date confirmed, or do you need a reading?`, `Confirming advances this flat’s demo to the final reading date.`],
  needs_input: () => [`I’ve asked the customer for the meter reading. I’ll call you back as soon as I have it.`],
  confirmed: (o) => [`Great, I’ve told the customer it starts on ${prettyDate(o.requested_start)}.`, `I’ll check in on the start day to confirm the power is running.`],
  active: () => [`All done. The contract is filed with the property. Thank you!`],
  rejected: () => [`Understood. I’ll offer the customer another plan. Thank you.`],
};
const PARTNER_ANSWERS = {
  submitted: [["received", "Yes, I’ll take it", "button-primary"], ["pending", "Still checking", ""], ["rejected", "No, decline", ""]],
  received: [["confirmed", "Confirm start date", "button-primary"], ["pending", "Still checking", ""], ["needs_input", "I need the meter reading", ""]],
  confirmed: [["active", "Start day: power is running", "button-primary"]],
};
const STATUS_TEXT = {
  submitted: "Customer chose your plan", received: "You accepted the order", needs_input: "You asked for the meter reading",
  confirmed: "You confirmed the contract", pending: "Checking start date", active: "Supply started", rejected: "You declined the order", cancelled: "Order cancelled",
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
    byId("job-timeline").innerHTML = "";
    byId("order-id-label").textContent = "";
    return;
  }
  const titles = { submitted: "Incoming supplier call", received: "Confirm the start date", needs_input: "Waiting for a reading",
    confirmed: "Start date confirmed", active: "Handover complete", rejected: "Order declined", cancelled: "Order cancelled" };
  byId("company-title").textContent = titles[order.status] || "Supplier order";
  byId("partner-lead").innerHTML = `Simulated supplier call · <strong id="partner-name">${esc(order.partner)}</strong>`;
  const reading = order.subject.meter.switch_reading?.value_kwh;
  const rows = [
    ["Customer", `${order.subject.tenant.name} · contact via centrix`],
    ["Address", order.subject.address],
    ["Plan", `${order.product} · ${money(order.estimated_monthly_price_eur)}/month`],
    ["Start", prettyDate(order.requested_start)],
    ["Meter", `${order.subject.meter.number} · ${reading != null ? `${Number(reading).toLocaleString("en-GB")} kWh` : "reading on the switch date"}`],
    ["Old contract", `${order.previous_contract.supplier}, ends ${prettyDate(order.previous_contract.ends)} · you cancel it`],
    ["Consent", `${order.consent.channel} · ${new Date(order.consent.given_at).toLocaleString("en-GB")}`],
  ];
  byId("order-card").innerHTML = `<div class="detail-list order-details">${rows.map(([k, v]) => `<div class="detail-row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("")}</div>`;
  byId("call-script").innerHTML = (CALL_LINES[order.status] || (() => []))(order).map((line) => `<div class="call-line"><b>Assistant</b> ${esc(line)}</div>`).join("");
  byId("partner-actions").innerHTML = (PARTNER_ANSWERS[order.status] || [])
    .map(([status, label, cls]) => `<button class="button ${cls}" type="button" data-partner="${status}">${esc(label)}</button>`).join("")
    + (order.status === "needs_input" ? `<span class="muted small">Waiting for the customer…</span>` : "");
  byId("order-id-label").textContent = order.order_id;
  byId("job-timeline").innerHTML = order.status_history.slice().reverse().map((e) => `
    <li><strong>${esc(STATUS_TEXT[e.status] || e.status)}</strong><span class="muted">${new Date(e.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })} · ${esc(e.by === "centrix" ? "centrix" : "you")}${e.note ? ` · ${esc(e.note)}` : ""}${e.result ? ` · contract ${esc(e.result.contract_number)}` : ""}</span></li>`).join("");
}

async function partnerAnswer(status) {
  const note = status === "needs_input" ? "meter reading" : status === "rejected" ? "not available for this postcode" : "";
  try {
    await post(`/api/orders/${latestOrder().order_id}/status`, { status, note });
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
  call: "Call", choice: "Plan chosen on the call", case: "Resident file updated", reading: "Meter reading", link: "Link sent", declined: "Resident declined",
  callback: "Callback requested", handoff: "Needs a person", consent: "Consent given and signed", order: "Order sent to partner",
  received: "Partner accepted", needs_input: "Partner needs input", confirmed: "Contract confirmed", rejected: "Partner declined",
  active: "New supply running", filed: "Filed on the unit", cancelled: "Order cancelled", issue: "Problem reported",
  issue_reply: "Complaint answered", issue_shared: "Supplier follow-up requested", pending: "Supplier checking start date",
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
    ? log.map((c) => `<div class="detail-row"><span>${esc(new Date(c.given_at).toLocaleString("en-GB"))} · ${esc(c.channel)}</span><strong>${esc(c.partner)}: ${esc(c.data_shared.join(", "))}</strong></div>`).join("")
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
      tile("!", (run.attention || 0) + (k.issues_urgent || 0), "Need a person", `${k.issues_open || 0} open complaints`, "amber"),
    ].join("");
    if (kpis.dataset.html !== html) { kpis.dataset.html = html; kpis.innerHTML = html; }
  }
  const slot = document.getElementById("clock-slot");
  if (slot && !slot.querySelector("#demo-date")) slot.innerHTML = `<label class="clock"><span>Demo date</span><input type="date" id="demo-date" value="${esc(portfolio.today)}"></label>`;
  else if (slot && document.activeElement?.id !== "demo-date") slot.querySelector("#demo-date").value = portfolio.today;
  const html = `
    <div class="panel-head"><h2>Today’s run</h2><span class="muted small">${run ? `${esc(prettyDate(run.date))} · ${new Date(run.ran_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })} · ${esc(run.source)}` : ""}</span></div>
    <p class="muted small">Matched residents receive a renewal offer in their portal. Phone calls handle complaints and supplier follow-up.</p>
    ${run ? `<div class="run-table-wrap"><table class="run-table"><thead><tr><th>Flat</th><th>Resident</th><th>Decision</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="muted">No run yet.</p>`}
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
function syncFromCase() {
  const c = serverCase();
  if (c.chosen_offer_id && c.chosen_offer_id !== syncedChoice) {
    syncedChoice = c.chosen_offer_id;
    const index = offers().findIndex((o) => o.offer_id === c.chosen_offer_id);
    if (index >= 0 && chosenOffer !== index) { chosenOffer = index; renderOffers("tenant-offers", true); }
  }
  if (typeof c.declined === "boolean") resident.declined = c.declined;
}

function render() {
  syncFromCase();
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
  if (form.id !== "problem-form" && form.id !== "finish-dropped-call" && !form.dataset.issue) return;
  event.preventDefault();
  const button = form.querySelector("button");
  if (button.disabled) return;
  button.disabled = true;
  const data = new FormData(form);
  const feedback = form.querySelector('[role="status"]') || byId("issue-feedback");
  try {
    if (form.id === "problem-form") {
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
    await endCall();
    Object.assign(call, { status: "idle", transcript: [], issueId: null, error: "", muted: false });
    Object.assign(complaintFormAttempt, { sessionId: null, eventId: null, text: null });
    Object.assign(resident, { declined: false, consent: false, busy: false, error: "" });
    chosenOffer = null;
    await post("/api/reset");
    await refreshState();
    await loadPortfolio();
  }
});
document.addEventListener("change", async (event) => {
  if (event.target.id === "consent-box") { resident.consent = event.target.checked; renderDecision(); }
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
if (location.hash === "#complaint-demo" && ROLE === "tenant") byId("complaint-demo").open = true;

window.flow = { render, renderPartner };
render();
})();
