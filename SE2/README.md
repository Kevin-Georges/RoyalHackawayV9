# Incident Summary System

Uncertainty-aware incident summary from emergency call transcript stream. No asserted facts; only accumulated evidence with explicit confidence. Probabilities are updatable via merge logic; LLM extraction includes hallucination handling.

## Setup

1. **Create a virtual environment and install dependencies**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   ```

2. **Optional: Use OpenAI for extraction**

   - Copy `.env.example` to `.env`.
   - Set `OPENAI_API_KEY=sk-your-key` in `.env`.
   - If the key is not set, the system uses regex extraction (no API calls).

## Run

```bash
python run_api.py
```

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

Tests cover: core models (ConfidenceValue, LocationValue, Incident), Bayesian posterior and apply_claims, regex extractor (fire, gun shot, locations, people, hazards), Judge default scores, API (POST /chunk, GET /incident, demo-locations, boost_repeated_mention). One Judge test runs only when `OPENAI_API_KEY` is set.

- API: http://localhost:8000  
- Dashboard: http://localhost:8000/dashboard/  
- Health: http://localhost:8000/health  

## API

- **POST /chunk** — Ingest a transcript chunk. Body: `{ "text": "...", "incident_id": "incident-001" }`. Returns updated summary and claims added.
- **GET /incident/{incident_id}** — Full incident state (summary + timeline).
- **GET /incident/{incident_id}/timeline** — Timeline only.
- **GET /incidents** — List incident IDs.
- **GET /health** — Status and extractor type (openai vs regex).

## Dashboard

- Submit transcript chunks; view current summary and timeline.
- Summary shows location, incident type, people estimate, hazards — each with confidence.
- Timeline is an append-only audit log of every extracted claim.

## Hallucination handling (OpenAI)

- Extraction prompt instructs the model to only state what is explicitly in the transcript.
- Each extracted value is checked for grounding in the source text; confidence is capped when the value is not clearly present.
- Probabilities are merged over time (never reduced) so new evidence updates confidence.

## Providing your API key

Put your OpenAI API key in a `.env` file in the project root:

```
OPENAI_API_KEY=sk-your-actual-key
```

Do not commit `.env`. The app uses it when you run `python run_api.py` (if `python-dotenv` is installed; add `python-dotenv` to `requirements.txt` and run `pip install -r requirements.txt` if you want automatic loading).
