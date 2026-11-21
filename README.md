# MainReltix

Electricity renewal demo on the Centrix dashboard. Residents choose their plan in the tenant portal. Phone calls handle complaints and supplier follow-up.

## Run locally

Run `python main.py --demo-date 2026-12-01` with Python 3.10 or later and open <http://127.0.0.1:8765/>. Add `--port 8769` to use another port. This date opens Lena’s renewal window in flat 4B.

The home page links to the tenant, staff, owner and supplier workspaces. The staff dashboard also links each workspace to its selected flat. See [the walkthrough](DEMO.md) for the full handover.

The local simulation uses Python’s standard library and sample JSON. It works without a microphone or API keys. **Call AI agent** uses the existing ElevenLabs browser integration and requires microphone and network access. Complaint calls use the live voice agent. Supplier calls and orders are simulated. The optional resident chat uses a local Ollama instance with `qwen2.5:0.5b`.

## Demo data

The daily check finds opted-in residents within the renewal window and the partner’s postcode coverage. Matched residents see a portal offer. Staff can change the date, rerun the check or reset the sample data. Orders, replies, consent and contract history are stored under `data/centrix/`.

The owner sees the unit status and confirmed start date. The supplier sees its accepted orders and complaints the tenant chose to share. These are role-specific demo views on a local server.
