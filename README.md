# MainReltix

Local demo of an energy renewal connector between Centrix, a tenant, a property manager, and an energy partner.

## Run locally

1. Open a terminal in this folder.
2. Run `python main.py` with Python 3.
3. Open <http://127.0.0.1:8765> in a browser.

The app uses only Python's standard library and runs locally. It does not require API keys or connect to external services.

## Optional local language model

The assistant is configured to use Ollama with `qwen2.5:0.5b`, a small 0.5-billion-parameter model. To enable replies, install Ollama, download the model with `ollama pull qwen2.5:0.5b`, and keep Ollama running while the demo is open. The app sends the current Centrix and partner JSON records plus recent chat messages to Ollama's local chat endpoint. If Ollama is unavailable, the app clearly reports that the assistant is offline rather than generating a separate hard-coded reply.

## Demo views

- **Tenant:** property and contract dashboard, a contextual local assistant, and a form for the electricity changeover reading.
- **Landlord:** property, tenant, contract, offer, and meter-reading dashboard.
- **Energy company:** one text area for entering offers, one per line, in this format:

  `Product | monthly price | renewable % | months`

Submitting a meter reading updates `data/centrix/unit_4b.json`. Submitting offers updates `data/partners/electricity_offers.json`.

<<<<<<< Updated upstream
The model receives only the current property/unit and partner-offer records; the prompt tells it to answer from those records and say when something is missing. Chat history stays in the browser session and is not written to JSON. ChatGPT and ElevenLabs can replace or extend the local model later. All people, partner names, prices, and readings are sample data.

## Full flow: voice call → one-tap decision → partner → write-back

Run `python main.py` and open <http://127.0.0.1:8765>. Nothing else to install. Put **/tenant** and **/reltix** side by side in two windows; they update each other live.

1. **Resident:** "Start the call" makes Mia (ElevenLabs voice agent, `web/vendor/elevenlabs-client.js`) call Lena about her expiring contract. Use headphones. "No mic? Simulate it" plays the same call as text. The page then continues where the call left off: chosen plan, saving, consent (exact fields listed), **Confirm & sign**, or **Keep my current contract**.
2. **Energy partner:** the order arrives with only the fields the plan needs. The partner answers like on the call: accept / decline, contract ready / need the meter reading, power running. Each answer is a spec §6 status update; illegal moves return 409.
3. **Edge cases:** the resident declines (nothing shared); the partner declines (resident is asked to pick another plan); the partner needs the meter reading (resident is asked, centrix resubmits automatically).
4. **Property manager:** a "What happened" timeline of every step (0 by the manager), consent log, and contracts on file. The signed contract and consent are written to `unit_4b.json`.
5. **Reset demo** (footer) restores the sample data.

Code: `web/flow.js` (new), small hooks in `web/app.js` and `web/index.html`, styles at the end of `web/styles.css`. Backend: `POST /api/orders`, `POST /api/orders/{id}/status`, `POST /api/events`, `POST /api/reset` in `main.py`.

## Automatic trigger across a portfolio (12 dummy flats)

centrix doesn't wait for someone to click "call". Every day it checks **every flat** against the trigger rule from the connector spec (§11) and decides, with a reason, who gets contacted:

| Rule | Example flat |
|---|---|
| Active contract **inside the renewal window** (today ≥ expiry − `renewal_notice_days`) → **call** | 1A Jonas, 3B Thomas (meter number missing → Mia asks for a photo), 11A Elena (45-day notice) |
| Already **expired** → **urgent call** (probably on a pricier default tariff) | 8C Yusuf |
| In the window but **no partner serves the postcode** → **property manager**, no call | 2C Aylin (Düsseldorf 40479) |
| **Too early** → skipped until the window opens | 4B Lena (opens 1 Dec), 12C Felix (11 Dec), 5A Sophie |
| Tenant **didn't opt in** to share contract data → skipped | 6B Marco |
| **Already renewed**, **no contract on record**, **vacant** → skipped | 7A Hannah, 9A Clara, 10B |

Data: `data/centrix/unit_4b.json` plus `data/centrix/portfolio/*.json` (same schema). The run happens on server start, on **Run again**, and whenever the **demo date** changes on the staff page (`/reltix`). Matched residents' pages (`/tenant?unit=unit_1a` …) **ring by themselves**: Answer → Mia, Decline → SMS link. Each flat has its own case page: `/reltix?unit=…`. Endpoints: `GET /api/portfolio`, `POST /api/clock`, `POST /api/run`, `POST /api/calls/{id}`.

**Demo:** staff page shows today's run (4 calls, 1 for a person, 7 skipped with reasons) → open Jonas's resident page, it's ringing → answer → sign → back on staff, the case for 1A shows it all. Then move the demo date to **1 Dec**: Lena enters her window and her page starts ringing.

### Optional: Mia calls a real phone (automatic)

Uses ElevenLabs + Twilio. Off unless configured.
1. **Twilio:** sign up for the free trial, verify your own mobile (SMS code), buy a number with the trial credit, and under Voice → Settings → **Geo permissions** enable the country you're calling (e.g. Belgium). A trial account can only call numbers you verified.
2. **ElevenLabs → Agents → Channels → Telephony → Import number:** choose Twilio, paste the number, Account SID and Auth Token (from the Twilio console). Assign the agent "reltix Mia". Copy the **phone number ID**.
3. **ElevenLabs → Developers → API keys:** create a key with access to agents.
4. Start the server with these set (PowerShell, one line):
   `$env:ELEVENLABS_API_KEY="…"; $env:ELEVENLABS_PHONE_NUMBER_ID="…"; $env:DEMO_PHONE_NUMBER="+32…"; python main.py`
5. **Automatic:** every run (server start, Run again, or a new demo date) phones `DEMO_PHONE_NUMBER` for the most urgent *new* match, one call per run. In the demo every resident "lives" at your number. Move the demo date to **1 Dec** and your phone rings for Lena. "call phone" next to any queued call dials it by hand.

On a phone line Mia's browser tools don't run. She uses the offers centrix passes in (`offers_summary`) and tells the resident she'll text the confirm link. Making the tools work on the phone means turning them into ElevenLabs *webhook* tools that call this server through a public URL (e.g. ngrok).

### Free: make your own phone ring (no Twilio)

The Twilio trial blocks the audio streaming ElevenLabs needs, so real phone-line calls require a paid Twilio account. For free, open the resident page **on your phone** and it rings by itself when the daily run picks that flat:
1. Laptop: start the server as usual.
2. Second terminal: `winget install Cloudflare.cloudflared`, then `cloudflared tunnel --url http://localhost:8765`. It prints an `https://….trycloudflare.com` address. HTTPS is needed so the phone may use its microphone.
3. Phone: open `https://….trycloudflare.com/tenant?unit=unit_4b` and tap the page once (lets it play sound).
4. Laptop: on `/reltix`, set the demo date to **1 Dec**. The phone rings (and vibrates on Android): Answer, talk to Mia, sign on the phone.
=======
The model receives only the current property/unit and partner-offer records; the prompt tells it to answer from those records and say when something is missing. Recent chat history stays in the browser session and is included with each model request, so follow-up questions retain their meaning. It is not written to JSON. ChatGPT and ElevenLabs can replace or extend the local model later. All people, partner names, prices, and readings are sample data.
>>>>>>> Stashed changes
