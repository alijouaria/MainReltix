# MainReltix

Local demo of an energy renewal connector between Centrix, a tenant, a property manager, and an energy partner.

## Run locally

1. Open a terminal in this folder.
2. Run `python main.py` with Python 3.
3. Open <http://127.0.0.1:8765> in a browser.

The app uses only Python's standard library and runs locally. It does not require API keys or connect to external services.

## Optional local language model

The assistant is configured to use Ollama with `qwen2.5:0.5b`, a small 0.5-billion-parameter model. To enable LLM replies, install Ollama, download the model with `ollama pull qwen2.5:0.5b`, and keep Ollama running while the demo is open. The app sends the current Centrix and partner JSON records to Ollama's local chat endpoint. Without Ollama, the assistant falls back to basic record-based replies.

## Demo views

- **Tenant:** property and contract dashboard, a contextual local assistant, and a form for the electricity changeover reading.
- **Landlord:** property, tenant, contract, offer, and meter-reading dashboard.
- **Energy company:** one text area for entering offers, one per line, in this format:

  `Product | monthly price | renewable % | months`

Submitting a meter reading updates `data/centrix/unit_4b.json`. Submitting offers updates `data/partners/electricity_offers.json`.

The model receives only the current property/unit and partner-offer records; the prompt tells it to answer from those records and say when something is missing. Chat history stays in the browser session and is not written to JSON. ChatGPT and ElevenLabs can replace or extend the local model later. All people, partner names, prices, and readings are sample data.
