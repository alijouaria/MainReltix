# Demo script

Start: `python main.py` (Windows: `& "$env:USERPROFILE\anaconda3\python.exe" main.py`).
Phone: `& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8765`, then open the printed `https://….trycloudflare.com` address on the phone.

Before every run: click **Reset demo** (bottom of any page). This puts the demo date back to today and clears calls, orders and notes.
Keep the staff page `/reltix` open on the laptop next to the phone: it automatically follows the flat with the latest activity (title shows "· live"), and everything the resident says shows up there within ~2 seconds. To look at one specific flat, open `/reltix?unit=unit_1a` (or click a flat in "Today's run").

| # | What it shows | Open on the phone | What to do | What you should see |
|---|---|---|---|---|
| 1 | **Automatic trigger** (the "wow") | `/tenant?unit=unit_4b` (Lena) | Page stays quiet. On the laptop staff page set **Demo date → 1 Dec 2026**. | Lena's phone rings by itself a few seconds later. Staff page: "Renewal window open … case opened automatically". |
| 2 | **Full renewal by voice** | `/tenant?unit=unit_1a` (Jonas) | Answer. Say yes, pick the cheaper offer, read a meter number (e.g. "one two zero three four"), ask for SMS. | Mia hangs up herself. Offer pre-selected on the page; staff page "What the resident told us" shows choice, SMS, meter reading. |
| 3 | **Sign → partner → active** | same page after #2 | Tick consent, **Sign**. On the staff page answer as the partner: Received → Confirmed → Active. | Resident sees live status steps; staff page shows the new contract written back and the consent log. |
| 4 | **Callback** | `/tenant?unit=unit_3b` (Thomas) | Answer and say "can you call me back in 10 seconds?" | Mia confirms, says goodbye, hangs up. ~10 s later: "Calling you back". |
| 5 | **Urgent case** | `/tenant?unit=unit_8c` (Yusuf) | Rings immediately on load. | Contract already expired 5 days ago → marked urgent. |
| 6 | **Says no / wants a human** | `/tenant?unit=unit_11a` (Elena) | Say "no thanks, I already sorted it" (or "I want to talk to a person"). | Mia accepts right away and hangs up; staff page shows "declined" (or "needs a person"). |
| 7 | **Report a problem** (resident calls Mia) | `/tenant?unit=unit_1a` | Decline or finish the renewal call, scroll to "Report a problem", tap **Call AI agent**. Say "water is leaking under my kitchen sink since this morning". | Mia asks 1–2 questions, flags it urgent, hangs up. Ticket shows under the card ("Sent to your property team") and on the staff page as "⚠ Urgent problem". |
| 8 | **Why not everyone gets called** | staff page `/reltix` | Scroll to "Today's run". | 12 flats, each with a reason: too early, no opt-in, already renewed, vacant, no partner in Düsseldorf (manager notified). |

No microphone? Every incoming call also has **Simulate** on the card (laptop view).

If something goes wrong on stage: **Reset demo** and start the scenario again. If the phone page won't load, pause the VPN on the laptop.
