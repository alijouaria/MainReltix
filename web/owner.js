"use strict";
const flat = new URLSearchParams(location.search).get("unit") || "unit_4b";
const labels = { idle: "No renewal due", offered: "Offer ready for the tenant", declined: "Tenant kept the current plan",
  submitted: "Awaiting supplier confirmation", received: "Supplier is processing the order", pending: "Supplier checking the start date",
  needs_input: "Waiting for a meter reading",
  confirmed: "Start date confirmed", active: "New supply active", rejected: "Supplier declined the request", cancelled: "Order cancelled" };
async function showOwnerStatus() {
  const status = document.getElementById("owner-status");
  try {
    const response = await fetch("/api/state?role=owner&unit=" + encodeURIComponent(flat), { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load this unit.");
    const unit = await response.json();
    document.getElementById("flat-title").textContent = "Flat " + unit.unit_id.replace(/^unit_/, "").toUpperCase();
    document.getElementById("flat-address").textContent = unit.address;
    const start = unit.start && new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" }).format(new Date(unit.start));
    status.textContent = (labels[unit.status] || "Renewal status unavailable") + (start ? " · " + start : "")
      + (unit.start_unconfirmed ? " · start not confirmed; property team owns follow-up" : "")
      + (unit.start_date_mismatch ? ` · supplier proposed ${new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" }).format(new Date(unit.start_date_mismatch.proposed + "T00:00:00Z"))}; neither date is confirmed` : "")
      + (!unit.supplier_known && ["cancelled", "rejected"].includes(unit.status) ? " · current supplier is not confirmed" : "");
  } catch (error) { status.textContent = error.message; }
}
showOwnerStatus();
setInterval(() => { if (!document.hidden) showOwnerStatus(); }, 2500);
