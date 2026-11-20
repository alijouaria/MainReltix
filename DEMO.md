# Electricity renewal walkthrough

**Start (one click):** double-click **`Start Reltix.bat`** (Mac/Linux: `python3 launch.py`). It starts the app and the phone link, opens the staff dashboard and copies a phone link to the clipboard. On the dashboard, click **📱 Phone**, pick a flat and scan the QR code with your phone. Close the window to stop. (Needs cloudflared once: `winget install Cloudflare.cloudflared`.)


Start `python main.py` (the demo begins on 20 Nov 2026; add `--demo-date 2026-12-01` to start on Lena’s renewal). Use the home link to switch workspaces. The default flat, 4B, is Lena’s.

1. **Tenant** · Lena’s current contract ends on 31 December. Choose Green Home 12, review the €78.40 monthly estimate, tick consent and select **Confirm & sign**.
2. **Report a problem → Simulate complaint call** · Enter “I’m still waiting for my start date.” Select the supplier-sharing option and finish the call. Without that selection, the report goes to the property team. A tenant can also report a problem before placing an order.
3. **Energy supplier** · Choose **Still checking** for a pending answer or **Yes, I’ll take it → Confirm start date** to confirm 1 January. Open the complaint, enter a phone reply and select **Send reply**. Confirming the order and answering the complaint are separate actions.
4. **Tenant** · Read the reply under the complaint. In 4B, confirmation also plays forward the simulated remote reading to 31 December and passes 18,610 kWh to the supplier.
5. **Owner portal** · Check the confirmed start. **IT staff** shows the filed contract, consent and timeline. Staff can answer complaints assigned to them. **Start day: power is running** in the supplier view completes the demo handover.

For the manual reading path, open flat **1A** from the staff dashboard. Confirm its order or request a meter reading from the supplier view, then enter the end-date reading in the tenant portal. The supplier can complete activation after receiving it.

Use **Reset demo** on the staff dashboard to repeat the walkthrough.


## Mia's calls (voice)

- **Renewal calls:** when the daily check matches a flat, Mia calls the resident. The resident page (`/tenant?unit=…`) rings by itself: Answer → Mia (ElevenLabs, needs mic), Decline → SMS link, **No mic? Simulate** plays the call as text. What the resident says (plan, meter reading, notes) lands on the flat and shows up on the staff page.
- **Callbacks:** say "call me back in 10 seconds / 5 minutes"; Mia hangs up and the page rings again when it's due.
- **Complaints:** **Report a problem → Call AI agent** (or Simulate complaint call).
- **Call now:** on the staff page, **☎ Call** next to any flat makes Mia call that resident immediately.

## Staff dashboard

- **Date bar:** +1 day, +1 week, or **Next event** jumps to the next date something happens.
- **Needs you:** everything centrix and Mia couldn't finish alone (flags, complaints, requests for a person), urgent first, with **Open case** and **Mark handled**.
- **Flats:** search (press `/`), filter chips, 10 per page. Click a row to open the case; `⋯` opens the resident, supplier or owner view.
- **Live activity** + pop-ups show what residents, Mia and the supplier just did. The case panel follows the latest activity; on a pinned flat, the pill at the top takes you back to live.

## Date guide (set the demo date in the sidebar, then watch "Today's run")

Dates are cumulative, like real days passing: a later date also includes everything before it.

| Demo date | What centrix does | Flat · resident |
|---|---|---|
| 20 Nov (today) | calls 1A, 3B, 8C (urgent: expired), 11A (45-day notice) | Jonas, Thomas, Yusuf, Elena |
| 20 Nov | ⚠ flags: no partner for the postcode | 2C Aylin (Düsseldorf) |
| 20 Nov | ⚠ flags: no phone or email, letter needed | 19C Georg |
| 20 Nov | ⚠ flags: contract has no end date | 22C Julia |
| 20 Nov | skips: already cheaper than any partner offer | 23A Oliver |
| 20 Nov | skips: commercial unit | 25A Bäckerei Krause |
| 22 Nov | ⚠ flags: shared building meter, landlord decides | 27C Tim |
| 1 Dec | calls | 4B Lena |
| 2 Dec | calls | 28A Ben |
| 6 Dec | ⚠ flags: two active contracts on file (data conflict) | 20B Sarah |
| 9 Dec | asked not to be called: SMS link instead of a call | 24B Lea |
| 11 Dec | calls | 12C Felix |
| 16 Dec | calls | 13A Nina |
| 21 Dec | ⚠ flags: unusual usage (9,800 kWh in 48 m²) | 21A Dennis |
| 30 Dec | calls (60-day notice period, so earlier than usual) | 16A Anna |
| 1 Jan | calls | 14B Lukas |
| 6 Jan | calls in German | 26B Helga |
| 15 Jan | calls | 15C Mehmet |
| 21 Jan | ⚠ flags: moves out before the contract ends | 18A Katrin |
| 13 Feb | calls | 17B Paul |
| 1 Mar | calls | 5A Sophie |

Always skipped: 6B Marco (no opt-in), 7A Hannah (already renewed), 9A Clara (no contract), 10B (vacant).
Flagged flats show at the top of "Today's run" in amber, count in the "Need a person" tile, and get a "⚠ Needs a person" entry on their case.
To see a resident's phone for a flat, open `/tenant?unit=unit_13a` (any flat id).
