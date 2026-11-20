const state = { unit: null, partner: null };
const chatHistory = [];
// Each role has its own page (tenant.html, landlord.html, partner.html) with only its own elements.
// Elements that belong to another role's page resolve to a detached placeholder, so shared code is safe.
const ROLE = document.body.dataset.role;
// Which flat this page shows. The staff page without ?unit= follows the flat with the latest activity.
const PINNED_UNIT = new URLSearchParams(location.search).get("unit");
let UNIT = PINNED_UNIT || "unit_4b";
const ABSENT = document.createElement("div");
const byId = (id) => document.getElementById(id) || ABSENT;
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[char]));
const money = (value) => new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" }).format(Number(value || 0));
const prettyDate = (value) => value ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(new Date(`${value}T12:00:00`)) : "Not set";

function energyContract() {
  const contracts = state.unit?.contracts?.filter(contract => contract.type === "energy_supply") || [];
  return contracts.find(contract => contract.status === "active") || contracts.find(contract => contract.status === "ending") || contracts[0] || {};
}

function electricityMeter() {
  const meters = (state.unit?.unit?.meters || []).filter((meter) => meter.type === "electricity");
  const selectedId = byId("electric-meter-select").value || state.unit?.unit?.selected_electricity_meter_id;
  return meters.find((meter) => meter.meter_id === selectedId) || meters[0] || {};
}

function renderMeterControls() {
  const meters = (state.unit?.unit?.meters || []).filter((meter) => meter.type === "electricity");
  const select = byId("electric-meter-select");
  const previous = select.value || state.unit?.unit?.selected_electricity_meter_id;
  select.innerHTML = meters.map((meter, index) => `<option value="${esc(meter.meter_id)}">${esc(meter.number || `Electricity meter ${index + 1} · number needed`)}</option>`).join("");
  if (meters.some((meter) => meter.meter_id === previous)) select.value = previous;
  if (meters.length > 1) byId("meter-select-wrap").hidden = false;
  else if (meters.length === 1) { select.value = meters[0].meter_id; byId("meter-select-wrap").hidden = false; }
  else { select.innerHTML = '<option value="">Add an electricity meter below</option>'; byId("meter-select-wrap").hidden = false; }
  const meter = meters.find((item) => item.meter_id === select.value) || meters[0];
  byId("meter-identifier-value").value = meter?.number || "";
  byId("meter-identifier-value").required = !meter?.number;
}

window.selectedMeterId = () => byId("electric-meter-select").value || state.unit?.unit?.selected_electricity_meter_id || "";

function daysUntil(value) {
  if (!value) return null;
  const today = state.today ? new Date(`${state.today}T00:00:00`) : new Date(); // demo clock from centrix
  today.setHours(0, 0, 0, 0);
  const expiry = new Date(`${value}T00:00:00`);
  return Math.ceil((expiry - today) / 86_400_000);
}

let chosenOffer = null;

function renderOffers(targetId, selectable = false) {
  const offers = state.partner?.offers || [];
  const target = byId(targetId);
  if (!offers.length) {
    target.innerHTML = '<div class="reading-summary">No partner offers have been added yet.</div>';
    return;
  }
  const best = offers.reduce((a, b) => Number(b.estimated_monthly_price_eur) < Number(a.estimated_monthly_price_eur) ? b : a);
  const tag = selectable ? "button" : "div";
  target.innerHTML = offers.map((offer, index) => `
    <${tag} class="offer-row${offer === best ? " recommended" : ""}"${selectable ? ` type="button" data-index="${index}" aria-pressed="${chosenOffer === index}"` : ""}>
      <div class="offer-main">
        <span class="offer-title">${esc(offer.product)}</span>
        <span class="offer-meta">${offer === best ? '<span class="offer-tag">Lowest price</span>' : ""}<span>${Number(offer.renewable_share_percent || 0)}% renewable</span><span>${Number(offer.contract_months || 0)} months</span></span>
      </div>
      <div class="offer-price">${money(offer.estimated_monthly_price_eur)}<span>${chosenOffer === index && selectable ? "your pick" : "per month · estimate"}</span></div>
    </${tag}>`).join("");
  if (selectable) target.querySelectorAll("button.offer-row").forEach((row) => row.addEventListener("click", () => {
    chosenOffer = Number(row.dataset.index);
    renderOffers(targetId, true);
    window.flow?.render();
  }));
}

function render() {
  if (!state.unit || !state.partner) return;
  renderMeterControls();
  const unit = state.unit;
  const tenant = unit.tenant || {};
  const property = unit.property || {};
  const home = unit.unit || {};
  const contract = energyContract();
  const meter = electricityMeter();
  const lease = unit.contracts?.find((item) => item.type === "lease") || {};
  const offers = state.partner.offers || [];
  const remaining = daysUntil(contract.expiry_date);

  byId("tenant-title").textContent = `Good morning, ${tenant.name?.split(" ")[0] || "there"}`;
  byId("tenant-address").textContent = property.address || "Address unavailable";
  byId("tenant-unit-meta").textContent = `Unit ${unit.unit_id || "—"} · ${home.size_sqm || "—"} m² · ${home.rooms || "—"} rooms`;
  byId("current-provider").textContent = contract.supplier || "Not listed";
  byId("current-price").textContent = contract.current_monthly_price_eur ? `${money(contract.current_monthly_price_eur)} / mo` : "Not listed";
  byId("contract-expiry").textContent = prettyDate(contract.expiry_date);
  byId("tenant-contract-title").textContent = `${contract.category ? contract.category[0].toUpperCase() + contract.category.slice(1) : "Energy"} supply`;
  byId("offer-count").textContent = `${offers.length} ${offers.length === 1 ? "offer" : "offers"}`;
  byId("landlord-offer-count").textContent = `${offers.length} ${offers.length === 1 ? "offer" : "offers"}`;
  byId("partner-name").textContent = state.partner.partner || "Energy company";
  renderOffers("tenant-offers", true);
  renderOffers("landlord-offers");

  const expiryBadge = byId("expiry-badge");
  if (remaining === null) {
    expiryBadge.textContent = "Expiry unavailable";
    expiryBadge.className = "badge badge-soft";
    byId("expiry-message").textContent = "Your contract end date isn’t listed yet.";
  } else if (remaining < 0) {
    expiryBadge.textContent = "Past expiry date";
    expiryBadge.className = "badge badge-amber";
    byId("expiry-message").textContent = `This contract passed its expiry date ${Math.abs(remaining)} days ago.`;
  } else if (remaining === 0) {
    expiryBadge.textContent = "Expires today";
    expiryBadge.className = "badge badge-amber";
    byId("expiry-message").textContent = "Your contract expires today.";
  } else {
    expiryBadge.textContent = `${remaining} days left`;
    expiryBadge.className = remaining <= 60 ? "badge badge-amber" : "badge badge-green";
    byId("expiry-message").textContent = `Your current contract ends in ${remaining} days.`;
  }
  const currentOrder = (state.orders || []).at(-1);
  byId("expiry-subtext").textContent = (!state.offer_available || (currentOrder && currentOrder.status !== "rejected")) ? "" : offers.length
    ? `${offers.length} partner ${offers.length === 1 ? "offer is" : "offers are"} available to review.`
    : "There are no partner offers available yet.";

  byId("landlord-property").textContent = property.address || "Address unavailable";
  byId("landlord-property-meta").textContent = `${property.building_year || "—"} · ${property.heating || "Heating not listed"}`;
  byId("landlord-tenant").textContent = tenant.name || "No tenant listed";
  byId("landlord-tenant-meta").textContent = `Moved in ${prettyDate(tenant.move_in)}`;
  byId("landlord-expiry").textContent = prettyDate(contract.expiry_date);
  byId("landlord-days").textContent = remaining === null ? "Expiry date unavailable" : remaining < 0 ? `${Math.abs(remaining)} days past expiry` : `${remaining} days remaining`;
  byId("landlord-provider").textContent = contract.supplier || "Not listed";
  byId("landlord-price").textContent = contract.current_monthly_price_eur ? `${money(contract.current_monthly_price_eur)} / month` : "Price not listed";

  const detailRows = [
    ["Unit", unit.unit_id], ["Floor", home.floor], ["Size", home.size_sqm ? `${home.size_sqm} m²` : "—"],
    ["Rooms", home.rooms], ["Heating", property.heating], ["Certificate", property.energy_certificate?.class ? `Class ${property.energy_certificate.class}` : "—"],
    ["Owner", unit.owner?.name], ["Tenant", tenant.name], ["Lease rent", lease.rent_cold_eur ? `${money(lease.rent_cold_eur)} / month` : "—"],
    ["Annual usage", contract.annual_consumption_kwh ? `${Number(contract.annual_consumption_kwh).toLocaleString("en-GB")} kWh` : "—"],
  ];
  byId("property-details").innerHTML = detailRows.map(([label, value]) => `<div class="detail-row"><span>${esc(label)}</span><strong>${esc(value || "—")}</strong></div>`).join("");

  const switchReading = meter.switch_reading || {};
  const hasReading = switchReading.value_kwh !== null && switchReading.value_kwh !== undefined && switchReading.value_kwh !== "";
  byId("reading-status").textContent = hasReading ? "Reading saved" : "Not entered";
  byId("reading-status").className = hasReading ? "badge badge-green" : "badge badge-soft";
  byId("landlord-reading-status").textContent = hasReading ? "Reading saved" : "Not entered";
  byId("landlord-reading-status").className = hasReading ? "badge badge-green" : "badge badge-soft";
  byId("landlord-reading-summary").textContent = hasReading
    ? `${Number(switchReading.value_kwh).toLocaleString("en-GB")} kWh recorded on ${prettyDate(switchReading.read_at)}.`
    : `No changeover reading has been entered. The last known reading is ${Number(meter.last_reading?.value_kwh || 0).toLocaleString("en-GB")} kWh from ${prettyDate(meter.last_reading?.read_at)}.`;
  if (hasReading) {
    byId("reading-value").value = switchReading.value_kwh;
    byId("reading-date").value = switchReading.read_at || "";
  }

  renderJob();
  renderPlans();
  const offersText = offers.map((offer) => `${offer.product} | ${Number(offer.estimated_monthly_price_eur).toFixed(2)} | ${Number(offer.renewable_share_percent)} | ${Number(offer.contract_months)}`).join("\n");
  byId("offers-input").value = offersText;
  if (!document.querySelector("#chat-messages .message")) {
    addMessage("assistant", `Hi ${tenant.name?.split(" ")[0] || "there"}. I can help with your home and electricity contract. It currently lists ${contract.supplier || "your supplier"} and ${offers.length} renewal ${offers.length === 1 ? "offer" : "offers"}. What would you like to know?`);
  }
}

function addMessage(kind, text) {
  const message = document.createElement("div");
  message.className = `message ${kind}`;
  message.textContent = text;
  byId("chat-messages").appendChild(message);
  byId("chat-messages").scrollTop = byId("chat-messages").scrollHeight;
}

async function refreshState() {
  const follow = ROLE === "reltix" && !PINNED_UNIT;
  const response = await fetch(`/api/state?role=${encodeURIComponent(ROLE)}${follow ? "" : `&unit=${encodeURIComponent(UNIT)}`}`, { cache: "no-store" });
  if (!response.ok) throw new Error("Could not load the local demo records.");
  Object.assign(state, await response.json());
  if (follow && state.unit_id) UNIT = state.unit_id;
  render();
  window.flow?.render();
}

byId("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("chat-input");
  const text = input.value.trim();
  if (!text || !state.unit) return;
  addMessage("user", text);
  chatHistory.push({ role: "user", content: text });
  input.value = "";
  input.disabled = true;
  const sendButton = document.querySelector("#chat-form .send-button");
  sendButton.disabled = true;
  const responseBubble = document.createElement("div");
  responseBubble.className = "message assistant";
  responseBubble.textContent = "Thinking locally…";
  byId("chat-messages").appendChild(responseBubble);
  byId("chat-messages").scrollTop = byId("chat-messages").scrollHeight;
  let answer;
  try {
    const response = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatHistory.slice(-16), unit_id: UNIT })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Local model unavailable.");
    answer = result.answer;
    byId("assistant-mode").textContent = "QWEN 0.5B";
    byId("chat-disclaimer").textContent = "Local language model · Uses the sample JSON and recent conversation.";
  } catch (error) {
    answer = "I can’t reach the local assistant right now. Start Ollama and make sure qwen2.5:0.5b is installed, then try again.";
    byId("assistant-mode").textContent = "OFFLINE";
    byId("chat-disclaimer").textContent = "The local language model must be running for chat replies.";
  } finally {
    input.disabled = false;
    sendButton.disabled = false;
    input.focus();
  }
  responseBubble.textContent = answer;
  chatHistory.push({ role: "assistant", content: answer });
  byId("chat-messages").scrollTop = byId("chat-messages").scrollHeight;
});

document.querySelectorAll(".suggestion").forEach((button) => button.addEventListener("click", () => {
  byId("chat-input").value = button.dataset.prompt;
  byId("chat-form").requestSubmit();
}));

byId("reading-date").value = new Date().toISOString().slice(0, 10);
byId("reading-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = byId("reading-message");
  message.className = "form-message";
  message.textContent = "Saving…";
  try {
    const response = await fetch("/api/reading", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value_kwh: byId("reading-value").value, read_at: byId("reading-date").value,
        unit_id: UNIT, meter_id: window.selectedMeterId() })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save the reading.");
    message.textContent = "Reading saved to the local unit record.";
    await refreshState();
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
});

byId("electric-meter-select").addEventListener("change", async () => {
  const message = byId("meter-identifier-message");
  try {
    const response = await fetch("/api/meter", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unit_id: UNIT, meter_id: window.selectedMeterId() }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not select that meter.");
    await refreshState();
  } catch (error) { message.textContent = error.message; message.classList.add("error"); }
});

byId("meter-identifier-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = byId("meter-identifier-message");
  message.className = "form-message";
  message.textContent = "Saving…";
  try {
    const response = await fetch("/api/meter", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unit_id: UNIT, meter_id: window.selectedMeterId(), number: byId("meter-identifier-value").value }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save the meter number.");
    message.textContent = "Meter details saved.";
    await refreshState();
  } catch (error) { message.textContent = error.message; message.classList.add("error"); }
});

byId("offers-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = byId("offers-message");
  message.className = "form-message";
  message.textContent = "Saving…";
  try {
    const response = await fetch("/api/offers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: byId("offers-input").value })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save the offers.");
    message.textContent = "Saved. Customers now see these plans.";
    await refreshState();
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
});


/* ---------- Partner inbox: real orders, see flow.js ---------- */
function renderJob() { window.flow?.renderPartner(); }

/* ---------- Plans editor: simple rows, saved through the existing offers form ---------- */
function planRow(offer = {}) {
  const v = (x) => x === undefined || x === null ? "" : esc(x);
  return `<div class="plan-row">
    <label><span>Plan name</span><input data-k="name" value="${v(offer.product)}" required></label>
    <label><span>€ / month</span><input data-k="price" type="number" step="0.01" min="0" value="${v(offer.estimated_monthly_price_eur)}" required></label>
    <label><span>% green</span><input data-k="green" type="number" min="0" max="100" value="${v(offer.renewable_share_percent)}" required></label>
    <label><span>Months</span><input data-k="months" type="number" min="1" value="${v(offer.contract_months)}" required></label>
    <button class="remove-plan" type="button" aria-label="Remove plan">×</button>
  </div>`;
}
function renderPlans() {
  const box = byId("plan-rows");
  if (box.contains(document.activeElement)) return;
  box.innerHTML = (state.partner?.offers || []).map(planRow).join("") || planRow();
}
byId("add-plan").addEventListener("click", () => byId("plan-rows").insertAdjacentHTML("beforeend", planRow()));
byId("plan-rows").addEventListener("click", (event) => {
  if (event.target.classList.contains("remove-plan")) event.target.closest(".plan-row").remove();
});
byId("offers-form").addEventListener("submit", () => {
  byId("offers-input").value = [...document.querySelectorAll(".plan-row")].map((row) => {
    const get = (k) => row.querySelector(`[data-k="${k}"]`).value.trim();
    return `${get("name")} | ${get("price")} | ${get("green")} | ${get("months")}`;
  }).join("\n");
}, true);

refreshState().catch((error) => {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  const errorView = document.createElement("section");
  errorView.className = "view active";
  errorView.innerHTML = `<article class="card company-card"><h1>Couldn’t load the demo</h1><p class="muted">${esc(error.message)}</p><p class="muted small">Start it with <code>python main.py</code> from the MainReltix folder, then open <code>http://127.0.0.1:8765</code>.</p></article>`;
  document.querySelector(".main-area").prepend(errorView);
});
