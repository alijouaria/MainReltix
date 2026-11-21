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
  incoming: null,        // a call centrix queued for this flat, currently ringing on this page
  reason: "renewal",     // renewal | callback | problem
  simulated: false,
  linkSent: null,
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
// Calls Mia makes to the resident: the daily check's renewal call, a callback they asked for, or "call now" by staff.
// Supplier follow-ups also live in the call list, so only these kinds ring here. A callback rings once it's due.
const MIA_CALLS = ["renewal", "callback", "manual"];
const isDue = (c) => !c.due_at || Date.parse(c.due_at) <= Date.now();
const handledCalls = new Set(); // answered/declined/simulated here, even if the server hasn't caught up yet
const queuedCall = () => (state.mia_calls || []).slice().reverse().find((c) => MIA_CALLS.includes(c.kind) && ["queued", "ringing"].includes(c.status)
  && isDue(c) && !handledCalls.has(c.call_id)) || null;
const setCallStatus = (c, status) => c && post(`/api/calls/${c.call_id}`, { status }).catch(() => {});
const newComplaintId = () => globalThis.crypto?.randomUUID?.() || `complaint-${Date.now()}-${Math.random().toString(16).slice(2)}`;
// Everything the resident tells Mia or the portal is stored once on the flat (main.py update_case), so all pages show it.
const saveCase = (patch, source = "voice") => post("/api/case", { unit_id: UNIT, source, ...patch }).then(() => refreshState()).catch(() => {});
const serverCase = () => state.case || {};
const offers = () => state.partner?.offers || [];
const latestOrder = () => (state.orders || []).slice(-1)[0] || null;
const firstName = () => (state.unit?.tenant?.name || "there").split(" ")[0];
// The contract Mia talks about on renewal calls (the running one, not a newly signed one).
const currentContract = () => (state.unit?.contracts || []).find((c) => c.type === "energy_supply" && c.status !== "signed") || energyContract();
const pickedOffer = () => offers().find((offer) => offer.offer_id === chosenOfferId) || null;
const bestOffer = () => offers().reduce((a, b) => (Number(b.estimated_monthly_price_eur) < Number(a.estimated_monthly_price_eur) ? b : a), offers()[0]);
const spokenDate = (iso) => (iso ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" }).format(new Date(`${iso}T12:00:00`)) : "soon");
function selectOffer(offerId) {
  const offer = offers().find((o) => o.offer_id === offerId);
  if (!offer) return null;
  chosenOfferId = offer.offer_id;
  chosenOfferVersion = offer.offer_version;
  syncedChoice = offer.offer_id;
  renderOffers("tenant-offers", true);
  return offer;
}
const SHARED_FIELDS = ["my name", "address", "meter number", "switch reading", "and start date"];

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

/* Mia's tools. Renewal calls collect the choice by voice; signing and consent still happen in the portal.
   Complaint calls (reason "problem") turn notes into a ticket. */
const clientTools = {
  get_offers: () => {
    const now = Number(currentContract().current_monthly_price_eur || 0);
    const cheapest = bestOffer();
    return JSON.stringify({ current_monthly_price_eur: now, offers: offers().map((o) => ({
      offer_id: o.offer_id, name: o.product, est_monthly_eur: o.estimated_monthly_price_eur,
      saving_per_month_eur: Math.round((now - o.estimated_monthly_price_eur) * 100) / 100,
      contract_months: o.contract_months, renewable_percent: o.renewable_share_percent, cheapest: o === cheapest })) });
  },
  record_choice: ({ offer_id }) => {
    const offer = selectOffer(String(offer_id));
    if (!offer) return "Unknown offer_id. Call get_offers and use one of those ids.";
    saveCase({ chosen_offer_id: offer.offer_id, offer_version: offer.offer_version });
    render();
    return `Saved: ${offer.product}.`;
  },
  send_portal_link: ({ channel }) => {
    const via = channel === "email" ? "email" : "SMS";
    call.linkSent = via;
    saveCase({ preferred_channel: via, note: `Confirm link sent by ${via}.` });
    return `Link sent by ${via}.`;
  },
  mark_declined: ({ reason }) => {
    resident.declined = true;
    saveCase({ declined: true, reason: reason || "said no on the call" });
    render();
    return "Recorded. Nothing will be shared with the partner.";
  },
  request_callback: ({ preferred_time }) => {
    const when = String(preferred_time || "later");
    saveCase({ callback: when });
    return /\d|second|minute|hour/i.test(when)
      ? `Callback scheduled ${when}. Confirm the time in a few words, say goodbye and end the call so the line is free.`
      : `Callback noted for ${when}; the property team will call then. Say goodbye and end the call.`;
  },
  save_meter_reading: async ({ value_kwh }) => {
    const value = Number(String(value_kwh).replace(",", ".").replace(/[^0-9.]/g, ""));
    if (!value) return "That didn't sound like a meter reading. Ask again, number only, in kWh.";
    try { await post("/api/reading", { unit_id: UNIT, value_kwh: value, source: "voice" }); await refreshState(); }
    catch (error) { return `Couldn't save it: ${error.message}. Tell them they can add it in the portal.`; }
    return `Saved meter reading ${value} kWh.`;
  },
  // Memory: Mia can look up what this resident chose, signed, reported or asked for, as it stands right now.
  get_customer_status: async () => {
    const fresh = await freshState();
    const status = state.agent_variables?.customer_status || "No record for this resident.";
    return fresh ? status : `${status} (Could not refresh; this may be a few minutes old.)`;
  },
  // The resident says a problem they reported is fixed: close that complaint.
  resolve_issue: async ({ issue_id, note }) => resolveIssue(issue_id, note),
  save_note: async ({ note }) => {
    if (!note) return "Nothing to save.";
    if (call.reason === "problem") {
      // "It's fixed now" is not a new complaint: close the open one instead of opening another ticket.
      if (!call.issueId && FIXED_WORDS.test(String(note)) && openIssues().length) return resolveIssue("", String(note));
      if (call.resolvedIssue && !call.issueId) { saveCase({ note: String(note) }); return "Noted in the resident's file."; }
      return reportIssue({ text: String(note) });
    }
    saveCase({ note: String(note) });
    return "Noted in the resident's file.";
  },
  handoff_to_human: ({ reason }) => {
    if (call.reason === "problem") return reportIssue({ urgent: true, reason, text: call.issueId ? "" : String(reason || "Urgent problem") });
    saveCase({ needs_person: reason || "resident asked for a person" });
    return "A property manager will follow up.";
  },
};

// Fresh copy of the resident's file. The page polls in parallel and a request can fail now and then
// (busy laptop, phone tunnel), so retry a few times and fall back to the last copy we have.
async function freshState() {
  for (let attempt = 0; attempt < 4; attempt++) {
    try { await refreshState(); return true; } catch { await new Promise((r) => setTimeout(r, 300 * (attempt + 1))); }
  }
  return false;
}
const FIXED_WORDS = /\b(fixed|resolved|sorted|solved|repaired|working again|works again|no longer|all good now)\b/i;
const openIssues = () => (state.issues || []).filter((i) => !i.resolved && !i.reply);
async function resolveIssue(issueId, note) {
  await freshState();
  const open = openIssues();
  const issue = open.find((i) => i.issue_id === issueId) || (open.length === 1 ? open[0] : null)
    || (!issueId && open.length ? open[open.length - 1] : null);   // "it's fixed" with no id: the most recent one
  if (!issue) return open.length ? `Which one? Open complaints: ${open.map((i) => `${i.issue_id}: ${i.text}`).join("; ")}` : "There are no open complaints on file.";
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await post("/api/issues", { unit_id: UNIT, issue_id: issue.issue_id, resolved: true, note: String(note || "").slice(0, 200), source: "voice" });
      call.resolvedIssue = issue.issue_id;
      freshState();
      return `Marked as fixed: ${issue.text}. The property team can see it's closed.`;
    } catch (error) {
      if (attempt === 2) return `Couldn't update it: ${error.message}`;
      await new Promise((r) => setTimeout(r, 400));
    }
  }
}

// Problem reports (resident calls Mia): one ticket per call, later details and "urgent" update the same ticket.
async function reportIssue({ text = "", urgent = false, reason = "" }) {
  call.sessionId ||= newComplaintId();
  const normalized = JSON.stringify([text, urgent, reason]);
  if (!call.pendingEvent || call.pendingEvent.payload !== normalized) {
    call.pendingEvent = { payload: normalized, id: `${call.sessionId}-${++call.eventNumber}` };
  }
  // Same event id on every retry, so the server never stores it twice.
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const { issue } = await post("/api/issues", { unit_id: UNIT, source: "voice", issue_id: call.issueId, text, urgent, reason,
        call_session_id: call.sessionId, client_event_id: call.pendingEvent.id });
      call.issueId = issue.issue_id;
      call.pendingEvent = null;
      freshState();
      return issue.urgent ? `Saved as urgent complaint ${issue.issue_id}. The property team will contact them soon.` : `Saved as complaint ${issue.issue_id} for the property team.`;
    } catch (error) {
      if (attempt === 3) return `NOT SAVED (${error.message}). Tell the resident you couldn't log it and they should report it in the portal.`;
      await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
    }
  }
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

// Phones only allow sound after the user has tapped the page once, so we unlock audio on the first tap.
let audioCtx = null;
const audioReady = () => audioCtx && audioCtx.state === "running";
function unlockAudio() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    audioCtx.resume().then(renderCall);
  } catch { /* no audio */ }
}
document.addEventListener("pointerdown", unlockAudio);

let ringer = null;
function ring(on) {
  if (on && !ringer) {
    const tone = () => {
      if (audioReady()) {
        for (const [freq, start] of [[440, 0], [480, 0], [440, 0.5], [480, 0.5]]) { // classic two-tone ring
          const o = audioCtx.createOscillator(); const g = audioCtx.createGain();
          o.frequency.value = freq; g.gain.value = 0.12; o.connect(g); g.connect(audioCtx.destination);
          o.start(audioCtx.currentTime + start); o.stop(audioCtx.currentTime + start + 0.4);
        }
      }
      navigator.vibrate?.([400, 200, 400]); // Android buzz (iPhone browsers don't allow vibration)
      document.title = document.title.startsWith("📞") ? "Reltix · Resident" : "📞 Incoming call · reltix";
    };
    tone();
    ringer = { timer: setInterval(tone, 2000) };
  } else if (!on && ringer) {
    clearInterval(ringer.timer); ringer = null;
    document.title = "Reltix · Resident";
  }
}

function checkIncoming() {
  const c = queuedCall();
  if (c && idleish() && !call.incoming) {
    call.incoming = c;
    if (c.status === "queued") setCallStatus(c, "ringing");
    ring(true);
  }
  if (!c && call.incoming && idleish()) { call.incoming = null; ring(false); }
}
async function answerIncoming() {
  ring(false);
  if (call.incoming) handledCalls.add(call.incoming.call_id);
  setCallStatus(call.incoming, "answered");
  await startCall(call.incoming?.kind === "callback" ? "callback" : "renewal");
}
function declineIncoming() {
  ring(false);
  if (call.incoming) handledCalls.add(call.incoming.call_id);
  setCallStatus(call.incoming, "declined");
  Object.assign(call, { incoming: null, status: "ended", linkSent: "SMS", transcript: [], reason: "renewal" });
  render();
}
function simulateCall() {
  const answered = call.incoming;
  if (answered) handledCalls.add(answered.call_id);
  call.incoming = null;
  ring(false);
  if (answered) setCallStatus(answered, "answered").then(() => setCallStatus(answered, "done"));
  const offer = bestOffer();
  const contract = currentContract();
  if (!offer) return;
  Object.assign(call, { status: "ended", simulated: true, reason: "renewal", error: "", linkSent: "SMS", transcript: [
    { who: "Mia", text: `Hi ${firstName()}, this is Mia from reltix. Your electricity contract with ${contract.supplier} ends on ${spokenDate(contract.expiry_date)}. Do you have two minutes?` },
    { who: "You", text: "Sure." },
    { who: "Mia", text: `${offer.product} from ${state.partner.partner} is about ${Math.round(offer.estimated_monthly_price_eur)} euros a month, roughly ${Math.round(contract.current_monthly_price_eur - offer.estimated_monthly_price_eur)} less than now. Shall I send you the details?` },
    { who: "You", text: "Yes, by SMS please." },
  ] });
  selectOffer(offer.offer_id);
  saveCase({ chosen_offer_id: offer.offer_id, offer_version: offer.offer_version, preferred_channel: "SMS" });
  logEvent("call", `(Simulated) Mia called ${state.unit.tenant.name}; chose ${offer.product}; link sent by SMS.`);
  render();
}

async function startCall(reason = "renewal") {
  // Live voice sends audio to ElevenLabs: complaint calls need the consent box; answering Mia's call counts as consent.
  const voiceConsent = byId("voice-consent-box");
  if (reason === "problem" && !voiceConsent?.checked) return;
  if (!window.ElevenLabsClient) {
    call.error = reason === "problem" ? "Voice calling is unavailable right now. Please try again later." : "Voice is unavailable. Use “No mic? Simulate” instead.";
    return render();
  }
  const contract = currentContract();
  if (reason === "problem") { voiceConsent.checked = false; byId("call-agent-button").disabled = true; }
  try {
    await post("/api/privacy/consent", { unit_id: UNIT, purpose: "live_voice", granted: true });
  } catch (error) {
    call.error = `Could not record your consent, so the live call was not started: ${error.message}`;
    return render();
  }
  Object.assign(call, { status: "connecting", transcript: [], error: "", issueId: null, resolvedIssue: null, sessionId: newComplaintId(),
    eventNumber: 0, pendingEvent: null, recovery: null, reason, simulated: false, linkSent: null });
  render();
  await freshState();   // Mia starts with the latest record (choices, complaints, notes)
  const vars = { ...state.agent_variables };
  if (reason === "problem") {
    const open = openIssues();
    const last = open[open.length - 1];
    const topic = last ? last.text.replace(/[.!?].*$/, "").toLowerCase().split(" ").slice(0, 8).join(" ") : "";
    Object.assign(vars, { call_reason: "problem_report", first_message: last
      ? `Hi ${firstName()}, it's Mia from reltix. Is this about the ${topic.startsWith("the ") ? topic.slice(4) : topic} you reported, or something new?`
      : `Hi ${firstName()}, it's Mia from reltix. What's going on at home?` });
    logEvent("call", `${state.unit.tenant.name} called Mia to report a problem.`);
  } else if (reason === "callback") {
    Object.assign(vars, { call_reason: "callback", first_message: `Hi ${firstName()}, it's Mia from reltix again, calling you back as promised about your electricity contract. Is now a better time?` });
    logEvent("call", `Mia called ${state.unit.tenant.name} back, as they asked.`);
  } else {
    logEvent("call", `Mia called ${state.unit.tenant.name}: contract with ${contract.supplier} ends ${prettyDate(contract.expiry_date)}.`);
  }
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
        if (call.incoming) { setCallStatus(call.incoming, "done"); call.incoming = null; }
        render();
        if (call.reason === "problem") recoverDroppedCall();
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
    Object.assign(call, { status: "idle", incoming: null, error: `Couldn't start the call: ${error.message || error}. Please try again later.` });
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
  } else if (call.incoming && idleish()) {
    const c = call.incoming;
    head = `<strong><span class="live-dot"></span> Incoming call · reltix${c.urgent ? " · urgent" : ""}</strong><span class="muted small">${c.kind === "callback" ? "Mia is calling you back, as you asked." : "centrix's daily check found your contract ending soon and asked Mia to call you."}${audioReady() ? "" : " <b>Tap anywhere to hear it ring.</b>"}</span>`;
    actions = `<button class="button button-primary" type="button" data-call="answer">Answer</button><button class="text-button" type="button" data-call="decline">Decline</button><button class="text-button" type="button" data-call="simulate">No mic? Simulate</button>`;
  } else if (call.status === "ended" && call.reason === "problem") {
    head = "<strong>Complaint call ended</strong><span class='muted small'>See your reports and replies under Report a problem.</span>";
    actions = "";
  } else if (call.status === "ended") {
    head = `<strong>${call.simulated ? "Call (simulated)" : "Call with Mia"} · ended</strong><span class="muted small">${call.linkSent ? `Link sent by ${esc(call.linkSent)} · this page picks up where the call left off.` : "Your choices are saved below."}</span>`;
    actions = state.offer_available ? `<button class="text-button" type="button" data-call="start">Call Mia again</button>` : "";
  } else {
    head = "<strong>Mia, your reltix assistant</strong><span class='muted small'>Calls you when your contract is ending and takes complaints by voice.</span>";
    actions = state.offer_available ? '<button class="button button-primary" type="button" data-call="start">Talk to Mia about my renewal</button>' : "";
  }
  card.classList.toggle("ringing", !!call.incoming && idleish());
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
  const ringingNow = !!call.incoming && idleish();
  const live = call.status === "connecting" || call.status === "connected";
  if (!ringingNow && !live) { el?.remove(); document.body.classList.remove("in-call"); return; }
  if (!el) { el = document.createElement("div"); el.id = "phone-screen"; el.setAttribute("role", "dialog"); el.setAttribute("aria-label", "Phone call"); document.body.appendChild(el); }
  document.body.classList.add("in-call");
  const who = state.unit?.tenant?.name || "";
  let sub = call.status === "connecting" ? "Connecting…" : `<span id="phone-timer"></span> · ${call.muted ? "muted" : call.mode === "speaking" ? "Mia is speaking" : "listening"}`;
  let buttons = `
      <div class="phone-btn-wrap"><button class="phone-btn mute${call.muted ? " on" : ""}" type="button" data-call="mute" aria-label="${call.muted ? "Unmute" : "Mute"}" ${call.status !== "connected" ? "disabled" : ""}>${ICON_MIC(call.muted)}</button><span>${call.muted ? "Unmute" : "Mute"}</span></div>
      <div class="phone-btn-wrap"><button class="phone-btn decline" type="button" data-call="end" aria-label="Hang up">${ICON_PHONE}</button><span>End</span></div>`;
  if (ringingNow) {
    sub = `${call.incoming.kind === "callback" ? "Calling you back" : "Incoming call"}${call.incoming.urgent ? " · urgent" : ""}${audioReady() ? "" : `<br><span class="phone-tap-hint">Tap anywhere to hear it ring</span>`}`;
    buttons = `
      <div class="phone-btn-wrap"><button class="phone-btn decline" type="button" data-call="decline" aria-label="Decline">${ICON_PHONE}</button><span>Decline</span></div>
      <div class="phone-btn-wrap"><button class="phone-btn answer" type="button" data-call="answer" aria-label="Answer">${ICON_PHONE}</button><span>Answer</span></div>`;
  }
  const last = call.transcript.slice(-2).map((l) => `<p class="${l.who === "You" ? "you" : ""}"><b>${esc(l.who)}:</b> ${esc(l.text)}</p>`).join("");
  const cls = ringingNow ? "ringing" : call.mode === "speaking" ? "speaking" : "";
  if (el.className !== cls) el.className = cls;
  // Each part updates on its own, so a tap on a button is never lost when the text above it changes.
  if (!el.firstChild) el.innerHTML = `<div class="phone-top"></div><div class="phone-lines"></div><div class="phone-bottom"></div>`;
  const set = (sel, html) => { const part = el.querySelector(sel); if (part.dataset.html !== html) { part.dataset.html = html; part.innerHTML = html; } };
  set(".phone-top", `
      <div class="call-orb" aria-hidden="true"><span class="ring r1"></span><span class="ring r2"></span><span class="sphere"></span></div>
      <div class="wave" aria-hidden="true">${"<i></i>".repeat(9)}</div>
      <h2>Mia · reltix</h2>
      <p class="phone-sub">${sub}</p>
      <p class="phone-about">${ringingNow ? `About your electricity contract${who ? ` · ${esc(who)}` : ""}<br><span class="phone-consent">Mia is an AI voice by ElevenLabs. Answering sends your voice and contract details to ElevenLabs for this call.</span>` : call.reason === "problem" ? "Reporting a problem · speak normally" : "Speak normally, like on a phone call"}</p>`);
  set(".phone-lines", (live ? last : "") + (call.error ? `<p class="err">${esc(call.error)}</p>` : ""));
  set(".phone-bottom", `<div class="phone-buttons">${buttons}</div>
    ${ringingNow ? `<button class="phone-simulate" type="button" data-call="simulate">No mic? Simulate the call</button>` : ""}`);
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
      if (i.resolved && !i.reply) {
        return `<details class="fold resolved"${audience === "reltix" ? ` id="reply-${esc(i.issue_id)}"` : ""}><summary>✓ Fixed · ${esc(i.text.slice(0, 48))}</summary>
        <p>${esc(i.text)}</p><p class="muted small">Resident said it's fixed${i.resolved.source ? ` (${esc(i.resolved.source)})` : ""}${i.resolved.note ? `: ${esc(i.resolved.note)}` : ""}.</p></details>`;
      }
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
  const lastCall = (state.mia_calls || []).slice(-1)[0];
  const chosen = offers().find((o) => o.offer_id === c.chosen_offer_id);
  const tenant = state.unit.tenant?.name || "No resident";
  const node = (id, title, line, on, tone) => ({ id, title, line, on, tone });
  const resident = node("resident", tenant, chosen ? `Chose ${chosen.product}` : lastCall ? `Mia: ${CALL_STATE[lastCall.status] || lastCall.status}` : (state.issues || []).length ? "Reported a problem" : "Waiting", !!(lastCall || chosen || (state.issues || []).length), "cyan");
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
  supplier_follow_up: "Supplier callback queued", attention: "⚠ Needs a person", handled: "Handled by staff", reading_requested: "Switch reading requested", meter_identifier: "Meter number added",
  start_date_mismatch: "Supplier proposed a different start date",
};

let timelineAll = false;
function renderManager() {
  if (!state.unit || !document.getElementById("case-timeline")) return;
  const events = state.events || [];
  const contract = energyContract();
  const remaining = daysUntil(contract.expiry_date);
  const detected = { type: "detected", at: null, channel: "centrix", text: `Contract with ${contract.supplier} ends ${prettyDate(contract.expiry_date)} (${remaining} days). Case opened ${contract.renewal_notice_days || 30} days before expiry.` };
  const all = !state.offer_available || events.some((e) => e.type === "detected") ? events : [detected, ...events];
  byId("landlord-title").textContent = `Flat ${UNIT.replace("unit_", "").toUpperCase()}${state.unit.tenant?.name ? ` · ${state.unit.tenant.name}` : ""}${state.following ? " · live" : ""}`;
  byId("case-count").textContent = events.length || "";
  // Condensed timeline: repeated steps of the same kind (calls, file updates…) fold into one line you can expand.
  const groups = [];
  for (const e of all.slice().reverse()) {
    const last = groups[groups.length - 1];
    if (last && last.type === e.type) last.items.push(e); else groups.push({ type: e.type, items: [e] });
  }
  const hhmm = (at) => new Date(at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const title = (t) => (t === "detected" ? "Expiry detected" : EVENT_TITLES[t] || t);
  const line = (e) => `${e.at ? `${hhmm(e.at)} · ${esc(e.channel)} · ` : ""}${esc(e.text)}`;
  const shownGroups = timelineAll ? groups : groups.slice(0, 8);
  byId("case-timeline").innerHTML = shownGroups.map((g) => {
    const e = g.items[0];
    const cls = /issue|attention|handoff/.test(g.type) ? "warn" : /active|consent|confirmed|filed|issue_resolved/.test(g.type) ? "good" : /call|callback/.test(g.type) ? "call" : "";
    if (g.items.length === 1) return `<li class="${cls}"><strong>${esc(title(g.type))}</strong><span class="muted">${line(e)}</span></li>`;
    return `<li class="${cls}"><details class="tl-group"><summary><strong>${esc(title(g.type))} <span class="tl-x">×${g.items.length}</span></strong><span class="muted">latest ${line(e)}</span></summary>
      <ul>${g.items.slice(1).map((x) => `<li class="muted small">${line(x)}</li>`).join("")}</ul></details></li>`;
  }).join("") || `<li class="muted small">Nothing yet. Steps appear here as the resident, Mia and the supplier act.</li>`;
  const tl = byId("tl-toggle");
  tl.hidden = groups.length <= 8;
  tl.textContent = timelineAll ? "Show less" : `Show all ${groups.length}`;

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
    const slot = document.getElementById("resident-said-slot");
    if (slot) slot.appendChild(told); else document.querySelector("#landlord-view .status-list")?.insertAdjacentElement("afterend", told);
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
    ...(state.issues || []).slice(-5).map((i) => [i.resolved ? "✓ Fixed" : i.urgent ? "⚠ Urgent problem" : "Problem reported", `${i.text} (${new Date(i.reported_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })})`]),
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
  const openIssues = (state.issues || []).filter((i) => !i.reply && !i.resolved);
  const cc = document.getElementById("complaint-count");
  if (cc) { cc.textContent = openIssues.length || ""; cc.classList.toggle("hot", openIssues.some((i) => i.urgent)); }
  const none = document.getElementById("no-complaints");
  if (none) none.hidden = (state.issues || []).length > 0;
  if (document.getElementById("case-actions")) {
    const lastCall = (state.mia_calls || []).slice(-1)[0];
    const busy = lastCall && ["queued", "ringing"].includes(lastCall.status);
    setHtml("case-actions", state.unit.tenant ? `
      <button type="button" class="mini-btn primary" data-callnow="${UNIT}" ${busy ? "disabled" : ""}>☎ ${busy ? "Mia is calling…" : "Mia calls now"}</button>
      <button type="button" class="mini-btn" data-qr="${UNIT}">📱 QR for phone</button>
      <a class="mini-btn" href="/tenant?unit=${UNIT}" target="_blank" rel="noopener">Resident view ↗</a>
      <a class="mini-btn" href="/reltix?unit=${UNIT}#partner">Supplier order</a>` : `<span class="muted small">Vacant flat. Nothing to call.</span>`);
  }
  const navCase = document.getElementById("nav-case-flat");
  if (navCase) navCase.textContent = flatId(UNIT);
  byId("contract-list").innerHTML = energy.map((c) => `<div class="detail-row"><span><span class="badge ${c.status === "active" ? "badge-green" : c.status === "ended" ? "" : "badge-amber"}">${esc(c.status)}</span> ${esc(c.supplier)}</span><strong>${esc(c.product)} · ${money(c.current_monthly_price_eur)}/mo · ${c.expiry_date ? `ends ${esc(prettyDate(c.expiry_date))}` : `starts ${esc(prettyDate(c.start))}`}</strong></div>`).join("");
}

/* ---------- 5. staff: today's automatic run across the portfolio ---------- */

let portfolio = null;
async function loadPortfolio() {
  if (!document.getElementById("portfolio")) return;
  try { portfolio = await (await fetch("/api/portfolio", { cache: "no-store" })).json(); } catch { return; }
  renderPortfolio();
}

const DECISION = { matched: ["badge-green", "Mia calls"], attention: ["badge-amber", "⚠ Needs a person"], skipped: ["", "Skipped"] };
const CALL_STATE = { queued: "call queued", ringing: "ringing…", answered: "on the call", done: "called", declined: "declined → SMS", missed: "no answer → SMS", sms_sent: "SMS sent" };

// Flat list: built to stay short with 100+ flats. Filters, search, compact rows, "show more".
const flats = { filter: "active", query: "", limit: 10 };
const FILTERS = [
  ["active", "Active", (r) => r.decision !== "skipped"],
  ["attention", "Needs a person", (r) => r.decision === "attention"],
  ["matched", "Mia calling", (r) => r.decision === "matched"],
  ["skipped", "Skipped", (r) => r.decision === "skipped"],
  ["all", "All", () => true],
];
const flatId = (uid) => uid.replace("unit_", "").toUpperCase();
const shortDate = (iso) => (iso ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" }).format(new Date(`${iso}T12:00:00`)) : "—");

function renderPortfolio() {
  const box = document.getElementById("portfolio");
  if (!box || !portfolio) return;
  const run = portfolio.run;
  const results = run?.results || [];
  const calls = portfolio.mia_calls || [];
  const reviews = portfolio.review_queue || [];
  const callFor = (uid) => calls.filter((c) => c.unit_id === uid).slice(-1)[0];

  // header + toolbar are built once, so typing in the search box never loses focus
  if (!box.querySelector("#flat-rows")) {
    box.innerHTML = `
      <div class="panel-head"><h2>Flats</h2><span class="muted small" id="run-meta"></span></div>
      <div class="flat-tools">
        <input type="search" id="flat-search" placeholder="Search flat, resident or street  ( / )" aria-label="Search flats" autocomplete="off">
        <div class="filter-chips" id="flat-filters" role="tablist"></div>
      </div>
      <div id="flat-rows" class="flat-rows" role="list"></div>
      <div class="flat-foot" id="flat-foot"></div>`;
  }
  byId("run-meta").textContent = run ? `checked ${shortDate(run.date)} · ${new Date(run.ran_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}` : "";
  const chips = FILTERS.map(([key, label, test]) => `<button type="button" role="tab" class="filter-chip ${flats.filter === key ? "on" : ""} ${key}" data-filter="${key}" aria-selected="${flats.filter === key}">${label}<span>${results.filter(test).length}</span></button>`).join("");
  const chipBox = byId("flat-filters");
  if (chipBox.dataset.html !== chips) { chipBox.dataset.html = chips; chipBox.innerHTML = chips; }

  const q = flats.query.trim().toLowerCase();
  const order = { attention: 0, matched: 1, skipped: 2 };
  const test = FILTERS.find(([key]) => key === flats.filter)[2];
  const list = results.filter(test)
    .filter((r) => !q || [r.unit_id.replace("unit_", ""), r.tenant, r.address, r.reason].join(" ").toLowerCase().includes(q))
    .sort((a, b) => order[a.decision] - order[b.decision] || (a.days_left ?? 9999) - (b.days_left ?? 9999));
  const shown = list.slice(0, flats.limit);
  const rows = shown.map((r) => {
    const [cls, label] = DECISION[r.decision];
    const c = callFor(r.unit_id);
    const callBadge = c ? `<span class="badge badge-soft">${esc(CALL_STATE[c.status] || c.status)}</span>` : "";
    const ends = r.expiry ? `${shortDate(r.expiry)} · ${r.days_left < 0 ? `${-r.days_left} d ago` : `${r.days_left} d`}` : "no end date";
    const canCall = r.tenant && !(c && ["queued", "ringing"].includes(c.status));
    return `<div class="flat-row ${r.decision} ${r.unit_id === UNIT ? "current" : ""}" role="listitem" data-open="${r.unit_id}" tabindex="0" title="${esc(r.reason)}">
      <span class="fr-id"><strong>${flatId(r.unit_id)}</strong><small>${esc(r.address.split(",")[0])}</small></span>
      <span class="fr-who"><strong>${esc(r.tenant || "Vacant")}</strong><small>${esc(ends)}</small></span>
      <span class="fr-status"><span class="badge ${cls}">${label}</span>${callBadge}</span>
      <span class="fr-reason">${esc(r.reason)}</span>
      <span class="fr-actions">
        ${canCall ? `<button type="button" class="mini-btn" data-callnow="${r.unit_id}" title="Mia calls ${esc(r.tenant)} now">☎ Call</button>` : ""}
        <details class="row-menu"><summary aria-label="Open views">⋯</summary><div>
          <a href="/reltix?unit=${r.unit_id}#case">Case</a>${r.tenant ? `<button type="button" data-qr="${r.unit_id}">📱 QR for phone</button><a href="/tenant?unit=${r.unit_id}" target="_blank" rel="noopener">Resident view</a><a href="/supplier?unit=${r.unit_id}">Supplier view</a><a href="/owner?unit=${r.unit_id}">Owner view</a>` : ""}
        </div></details>
      </span></div>`;
  }).join("") || `<p class="muted small empty">${q ? `No flat matches “${esc(flats.query)}”.` : "Nothing here."}</p>`;
  const rowBox = byId("flat-rows");
  if (rowBox.dataset.html !== rows && !rowBox.querySelector("details[open]")) { rowBox.dataset.html = rows; rowBox.innerHTML = rows; }
  const foot = list.length > shown.length
    ? `<button type="button" class="text-button" data-more="1">Show ${Math.min(10, list.length - shown.length)} more</button><span class="muted small">${shown.length} of ${list.length}</span>`
    : `<span class="muted small">${list.length} ${list.length === 1 ? "flat" : "flats"}</span>`;
  const footBox = byId("flat-foot");
  if (footBox.dataset.html !== foot) { footBox.dataset.html = foot; footBox.innerHTML = foot; }

  renderKpis();
  renderInbox();
  renderFeed();
  renderDatebar();
  renderPipeline();
  renderCallsCard();
  renderSoon();
  renderPartnerBoard();
}

/* ---------- overview widgets ---------- */
function setHtml(id, html) {
  const box = document.getElementById(id);
  if (box && box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
  return box;
}
function renderPipeline() {
  const p = portfolio?.pipeline;
  if (!p) return;
  const steps = [["due", "In renewal window", "amber"], ["called", "Mia reached", "cyan"], ["chosen", "Plan chosen", "cyan"], ["signed", "Signed", "emerald"], ["active", "Running", "emerald"]];
  const max = Math.max(1, p.due, ...steps.map(([k]) => p[k] || 0));
  setHtml("pipeline", steps.map(([k, label, tone], i) => {
    const v = p[k] || 0;
    const prev = i ? p[steps[i - 1][0]] || 0 : 0;
    const conv = i && prev ? `${Math.round((v / prev) * 100)}%` : "";
    return `<div class="pipe-row ${tone}"><span class="pipe-label">${label}</span><span class="pipe-bar"><i style="width:${Math.max(v ? 6 : 0, (v / max) * 100)}%"></i></span><strong>${v}</strong><span class="pipe-conv">${conv}</span></div>`;
  }).join(""));
}
function renderCallsCard() {
  const calls = portfolio?.mia_calls || [];
  const count = (test) => calls.filter(test).length;
  const answered = count((c) => ["answered", "done"].includes(c.status));
  const sms = count((c) => ["declined", "missed", "sms_sent"].includes(c.status));
  const waiting = count((c) => ["queued", "ringing"].includes(c.status));
  const total = Math.max(1, calls.length);
  const sub = document.getElementById("calls-sub");
  if (sub) sub.textContent = calls.length ? `${calls.length} total` : "none yet";
  const names = Object.fromEntries((portfolio.run?.results || []).map((r) => [r.unit_id, r.tenant]));
  const KIND = { renewal: "Renewal", callback: "Callback", manual: "Staff call" };
  const recent = calls.slice(-3).reverse().map((c) => `<li data-open="${esc(c.unit_id)}" tabindex="0"><span class="cl-dot ${esc(c.status)}"></span><span><strong>${flatId(c.unit_id)} · ${esc(names[c.unit_id] || "")}</strong><small>${esc(KIND[c.kind] || "Call")} · ${esc(CALL_STATE[c.status] || c.status)}</small></span></li>`).join("");
  setHtml("calls-card", calls.length ? `
    <div class="stack-bar" role="img" aria-label="${answered} answered, ${sms} sent to SMS, ${waiting} waiting">
      <i class="ok" style="width:${(answered / total) * 100}%"></i><i class="sms" style="width:${(sms / total) * 100}%"></i><i class="wait" style="width:${(waiting / total) * 100}%"></i></div>
    <div class="stack-legend"><span><b class="ok"></b>${answered} answered</span><span><b class="sms"></b>${sms} → SMS</span><span><b class="wait"></b>${waiting} waiting</span></div>
    <ul class="call-list">${recent}</ul>` : `<p class="muted small empty">No calls yet. Run today’s check or press ☎ Call on a flat.</p>`);
}
function renderSoon() {
  const rows = (portfolio?.run?.results || []).filter((r) => r.expiry && r.days_left != null && r.days_left >= 0 && r.tenant)
    .sort((a, b) => a.days_left - b.days_left).slice(0, 5);
  setHtml("soon", rows.map((r) => {
    const pct = Math.max(4, Math.min(100, 100 - (r.days_left / 60) * 100));
    const tone = r.days_left <= 14 ? "hot" : r.days_left <= 30 ? "warm" : "";
    return `<div class="soon-row ${tone}" data-open="${r.unit_id}" tabindex="0"><span><strong>${flatId(r.unit_id)}</strong> ${esc(r.tenant)}</span><span class="soon-bar"><i style="width:${pct}%"></i></span><span class="soon-days">${r.days_left} d</span></div>`;
  }).join("") || `<p class="muted small empty">No contracts ending soon.</p>`);
}

/* ---------- energy partner page ---------- */
const BOARD_COLS = [
  ["new", "New", ["submitted"]],
  ["progress", "In progress", ["received", "pending", "needs_input"]],
  ["confirmed", "Confirmed", ["confirmed"]],
  ["active", "Running", ["active"]],
  ["closed", "Declined / cancelled", ["rejected", "cancelled"]],
];
function renderPartnerBoard() {
  const orders = portfolio?.orders;
  if (!orders || !document.getElementById("order-board")) return;
  const latest = {};
  orders.forEach((o) => { latest[o.unit_id] = o; });   // one card per flat: its newest order
  const list = Object.values(latest);
  const eur = (v) => (v == null ? "—" : `€${Number(v).toFixed(0)}`);
  setHtml("order-board", BOARD_COLS.map(([key, label, statuses]) => {
    const cards = list.filter((o) => statuses.includes(o.status));
    return `<div class="board-col ${key}"><div class="board-head"><span>${label}</span><b>${cards.length}</b></div>
      ${cards.map((o) => `<a class="order-tile ${o.unit_id === UNIT ? "current" : ""}" href="/reltix?unit=${o.unit_id}#partner">
        <strong>${flatId(o.unit_id)} · ${esc(o.tenant)}</strong><span>${esc(o.product)} · ${eur(o.price)}/mo</span>
        <small>${esc(STATUS_TEXT[o.status] || o.status)}${o.start_date_mismatch ? " · ⚠ date" : ""}${o.requested_start ? ` · start ${shortDate(o.requested_start)}` : ""}</small></a>`).join("") || `<span class="board-empty">—</span>`}</div>`;
  }).join(""));
  const open = list.filter((o) => ["submitted", "received", "pending", "needs_input"].includes(o.status));
  const running = list.filter((o) => ["confirmed", "active"].includes(o.status));
  const volume = running.reduce((sum, o) => sum + (Number(o.price) || 0), 0);
  const saving = list.filter((o) => !["rejected", "cancelled"].includes(o.status)).reduce((sum, o) => sum + Math.max(0, (Number(o.previous_price) || 0) - (Number(o.price) || 0)), 0);
  const stat = (label, value, sub, tone = "") => `<div class="pstat ${tone}"><span>${label}</span><strong>${value}</strong><small>${sub}</small></div>`;
  setHtml("partner-stats", [
    stat("Need your answer", open.length, open.length ? "answer to keep the start date" : "you're up to date", open.length ? "amber" : ""),
    stat("Customers won", running.length, `${list.filter((o) => o.status === "active").length} already supplied`, "emerald"),
    stat("Monthly volume", `€${volume.toFixed(0)}`, "confirmed + running", "cyan"),
    stat("Residents save", `€${Math.round(saving * 12)}`, "per year vs. old contracts", "emerald"),
  ].join(""));
  const sub = document.getElementById("board-sub");
  if (sub) sub.textContent = `${list.length} ${list.length === 1 ? "flat" : "flats"} · click one to answer`;
  const nav = document.getElementById("nav-partner-count");
  if (nav) nav.textContent = open.length || "";
  renderPlanChart();
}
function renderPlanChart() {
  const plans = state.partner?.offers || [];
  if (!plans.length) return;
  const max = Math.max(...plans.map((o) => Number(o.estimated_monthly_price_eur) || 0), 1);
  setHtml("plan-chart", plans.map((o) => `<div class="plan-bar"><span>${esc(o.product)}</span><span class="pb-track"><i style="width:${((Number(o.estimated_monthly_price_eur) || 0) / max) * 100}%"></i><em style="width:${o.renewable_share_percent || 0}%"></em></span><strong>€${Number(o.estimated_monthly_price_eur || 0).toFixed(0)}</strong><small>${o.renewable_share_percent || 0}% green</small></div>`).join(""));
}

function renderKpis() {
  const run = portfolio?.run;
  const kpis = document.getElementById("kpis");
  if (!kpis || !run) return;
  const k = portfolio.kpis || {};
  const calls = portfolio.mia_calls || [];
  const tile = (key, icon, value, label, sub, tone = "") => `<button type="button" class="kpi tilt ${tone}" data-kpi="${key}"><span class="kpi-icon">${icon}</span><span class="kpi-label">${label}</span><strong>${value}</strong><span class="kpi-sub">${sub}</span></button>`;
  const html = [
    tile("all", "◎", run.scanned, "Flats checked", `${run.matched} renewals due today`),
    tile("matched", "☎", calls.length, "Calls by Mia", `${calls.filter((c) => ["answered", "done"].includes(c.status)).length} answered · ${k.support_calls || 0} other calls`, "cyan"),
    tile("partner", "✓", k.signed || 0, "Contracts signed", k.monthly_saving_eur ? `€${Math.round(k.monthly_saving_eur * 12)} saved per year` : `${k.active || 0} running`, "emerald"),
    tile("inbox", "!", (portfolio.review_queue || []).filter((i) => !["provenance", "data"].includes(i.category)).length, "Need a person",
      `${(portfolio.review_queue || []).filter((i) => ["provenance", "data"].includes(i.category)).length} data checks · ${k.issues_open || 0} open complaints`, "amber"),
  ].join("");
  if (kpis.dataset.html !== html) { kpis.dataset.html = html; kpis.innerHTML = html; }
}

// "Needs you": Ataberk's review queue (auditable: every item closes with a short note) plus the daily check's flags.
// Urgent first; the per-flat "check record sources" items fold into one Data checks row so the list stays short.
const REVIEW_ICON = { issue: "🔧", flag: "⚠", coverage: "📍", case: "🙋", order: "📄", data: "✎", provenance: "🗂" };
const REVIEW_RANK = { issue: 0, case: 1, order: 2, coverage: 3, flag: 4, data: 5, provenance: 6 };
let dataChecksOpen = false;
function reviewCard(item, names) {
  return `<div class="inbox-item ${item.severity === "urgent" ? "urgent" : ""} ${esc(item.category)}">
    <span class="ib-icon" aria-hidden="true">${REVIEW_ICON[item.category] || "•"}</span>
    <div class="ib-body"><strong>${flatId(item.unit_id)} · ${esc(names[item.unit_id] || "Vacant")}</strong>
      <span class="ib-kind">${item.severity === "urgent" ? "Urgent · " : ""}${esc(item.title)}</span><p>${esc(item.reason)}</p></div>
    <div class="ib-actions">${item.issue_id
      // complaints close when someone replies to the resident (Ataberk's rule), not with a review note
      ? `<a class="mini-btn primary" href="/reltix?unit=${item.unit_id}#reply-${esc(item.issue_id)}">Reply to resident</a>`
      : `<a class="mini-btn" href="/reltix?unit=${item.unit_id}#case">Open case</a>
      <button type="button" class="mini-btn primary" data-review-open="${esc(item.review_id)}">✓ Done</button>`}
    </div>
    ${item.issue_id ? "" : `<form class="review-form ib-note" data-review="${esc(item.review_id)}" data-unit="${esc(item.unit_id)}" hidden>
      <input name="note" maxlength="300" minlength="5" required value="Checked and handled" aria-label="What did you check or do?">
      <button class="mini-btn primary" type="submit">Save</button><span role="status" class="form-message"></span>
    </form>`}</div>`;
}
function renderInbox() {
  const box = document.getElementById("inbox-list");
  if (!box || !portfolio) return;
  if (box.querySelector(".ib-note:not([hidden])")) return; // don't redraw while someone is typing a note
  const names = Object.fromEntries((portfolio.run?.results || []).map((r) => [r.unit_id, r.tenant]));
  const all = (portfolio.review_queue || []).slice().sort((x, y) => (x.severity === "urgent" ? -1 : 0) - (y.severity === "urgent" ? -1 : 0)
    || (REVIEW_RANK[x.category] ?? 9) - (REVIEW_RANK[y.category] ?? 9));
  const DATA = ["provenance", "data"];   // record checks: important, but not today's work
  const main = all.filter((i) => !DATA.includes(i.category));
  const sources = all.filter((i) => DATA.includes(i.category));
  const html = (main.length ? main.map((i) => reviewCard(i, names)).join("")
      : `<div class="inbox-empty"><strong>All clear.</strong><span>Nothing needs a person right now. Mia and centrix are handling the rest.</span></div>`)
    + (sources.length ? `<details class="data-checks" ${dataChecksOpen ? "open" : ""}><summary><span>🗂 Data checks</span><span class="muted small">${sources.length} record ${sources.length === 1 ? "check" : "checks"} across ${new Set(sources.map((i) => i.unit_id)).size} flats</span></summary>${sources.map((i) => reviewCard(i, names)).join("")}</details>` : "");
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
  byId("inbox-sub").textContent = main.length ? `${main.length} ${main.length === 1 ? "item" : "items"}${main.some((i) => i.severity === "urgent") ? " · urgent first" : ""}` : "";
  const count = document.getElementById("nav-inbox-count");
  if (count) count.textContent = main.length || "";
}
document.addEventListener("toggle", (event) => { if (event.target.classList?.contains("data-checks")) dataChecksOpen = event.target.open; }, true);
document.addEventListener("click", (event) => {
  const open = event.target.closest("[data-review-open]");
  if (!open) return;
  const form = open.closest(".inbox-item").querySelector(".ib-note");
  form.hidden = !form.hidden;
  if (!form.hidden) { const input = form.querySelector("input"); input.focus(); input.select(); }
});

// Live activity: what residents, Mia and the supplier did, across all flats. New entries also pop up as toasts.
let feedSeen = null;
function renderFeed() {
  const box = document.getElementById("feed");
  const feed = portfolio?.feed || [];
  const key = (e) => `${e.at}|${e.unit_id}|${e.text}`;
  if (feedSeen) feed.filter((e) => !feedSeen.has(key(e))).slice(-3).forEach((e) => toast(`${flatId(e.unit_id || "")} · ${e.tenant || ""}`, e.text, e.unit_id));
  feedSeen = new Set(feed.map(key));
  if (!box) return;
  const html = feed.slice(-12).reverse().map((e) => `<li data-open="${esc(e.unit_id || "")}" tabindex="0"><span class="fd-time">${new Date(e.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</span><span class="fd-body"><strong>${flatId(e.unit_id || "")} · ${esc(EVENT_TITLES[e.type] || e.type)}</strong><span>${esc(e.text)}</span></span></li>`).join("")
    || `<li class="muted small">Nothing yet. Activity shows up here as it happens.</li>`;
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
}
function toast(title, text, unit) {
  let stack = document.getElementById("toasts");
  if (!stack) { stack = document.createElement("div"); stack.id = "toasts"; stack.setAttribute("aria-live", "polite"); document.body.appendChild(stack); }
  const t = document.createElement("button");
  t.type = "button"; t.className = "toast";
  t.innerHTML = `<strong>${esc(title)}</strong><span>${esc(text)}</span>`;
  t.addEventListener("click", () => { if (unit) location.href = `/reltix?unit=${unit}#case`; });
  stack.appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 400); }, 6000);
}

function renderDatebar() {
  const run = portfolio?.run;
  const slot = document.getElementById("clock-slot");
  if (slot && !slot.querySelector("#demo-date")) slot.innerHTML = `<label class="clock"><span>Demo date</span><input type="date" id="demo-date" value="${esc(portfolio.today)}"></label>`;
  else if (slot && document.activeElement?.id !== "demo-date") slot.querySelector("#demo-date").value = portfolio.today;
  const next = document.getElementById("next-event");
  if (next) {
    next.disabled = !portfolio.next_event;
    next.textContent = portfolio.next_event ? `Next event: ${shortDate(portfolio.next_event)} ▸` : "No more events";
  }
  const note = document.getElementById("datebar-note");
  if (note && run) note.textContent = `${run.matched} renewals · ${run.attention} flagged · ${run.scanned - run.matched - run.attention} skipped`;
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
    checkIncoming();
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
  if (document.getElementById("plan-chart")) renderPlanChart();
  showCaseIfAsked();
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
      form.hidden = true;
      await loadPortfolio(); await refreshState();
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

/* ---------- staff dashboard: phone link + QR code (link comes from launch.py / cloudflared) ---------- */
let tunnelUrl = null;
async function renderPhonePop() {
  const pop = document.getElementById("phone-pop");
  if (!pop || pop.hidden) return;
  try { tunnelUrl = (await (await fetch("/api/tunnel", { cache: "no-store" })).json()).url; } catch { tunnelUrl = null; }
  const select = byId("phone-flat");
  const residents = (portfolio?.run?.results || []).filter((r) => r.tenant);
  if (select.options.length !== residents.length && residents.length) {
    const keep = select.value;
    const first = residents.find((r) => r.decision === "matched") || residents[0];
    select.innerHTML = residents.map((r) => `<option value="${r.unit_id}" ${r.unit_id === (keep || PINNED_UNIT || first.unit_id) ? "selected" : ""}>${flatId(r.unit_id)} · ${esc(r.tenant)}${r.decision === "matched" ? " · Mia calling" : ""}</option>`).join("");
  }
  const qr = byId("phone-qr");
  const link = tunnelUrl ? `${tunnelUrl}/tenant?unit=${select.value || "unit_1a"}` : "";
  byId("phone-url").value = link || "No phone link";
  byId("phone-copy").disabled = !link;
  if (!link) {
    // A localhost link can't work on a phone (it would point at the phone itself), so show no QR at all.
    qr.dataset.link = "";
    qr.innerHTML = `<div class="qr-missing"><strong>No phone link running</strong><span>Close the app window and start it with <b>Start Reltix.bat</b>. It opens the phone link, then the QR code appears here.</span></div>`;
    byId("phone-note").textContent = "";
    return;
  }
  if (qr.dataset.link !== link) {
    qr.dataset.link = link;
    if (window.qrcode) { const code = qrcode(0, "M"); code.addData(link); code.make(); qr.innerHTML = code.createSvgTag({ cellSize: 5, margin: 2, scalable: true }); }
    else qr.textContent = "QR code unavailable";
  }
  byId("phone-note").textContent = "Scan with the phone camera, then tap the page once so it can ring.";
}
document.addEventListener("click", async (event) => {
  const btn = event.target.closest("#phone-btn");
  const pop = document.getElementById("phone-pop");
  if (!pop) return;
  if (btn) { pop.hidden = !pop.hidden; btn.setAttribute("aria-expanded", String(!pop.hidden)); renderPhonePop(); return; }
  if (event.target.closest("#phone-copy")) {
    const input = byId("phone-url");
    try { await navigator.clipboard.writeText(input.value); } catch { input.select(); document.execCommand("copy"); }
    event.target.textContent = "Copied ✓"; setTimeout(() => { event.target.textContent = "Copy"; }, 1500);
    return;
  }
  if (!event.target.closest(".phone-menu")) pop.hidden = true;
});
document.addEventListener("change", (event) => { if (event.target.id === "phone-flat") renderPhonePop(); });
async function openPhoneFor(unitId) {
  const pop = document.getElementById("phone-pop");
  if (!pop) return;
  pop.hidden = false;
  byId("phone-btn").setAttribute("aria-expanded", "true");
  await renderPhonePop();
  const select = byId("phone-flat");
  if ([...select.options].some((o) => o.value === unitId)) { select.value = unitId; await renderPhonePop(); }
  window.scrollTo({ top: 0, behavior: "smooth" });
}
document.addEventListener("click", (event) => {
  const qrBtn = event.target.closest("[data-qr]");
  if (!qrBtn) return;
  event.stopPropagation();
  qrBtn.closest("details")?.removeAttribute("open");
  openPhoneFor(qrBtn.dataset.qr);
}, true);

/* ---------- staff app: one screen per view (sidebar switches views, no long page scroll) ---------- */
const VIEWS = {
  overview: ["Overview", "What needs you today, and what Mia and centrix already handled."],
  flats: ["Flats", "Every flat centrix watches. Search, filter, call or open a case."],
  case: ["Case", "One flat: what the resident said, every step, contracts and the record."],
  partner: ["Energy partner", "Orders from residents who picked a plan. Answer them and keep your prices current."],
};
function viewFromHash() {
  const h = location.hash.slice(1);
  if (h.startsWith("reply-")) return ["case", "complaints"];
  if (h === "inbox") return ["overview"];
  return [VIEWS[h] ? h : "overview"];
}
function showView(name, tab) {
  if (!document.body.classList.contains("app")) return;
  document.body.dataset.view = name;
  document.querySelectorAll(".view-pane").forEach((p) => { p.hidden = p.dataset.pane !== name; });
  document.querySelectorAll("[data-view-link]").forEach((a) => a.classList.toggle("active", a.dataset.viewLink === name && a.closest(".side-nav")));
  const [title, sub] = VIEWS[name];
  byId("view-title").textContent = name === "case" && state.unit ? `Flat ${flatId(UNIT)}${state.unit.tenant?.name ? ` · ${state.unit.tenant.name}` : ""}` : title;
  byId("view-sub").textContent = sub;
  if (tab) selectTab(tab);
  window.scrollTo(0, 0);
}
function selectTab(tab) {
  document.querySelectorAll("#case-tabs [data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  document.querySelectorAll("[data-tabpane]").forEach((p) => { p.hidden = p.dataset.tabpane !== tab; });
}
function showCaseIfAsked() {
  // keeps the case header in sync once the flat has loaded
  if (document.body.dataset.view === "case") byId("view-title").textContent = `Flat ${flatId(UNIT)}${state.unit?.tenant?.name ? ` · ${state.unit.tenant.name}` : ""}`;
}
window.addEventListener("hashchange", () => { const [v, t] = viewFromHash(); showView(v, t); if (t === "complaints") openLinkedReply(); });
document.addEventListener("click", (event) => {
  const tab = event.target.closest("#case-tabs [data-tab]");
  if (tab) return selectTab(tab.dataset.tab);
  if (event.target.closest("#tl-toggle")) { timelineAll = !timelineAll; renderManager(); }
});
if (document.body.classList.contains("app")) { const [v, t] = viewFromHash(); showView(v, t); }

/* ---------- staff dashboard: buttons that do things ---------- */
document.addEventListener("click", async (event) => {
  if (!document.body.classList.contains("dash")) return;
  const el = event.target;
  const chip = el.closest("[data-filter]");
  if (chip) { flats.filter = chip.dataset.filter; flats.limit = 10; return renderPortfolio(); }
  if (el.closest("[data-more]")) { flats.limit += 10; return renderPortfolio(); }
  const callNow = el.closest("[data-callnow]");
  if (callNow) {
    callNow.disabled = true;
    try { await post("/api/calls", { unit_id: callNow.dataset.callnow }); toast(`${flatId(callNow.dataset.callnow)} · Mia is calling`, "It rings on the resident's page now."); }
    catch (error) { toast("Couldn't start the call", error.message); }
    await loadPortfolio();
    return;
  }
  const handled = el.closest("[data-handled]");
  if (handled) {
    handled.disabled = true;
    try { await post("/api/handled", { kind: handled.dataset.handled, unit_id: handled.dataset.unit, issue_id: handled.dataset.issue || undefined }); }
    catch (error) { toast("Couldn't update", error.message); }
    await loadPortfolio(); await refreshState();
    return;
  }
  const adv = el.closest("[data-advance]");
  if (adv) {
    adv.disabled = true;
    try { await post("/api/clock", { advance: adv.dataset.advance }); } catch (error) { toast("Date not changed", error.message); }
    adv.disabled = false;
    await loadPortfolio(); await refreshState();
    return;
  }
  const kpi = el.closest("[data-kpi]");
  if (kpi) {
    const k = kpi.dataset.kpi;
    if (k === "partner") { location.hash = "partner"; return; }
    if (k === "inbox") { document.getElementById("inbox")?.classList.add("flash"); setTimeout(() => document.getElementById("inbox")?.classList.remove("flash"), 1200); return; }
    flats.filter = k; flats.limit = 10; renderPortfolio();
    location.hash = "flats"; return;
  }
  const node = el.closest(".net-node");
  if (node && !node.classList.contains("core")) {
    if (node.classList.contains("resident")) return window.open(`/tenant?unit=${UNIT}`, "_blank", "noopener");
    if (node.classList.contains("supplier")) { location.hash = "partner"; return; }
    return selectTab("contracts");
  }
  const open = el.closest("[data-open]");
  if (open && open.dataset.open && !el.closest("a,button,details,summary")) location.href = `/reltix?unit=${open.dataset.open}#case`;
});
document.addEventListener("keydown", (event) => {
  if (!document.body.classList.contains("dash")) return;
  if (event.key === "/" && !/input|textarea/i.test(document.activeElement?.tagName || "")) { event.preventDefault(); byId("flat-search").focus(); }
  const row = event.target.closest?.("[data-open]");
  if (row && event.key === "Enter" && row.dataset.open && event.target === row) location.href = `/reltix?unit=${row.dataset.open}#case`;
});
document.addEventListener("input", (event) => {
  if (event.target.id === "flat-search") { flats.query = event.target.value; flats.limit = 10; renderPortfolio(); }
});
if (document.body.classList.contains("dash")) {
  // Live pill: "Live" follows the latest activity; on a pinned flat it offers the way back.
  const pill = document.getElementById("live-pill");
  if (pill && PINNED_UNIT) {
    pill.classList.add("pinned");
    pill.innerHTML = `Flat ${esc(flatId(PINNED_UNIT))} · back to live`;
    pill.addEventListener("click", () => { location.href = "/reltix"; });
  }
}

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
  if (t.dataset.call === "problem" && idleish() && !call.incoming) { unlockAudio(); startCall("problem"); }
  if (t.dataset.call === "start" && idleish() && !call.incoming) startCall("renewal");
  if (t.dataset.call === "answer") answerIncoming();
  if (t.dataset.call === "decline") declineIncoming();
  if (t.dataset.call === "simulate") simulateCall();
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
    ring(false);
    Object.assign(call, { status: "idle", transcript: [], issueId: null, error: "", muted: false, incoming: null, simulated: false, linkSent: null, reason: "renewal" });
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
  if (document.activeElement?.id !== "demo-date") loadPortfolio();
}, 2500);
loadPortfolio();

window.flow = { render, renderPartner, chooseOffer, syncOfferVersion };
render();
})();
