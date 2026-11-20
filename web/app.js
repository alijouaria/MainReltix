const state = { unit: null, partner: null };
const chatHistory = [];
const byId = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[char]));
const money = (value) => new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" }).format(Number(value || 0));
const prettyDate = (value) => value ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(new Date(`${value}T12:00:00`)) : "Not set";

function energyContract() {
  return state.unit?.contracts?.find((contract) => contract.type === "energy_supply") || {};
}

function electricityMeter() {
  return state.unit?.unit?.meters?.find((meter) => meter.type === "electricity") || {};
}

function daysUntil(value) {
  if (!value) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const expiry = new Date(`${value}T00:00:00`);
  return Math.ceil((expiry - today) / 86_400_000);
}

function renderOffers(targetId) {
  const offers = state.partner?.offers || [];
  const target = byId(targetId);
  if (!offers.length) {
    target.innerHTML = '<div class="reading-summary">No partner offers have been added yet.</div>';
    return;
  }
  target.innerHTML = offers.map((offer) => `
    <div class="offer-row">
      <div class="offer-main">
        <span class="offer-title">${esc(offer.product)}</span>
        <span class="offer-meta"><span>${Number(offer.renewable_share_percent || 0)}% renewable</span><span>${Number(offer.contract_months || 0)} months</span></span>
      </div>
      <div class="offer-price">${money(offer.estimated_monthly_price_eur)}<span>per month · estimate</span></div>
    </div>`).join("");
}

function render() {
  if (!state.unit || !state.partner) return;
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
  renderOffers("tenant-offers");
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
  byId("expiry-subtext").textContent = offers.length
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

function localAgentReply(message) {
  const text = message.toLowerCase();
  const tenant = state.unit.tenant || {};
  const contract = energyContract();
  const remaining = daysUntil(contract.expiry_date);
  const offers = state.partner.offers || [];
  if (/contract|agreement/.test(text)) {
    return `Your ${contract.category || "energy"} contract with ${contract.supplier || "your current provider"}: ${contract.product || "product not listed"}. Status: ${contract.status || "not listed"}. Start: ${prettyDate(contract.start)}; expires: ${prettyDate(contract.expiry_date)}. Monthly price: ${money(contract.current_monthly_price_eur)}. Annual usage: ${Number(contract.annual_consumption_kwh || 0).toLocaleString("en-GB")} kWh.`;
  }
  if (/expir|end date|when.*contract|contract.*when/.test(text)) {
    if (remaining === null) return "I can’t find an expiry date in the sample contract record.";
    if (remaining < 0) return `The listed contract expiry date was ${prettyDate(contract.expiry_date)}, ${Math.abs(remaining)} days ago. I can’t check its live status in this demo.`;
    return `Your ${contract.category || "energy"} contract with ${contract.supplier || "your current provider"} is listed to expire on ${prettyDate(contract.expiry_date)} (${remaining} days from today).`;
  }
  if (/offer|compare|renew|tariff|deal/.test(text)) {
    if (!offers.length) return "There aren’t any partner offers in the demo right now. The company view can add some.";
    const choices = offers.map((offer) => `${offer.product}: ${money(offer.estimated_monthly_price_eur)} per month, ${Number(offer.renewable_share_percent || 0)}% renewable, ${Number(offer.contract_months || 0)} months`).join("\n");
    return `Here are the sample offers:\n${choices}\nThese are demo estimates. I can explain them, but this demo won’t sign or order a contract.`;
  }
  if (/meter|reading|kwh/.test(text)) {
    const reading = electricityMeter().switch_reading || {};
    if (reading.value_kwh !== null && reading.value_kwh !== undefined) return `A changeover reading of ${Number(reading.value_kwh).toLocaleString("en-GB")} kWh was recorded for ${prettyDate(reading.read_at)}.`;
    return `There’s no changeover reading entered yet. You can add the meter value and date in the Changeover reading card. The last known reading is ${Number(electricityMeter().last_reading?.value_kwh || 0).toLocaleString("en-GB")} kWh from ${prettyDate(electricityMeter().last_reading?.read_at)}.`;
  }
  if (/price|cost|pay|supplier|provider|electricity/.test(text)) {
    return `Your current electricity plan is ${contract.product || "not specified"} with ${contract.supplier || "a provider not listed"}, at ${money(contract.current_monthly_price_eur)} per month. The sample record lists ${Number(contract.annual_consumption_kwh || 0).toLocaleString("en-GB")} kWh annual usage.`;
  }
  if (/address|home|property|apartment|flat|room|heating|landlord|owner/.test(text)) {
    const home = state.unit.unit || {};
    const property = state.unit.property || {};
    return `Your home is ${property.address || "an address not listed"}. It’s a ${home.size_sqm || "?"} m², ${home.rooms || "?"}-room unit on floor ${home.floor || "?"}; the building uses ${property.heating || "a heating type not listed"}. The owner record names ${state.unit.owner?.name || "an owner not listed"}.`;
  }
  if (/hello|hi|hey|good morning/.test(text)) return `Hi ${tenant.name?.split(" ")[0] || "there"}! I can answer from the sample records about your home, current supplier, expiry date, offers, or meter reading.`;
  return "I’m the simple local demo assistant, so I answer from the property and offer records only. Try asking when your contract expires, what offers are available, or what meter reading is saved.";
}

function isDirectRecordQuestion(message) {
  return /\b(contract|agreement|lease|rent|offer|tariff|plans|meter|reading|kwh|tenant|resident|owner|landlord|property|home|address|rooms|heating|unit|supplier|provider|price|cost)\b/i.test(message);
}

async function refreshState() {
  const response = await fetch("/api/state", { cache: "no-store" });
  if (!response.ok) throw new Error("Could not load the local demo records.");
  Object.assign(state, await response.json());
  render();
}

function setRole(role) {
  document.querySelectorAll(".role-button").forEach((button) => button.classList.toggle("active", button.dataset.role === role));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `${role}-view`));
  byId("view-crumb").textContent = role === "tenant" ? "Tenant portal" : role === "landlord" ? "Property workspace" : "Energy company";
  document.querySelector(".topbar-right .avatar").textContent = role === "tenant" ? "LH" : role === "landlord" ? "MB" : "EC";
}

document.querySelectorAll(".role-button").forEach((button) => button.addEventListener("click", () => setRole(button.dataset.role)));

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
    if (isDirectRecordQuestion(text)) {
      answer = localAgentReply(text);
      byId("assistant-mode").textContent = "JSON FACTS";
      byId("chat-disclaimer").textContent = "Direct record lookup · Values are read from the sample JSON.";
    } else {
      const response = await fetch("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: chatHistory.slice(-10) })
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Local model unavailable.");
      answer = result.answer;
      byId("assistant-mode").textContent = "QWEN 0.5B";
      byId("chat-disclaimer").textContent = "Local language model · Answers are grounded in the sample records.";
    }
  } catch (error) {
    answer = `${localAgentReply(text)}\n\n(Local LLM isn’t connected yet; showing a basic record-based reply.)`;
    byId("assistant-mode").textContent = "BASIC MODE";
    byId("chat-disclaimer").textContent = "Basic local replies · Start Ollama to use the small language model.";
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
      body: JSON.stringify({ value_kwh: byId("reading-value").value, read_at: byId("reading-date").value })
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
    message.textContent = "Offers saved. Tenant and landlord views are updated.";
    await refreshState();
    setRole("company");
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
});

refreshState().catch((error) => {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  const errorView = document.createElement("section");
  errorView.className = "view active";
  errorView.innerHTML = `<article class="card company-card"><h1>Couldn’t load the demo</h1><p class="muted">${esc(error.message)}</p><p class="muted small">Start it with <code>python main.py</code> from the MainReltix folder, then open <code>http://127.0.0.1:8765</code>.</p></article>`;
  document.querySelector(".main-area").prepend(errorView);
});
