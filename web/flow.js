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
  incoming: null,        // queued centrix call currently ringing on this page
  status: "idle",        // idle | connecting | connected | ended
  mode: "listening",     // speaking | listening
  transcript: [],        // { who: "Mia" | "You", text }
  simulated: false,
};
const resident = { declined: false, consent: false, busy: false, error: "" };

/* ---------- small helpers ---------- */

const post = async (path, body) => {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
};
const logEvent = (type, text, channel = "voice") => post("/api/events", { type, text, channel, unit_id: UNIT }).catch(() => {});
// The outbound call centrix queued for this flat after its daily trigger run (null if none).
// A callback the resident asked for only rings once its time (due_at) has come.
const isDue = (c) => !c.due_at || Date.parse(c.due_at) <= Date.now();
const handledCalls = new Set(); // answered/declined/simulated here, even if the server hasn't caught up yet
const queuedCall = () => (state.calls || []).slice().reverse().find((c) => ["queued", "ringing"].includes(c.status) && isDue(c) && !handledCalls.has(c.call_id)) || null;
const idleish = () => call.status === "idle" || call.status === "ended";
const setCallStatus = (c, status) => c && post(`/api/calls/${c.call_id}`, { status }).catch(() => {});
// Everything the resident tells Mia or the portal is stored once on the flat (main.py update_case), so all pages show it.
const saveCase = (patch, source = "voice") => post("/api/case", { unit_id: UNIT, source, ...patch }).then(() => refreshState()).catch(() => {});
const serverCase = () => state.case || {};
const offers = () => state.partner?.offers || [];
const latestOrder = () => (state.orders || []).slice(-1)[0] || null;
const openOrder = () => (state.orders || []).find((o) => ["submitted", "received", "needs_input"].includes(o.status)) || null;
const firstName = () => (state.unit?.tenant?.name || "there").split(" ")[0];
const currentContract = () => (state.unit?.contracts || []).find((c) => c.type === "energy_supply" && c.status !== "signed") || energyContract();
const bestOffer = () => offers().reduce((a, b) => (Number(b.estimated_monthly_price_eur) < Number(a.estimated_monthly_price_eur) ? b : a), offers()[0]);
const pickedOffer = () => offers()[chosenOffer] || null;
const spokenDate = (iso) => (iso ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" }).format(new Date(`${iso}T12:00:00`)) : "soon");
const SHARED_FIELDS = ["your name", "the address", "the meter number", "the meter reading at the switch", "the start date"];

function selectOffer(offerId) {
  const index = offers().findIndex((o) => o.offer_id === offerId);
  if (index < 0) return null;
  chosenOffer = index;
  renderOffers("tenant-offers", true);
  return offers()[index];
}

/* ---------- 1. voice call (resident channel) ---------- */

const clientTools = {
  get_offers: () => {
    const now = Number(currentContract().current_monthly_price_eur || 0);
    const cheapest = bestOffer();
    return JSON.stringify({
      current_monthly_price_eur: now,
      offers: offers().map((o) => ({
        offer_id: o.offer_id,
        name: o.product,
        est_monthly_eur: o.estimated_monthly_price_eur,
        saving_per_month_eur: Math.round((now - o.estimated_monthly_price_eur) * 100) / 100,
        contract_months: o.contract_months,
        renewable_percent: o.renewable_share_percent,
        cheapest: o === cheapest,
      })),
    });
  },
  record_choice: ({ offer_id }) => {
    const offer = selectOffer(String(offer_id));
    if (!offer) return "Unknown offer_id. Call get_offers and use one of those ids.";
    saveCase({ chosen_offer_id: offer.offer_id });
    render();
    return `Saved: ${offer.product}.`;
  },
  send_portal_link: ({ channel }) => {
    const via = channel === "email" ? "email" : "SMS";
    call.linkSent = via;
    saveCase({ preferred_channel: via, note: `Confirm link sent by ${via}.` });
    render();
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
    catch (error) { return `Couldn't save it: ${error.message}`; }
    return `Saved meter reading ${value} kWh.`;
  },
  save_note: ({ note }) => {
    if (!note) return "Nothing to save.";
    saveCase({ note: String(note) });
    return "Noted in the resident's file.";
  },
  handoff_to_human: ({ reason }) => {
    saveCase({ needs_person: reason || "resident asked for a person" });
    return "A property manager will follow up.";
  },
};

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
  await startCall();
}
function declineIncoming() {
  ring(false);
  if (call.incoming) handledCalls.add(call.incoming.call_id);
  setCallStatus(call.incoming, "declined");
  Object.assign(call, { incoming: null, status: "ended", linkSent: "SMS", transcript: [] });
  render();
}

async function startCall() {
  if (!window.ElevenLabsClient) {
    call.error = "Voice library didn't load. Use “Simulate the call” instead.";
    return render();
  }
  const contract = currentContract();
  const meter = electricityMeter();
  Object.assign(call, { status: "connecting", transcript: [], error: "", simulated: false, linkSent: null });
  render();
  logEvent("call", `Mia (voice agent) called ${state.unit.tenant.name}: contract with ${contract.supplier} ends ${prettyDate(contract.expiry_date)}.`);
  try {
    call.conversation = await ElevenLabsClient.Conversation.startSession({
      agentId: AGENT_ID,
      dynamicVariables: state.agent_variables, // built by centrix (main.py agent_variables), same as for phone calls
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
    Object.assign(call, { status: "idle", incoming: null, error: `Couldn't start the call: ${error.message || error}` });
    render();
  }
}

async function endCall() {
  await call.conversation?.endSession();
}

function simulateCall() {
  const answered = call.incoming;
  if (answered) handledCalls.add(answered.call_id);
  call.incoming = null;
  ring(false);
  if (answered) setCallStatus(answered, "answered").then(() => setCallStatus(answered, "done"));
  const offer = bestOffer();
  const contract = currentContract();
  Object.assign(call, { status: "ended", simulated: true, error: "", linkSent: "SMS", transcript: [
    { who: "Mia", text: `Hi ${firstName()}, this is Mia from reltix. Your electricity contract with ${contract.supplier} ends on ${spokenDate(contract.expiry_date)}. Do you have two minutes?` },
    { who: "You", text: "Sure." },
    { who: "Mia", text: `${offer.product} from ${state.partner.partner} is about ${Math.round(offer.estimated_monthly_price_eur)} euros a month, roughly ${Math.round(contract.current_monthly_price_eur - offer.estimated_monthly_price_eur)} less than now. Shall I send you the details?` },
    { who: "You", text: "Yes, by SMS please." },
  ] });
  selectOffer(offer.offer_id);
  logEvent("call", `(Simulated) Mia called ${state.unit.tenant.name}; chose ${offer.product}; link sent by SMS.`);
  render();
}

function renderCall() {
  const card = document.getElementById("call-card");
  if (!card || !state.unit) return;
  const last = call.transcript.slice(-4);
  const lines = last.map((l) => `<div class="call-line${l.who === "You" ? " you" : ""}"><b>${esc(l.who)}</b> ${esc(l.text)}</div>`).join("");
  let head, actions;
  if (call.status === "connecting") {
    head = `<strong>Calling ${esc(firstName())}…</strong><span class="muted small">Allow the microphone. Headphones work best.</span>`;
    actions = "";
  } else if (call.status === "connected") {
    head = `<strong><span class="live-dot"></span> On a call with Mia</strong><span class="muted small">${call.muted ? "Your mic is muted" : call.mode === "speaking" ? "Mia is speaking…" : "Listening…"}</span>`;
    actions = `<button class="text-button" type="button" data-call="mute">${call.muted ? "Unmute" : "Mute"}</button><button class="button" type="button" data-call="end">Hang up</button>`;
  } else if (call.incoming && idleish()) {
    const c = call.incoming;
    head = `<strong><span class="live-dot"></span> Incoming call · reltix${c.urgent ? " · urgent" : ""}</strong><span class="muted small">centrix's daily check found your contract ending soon and asked Mia to call you.${audioReady() ? "" : " <b>Tap anywhere to hear it ring.</b>"}</span>`;
    actions = `<button class="button button-primary" type="button" data-call="answer">Answer</button><button class="text-button" type="button" data-call="decline">Decline</button><button class="text-button" type="button" data-call="simulate">No mic? Simulate</button>`;
  } else if (call.status === "ended") {
    head = `<strong>${call.simulated ? "Call (simulated)" : "Call with Mia"} · ended</strong><span class="muted small">${call.linkSent ? `Link sent by ${esc(call.linkSent)} · this page picks up where the call left off.` : "No link was sent."}</span>`;
    actions = `<button class="text-button" type="button" data-call="start">Call again</button>`;
  } else {
    head = `<strong>Mia, your reltix assistant</strong><span class="muted small">Calls you about your contract, answers questions, then sends this page to confirm.</span>`;
    actions = `<button class="button button-primary" type="button" data-call="start">Start the call</button><button class="text-button" type="button" data-call="simulate">No mic? Simulate it</button>`;
  }
  card.classList.toggle("ringing", !!call.incoming && idleish());
  card.innerHTML = `
    <div class="call-head"><span class="call-icon"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/></svg></span><div>${head}</div><div class="call-actions">${actions}</div></div>
    ${lines ? `<div class="call-script">${lines}</div>` : ""}
    ${call.error ? `<p class="form-message error">${esc(call.error)}</p>` : ""}`;
  renderPhone();
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
  let sub, buttons;
  if (ringingNow) {
    sub = `${call.incoming.kind === "callback" ? "Calling you back" : "Incoming call"}${call.incoming.urgent ? " · urgent" : ""}${audioReady() ? "" : `<br><span class="phone-tap-hint">Tap anywhere to hear it ring</span>`}`;
    buttons = `
      <div class="phone-btn-wrap"><button class="phone-btn decline" type="button" data-call="decline" aria-label="Decline">${ICON_PHONE}</button><span>Decline</span></div>
      <div class="phone-btn-wrap"><button class="phone-btn answer" type="button" data-call="answer" aria-label="Answer">${ICON_PHONE}</button><span>Answer</span></div>`;
  } else {
    sub = call.status === "connecting" ? "Connecting…" : `<span id="phone-timer"></span> · ${call.muted ? "muted" : call.mode === "speaking" ? "Mia is speaking" : "listening"}`;
    buttons = `
      <div class="phone-btn-wrap"><button class="phone-btn mute${call.muted ? " on" : ""}" type="button" data-call="mute" aria-label="${call.muted ? "Unmute" : "Mute"}" ${call.status !== "connected" ? "disabled" : ""}>${ICON_MIC(call.muted)}</button><span>${call.muted ? "Unmute" : "Mute"}</span></div>
      <div class="phone-btn-wrap"><button class="phone-btn decline" type="button" data-call="end" aria-label="Hang up">${ICON_PHONE}</button><span>End</span></div>`;
  }
  const last = call.transcript.slice(-2).map((l) => `<p class="${l.who === "You" ? "you" : ""}"><b>${esc(l.who)}:</b> ${esc(l.text)}</p>`).join("");
  const cls = ringingNow ? "ringing" : call.mode === "speaking" ? "speaking" : "";
  if (el.className !== cls) el.className = cls;
  // Each part updates on its own, so a tap on a button is never lost when the text above it changes.
  if (!el.firstChild) el.innerHTML = `<div class="phone-top"></div><div class="phone-lines"></div><div class="phone-bottom"></div>`;
  const set = (sel, html) => { const part = el.querySelector(sel); if (part.dataset.html !== html) { part.dataset.html = html; part.innerHTML = html; } };
  set(".phone-top", `
      <div class="phone-avatar">M</div>
      <h2>Mia · reltix</h2>
      <p class="phone-sub">${sub}</p>
      <p class="phone-about">${ringingNow ? `About your electricity contract${who ? ` · ${esc(who)}` : ""}` : "Speak normally, like on a phone call"}</p>`);
  set(".phone-lines", `${live ? last : ""}${call.error ? `<p class="err">${esc(call.error)}</p>` : ""}`);
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
  ["confirmed", "Contract confirmed"],
  ["active", "New supply running"],
];

function renderDecision() {
  const box = document.getElementById("decision");
  if (!box || !state.unit) return;
  const order = latestOrder();
  const contract = currentContract();

  if (order && order.status !== "rejected") {
    const reached = new Set(order.status_history.map((s) => s.status));
    const result = order.status_history.find((s) => s.result)?.result;
    const steps = RESIDENT_STEPS.map(([key, label]) => `<li class="${reached.has(key) ? "done" : ""}">${reached.has(key) ? "✓" : "○"} ${esc(label)}</li>`).join("");
    const needs = order.status === "needs_input"
      ? `<p class="notice notice-amber">${esc(state.partner.partner)} needs your meter reading to finish. Add it under <b>Meter reading</b> below. It’s sent on automatically.</p>` : "";
    const done = result
      ? `<p class="notice">Your new contract ${esc(result.contract_number)} starts on ${esc(prettyDate(result.start))}. ${esc(order.partner)} cancels ${esc(order.previous_contract.supplier)} for you. Nothing else to do.</p>` : "";
    box.innerHTML = `<div class="decision-box"><div class="section-label"><span>${esc(order.product)} · your switch</span><span class="count-label">${esc(order.order_id)}</span></div><ul class="step-list">${steps}</ul>${needs}${done}</div>`;
    if (order.status === "needs_input") byId("reading-fold").open = true;
    return;
  }

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
      ${call.linkSent && offer ? `<p class="from-call">Continuing from your call: you chose <b>${esc(offer.product)}</b>.</p>` : ""}
      ${offer ? `<p class="saving">${saving > 0 ? `You save about <b>${money(saving)}</b> a month (${money(saving * 12)} a year) compared with today.` : `This plan costs ${money(-saving)} more per month than today.`}</p>` : `<p class="muted small">Pick a plan above to continue.</p>`}
      <label class="consent">
        <input type="checkbox" id="consent-box" ${resident.consent ? "checked" : ""} ${offer ? "" : "disabled"}>
        <span>I agree that reltix shares <b>only</b> ${SHARED_FIELDS.join(", ")} with ${esc(state.partner.partner)} to set up this contract. ${esc(state.partner.partner)} cancels my current contract. I can withdraw within 14 days.</span>
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
    await post("/api/orders", { unit_id: UNIT, offer_id: offer.offer_id, consent_given: true, channel: call.linkSent ? "portal (after call)" : "portal", signed_name: state.unit.tenant.name });
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
  received: () => [`Thanks. Is the contract ready to send, or do you need anything else?`],
  needs_input: () => [`I’ve asked the customer for the meter reading. I’ll call you back as soon as I have it.`],
  confirmed: (o) => [`Great, I’ve told the customer it starts on ${prettyDate(o.requested_start)}.`, `I’ll check in on the start day to confirm the power is running.`],
  active: () => [`All done. The contract is filed with the property. Thank you!`],
  rejected: () => [`Understood. I’ll offer the customer another plan. Thank you.`],
};
const PARTNER_ANSWERS = {
  submitted: [["received", "Yes, I’ll take it", "button-primary"], ["rejected", "No, decline", ""]],
  received: [["confirmed", "Contract is ready", "button-primary"], ["needs_input", "I need the meter reading", ""]],
  confirmed: [["active", "Start day: power is running", "button-primary"]],
};
const STATUS_TEXT = {
  submitted: "Customer chose your plan", received: "You accepted the order", needs_input: "You asked for the meter reading",
  confirmed: "You confirmed the contract", active: "Supply started", rejected: "You declined the order", cancelled: "Order cancelled",
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
  byId("company-title").textContent = order.status === "active" ? "Order complete" : order.status === "rejected" ? "Order declined" : "New order";
  byId("partner-lead").innerHTML = `Hi <strong id="partner-name">${esc(order.partner)}</strong>. You only get what your plan needs. Contact goes through centrix.`;
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

/* ---------- 4. property manager: what happened, consent, contracts ---------- */

const EVENT_TITLES = {
  detected: "Renewal window detected", call_queued: "Call queued for Mia",
  call: "Call", choice: "Plan chosen on the call", case: "Resident file updated", reading: "Meter reading", link: "Link sent", declined: "Resident declined",
  callback: "Callback requested", handoff: "Needs a person", consent: "Consent given and signed", order: "Order sent to partner",
  received: "Partner accepted", needs_input: "Partner needs input", confirmed: "Contract confirmed", rejected: "Partner declined",
  active: "New supply running", filed: "Filed on the unit", cancelled: "Order cancelled",
};

function renderManager() {
  if (!state.unit || !document.getElementById("case-timeline")) return;
  const events = state.events || [];
  const contract = energyContract();
  const remaining = daysUntil(contract.expiry_date);
  const detected = { type: "detected", at: null, channel: "centrix", text: `Contract with ${contract.supplier} ends ${prettyDate(contract.expiry_date)} (${remaining} days). Case opened ${contract.renewal_notice_days || 30} days before expiry.` };
  const all = events.some((e) => e.type === "detected") ? events : [detected, ...events];
  byId("landlord-title").textContent = `Flat ${UNIT.replace("unit_", "").toUpperCase()}`;
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

const DECISION = { matched: ["badge-green", "Call"], attention: ["badge-amber", "Needs a person"], skipped: ["", "Skipped"] };
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
    const links = `<a href="/reltix?unit=${r.unit_id}">case</a>${r.tenant ? ` · <a href="/tenant?unit=${r.unit_id}" target="_blank" rel="noopener">resident page</a>` : ""}${c && portfolio.phone_ready ? ` · <button class="text-button" type="button" data-phone="${c.call_id}">call phone</button>` : ""}`;
    return `<tr class="${r.unit_id === UNIT ? "current" : ""}">
      <td><strong>${esc(r.unit_id.replace("unit_", "").toUpperCase())}</strong><span class="muted small">${esc(r.address.split(",")[0])}</span></td>
      <td>${esc(r.tenant || "—")}<span class="muted small">${r.expiry ? `ends ${esc(prettyDate(r.expiry))} · ${r.days_left} d` : "no contract"}</span></td>
      <td><span class="badge ${cls}">${label}</span> ${callCell}<span class="muted small">${esc(r.reason)}</span></td>
      <td class="small">${links}</td></tr>`;
  }).join("");
  box.innerHTML = `
    <div class="run-head">
      <div><h1>Today’s run</h1><p class="muted small">Every morning centrix checks each flat against the trigger: active contract, tenant opt-in, inside the renewal window, a partner serves the postcode. Matches are called automatically.</p></div>
      <label class="clock"><span>Demo date</span><input type="date" id="demo-date" value="${esc(portfolio.today)}"></label>
    </div>
    ${run ? `<div class="run-stats">
      <div><strong>${run.scanned}</strong><span>flats checked</span></div>
      <div><strong>${calls.length}</strong><span>calls placed</span></div>
      <div><strong>${run.attention}</strong><span>need a person</span></div>
      <div><strong>${run.scanned - run.matched - run.attention}</strong><span>skipped today</span></div>
    </div>
    <p class="muted small">Last run ${esc(prettyDate(run.date))} · ${new Date(run.ran_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })} · ${esc(run.source)} · <button class="text-button" type="button" data-run="now">Run again</button></p>
    <div class="run-table-wrap"><table class="run-table"><thead><tr><th>Flat</th><th>Resident</th><th>Decision</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="muted">No run yet.</p>`}
    ${run?.auto_dial ? `<p class="notice ${run.auto_dial.ok ? "" : "notice-amber"}">📞 ${run.auto_dial.ok ? `Mia is phoning the demo number for flat ${esc(run.auto_dial.unit_id.replace("unit_", "").toUpperCase())}.` : esc(run.auto_dial.message)}</p>` : ""}
    <p class="phone-hint small muted">${portfolio.auto_dial_on ? "Real phone: ON. Each run phones the demo number for the most urgent new match." : portfolio.phone_ready ? "Real phone: on. Use “call phone” to have Mia ring a mobile." : "Calls ring on the resident page. Real phone calls are off (see README to switch them on)."}</p>
    <h2 class="staff-divider">Case · flat ${esc(UNIT.replace("unit_", "").toUpperCase())}</h2>`;
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
    checkIncoming();
    const avatar = document.querySelector(".topbar-right .avatar");
    const name = state.unit?.tenant?.name;
    if (avatar && name) avatar.textContent = name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  }
  renderCall();
  renderDecision();
  renderPartner();
  renderManager();
}

document.addEventListener("click", async (event) => {
  const t = event.target.closest("[data-call],[data-decide],[data-partner],[data-run],[data-phone],#demo-reset");
  if (!t) return;
  if (t.dataset.call === "start") startCall();
  if (t.dataset.call === "end") endCall();
  if (t.dataset.call === "answer") answerIncoming();
  if (t.dataset.call === "decline") declineIncoming();
  if (t.dataset.call === "mute") { call.muted = !call.muted; call.conversation?.setMicMuted(call.muted); renderCall(); }
  if (t.dataset.call === "simulate") simulateCall();
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
    ring(false);
    Object.assign(call, { status: "idle", transcript: [], simulated: false, linkSent: null, error: "", incoming: null, muted: false });
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

window.flow = { render, renderPartner };
render();
})();
