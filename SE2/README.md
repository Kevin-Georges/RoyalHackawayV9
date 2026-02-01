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

**API (incident engine):**
```bash
python run_api.py
```

**Real-time voice (Python, recommended):** Run from the SE2 directory with `DEEPGRAM_API_KEY` set (e.g. in `.env`). Clients (dashboard, root `index.html`) connect to `ws://localhost:8080`.
```bash
# From SE2 directory
python voice_server.py
# or: uvicorn voice_server:app --host 0.0.0.0 --port 8080
```
Same WebSocket protocol as the legacy Node `server.js`; no front-end changes needed.

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

Tests cover: core models (ConfidenceValue, LocationValue, Incident), Bayesian posterior and apply_claims, regex extractor (fire, gun shot, locations, people, hazards), Judge default scores, API (POST /chunk, GET /incident, demo-locations, boost_repeated_mention). One Judge test runs only when `OPENAI_API_KEY` is set.

- API: http://localhost:8000  
- Dashboard: http://localhost:8000/dashboard/  
- Health: http://localhost:8000/health  

## Clustering (dynamic incident assignment)

Reports can be **clustered** into incidents so multiple calls form one incident when they refer to the same event. Enable with `auto_cluster: true` on **POST /chunk**.

- **Embedding similarity**: Report and incident summaries (including device_geo when present) are embedded; cosine similarity contributes so "first floor" and "windsor building" from the same coords cluster.
- **LLM same-incident score**: An LLM scores how likely the report describes the same incident (0–1).
- **Time proximity**: Reports close in time score higher (same hour → 1.0, 6h → 0.8, 24h → 0.6, 7d → 0.3, else 0.1).
- **Geo proximity**: When device lat/lng is sent with the chunk, distance to the incident's device/location is used (same spot → 1.0, within 200m → 0.9, 500m → 0.7, 1km → 0.5, 2km → 0.3, else 0.1). So reports from the same building cluster even if one says "first floor" and another "windsor building".

Combined score = `0.35 * embedding_sim + 0.35 * llm_score + 0.15 * time_score + 0.15 * geo_score`. Send `device_lat` and `device_lng` with **POST /chunk** (dashboard requests location each time when auto-cluster is on). If the best score ≥ **CLUSTER_THRESHOLD** (default **0.65**), and optional semantic guards pass, the report is assigned to that incident; otherwise a new incident is created.

**Tuning so different incidents stay separate:** Set in `.env` (no code change):

- **CLUSTER_THRESHOLD** — Min combined score to merge (default `0.65`). Raise (e.g. `0.72`) to merge less often.
- **CLUSTER_WEIGHTS** — Comma-separated `embedding,llm,time,geo` (e.g. `0.4,0.4,0.1,0.1`) to rely more on semantic/LLM and less on time/place.
- **CLUSTER_MIN_EMBEDDING** — Optional: only merge if embedding similarity ≥ this (e.g. `0.4`). Avoids merging on time+geo alone.
- **CLUSTER_MIN_LLM** — Optional: only merge if LLM same-incident score ≥ this (e.g. `0.45`). Requires `OPENAI_API_KEY`.

## API

- **POST /chunk** — Ingest a transcript chunk. Body: `{ "text": "...", "incident_id": "incident-001", "auto_cluster": false }`. If `auto_cluster` is true, the report is assigned to the best-matching incident or a new one is created. Returns `cluster_score` and `cluster_new` when clustering is used.
- **GET /incident/{incident_id}** — Full incident state (summary + timeline).
- **GET /incident/{incident_id}/timeline** — Timeline only.
- **GET /incidents** — List incident IDs.
- **GET /health** — Status and extractor type (openai vs regex).

## Snowflake analytics (optional)

When `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, and `SNOWFLAKE_PASSWORD` (and optionally `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`) are set in `.env`, every **POST /chunk** update is written to Snowflake for analytics:

- **incident_snapshots** — One row per chunk (incident_id, last_updated, snapshot JSON, created_at). Use for incident state history and trends.
- **timeline_events** — One row per new timeline event (incident_id, event_time, claim_type, value, confidence, source_text, caller_id). Use for claim volume and audit analytics.
- **chunk_events** — One row per chunk (incident_id, chunk_preview, cluster_score, cluster_new, device_lat, device_lng, caller_id, ingested_at). Use for volume, clustering effectiveness, and device/location analytics.

Tables are created automatically (CREATE TABLE IF NOT EXISTS). Run SQL in Snowflake for incidents by type, by time, by location; clustering rates; timeline growth; etc. No-op if Snowflake env is not set; install `snowflake-connector-python` only if you use this.

**Analytics dashboard:** Open **http://localhost:8000/dashboard/analytics.html** for a full Snowflake-powered analytics page: KPIs, incidents over time (DATE_TRUNC), by incident type (GET_PATH on VARIANT), clustering stats, timeline volume, hour-over-hour trend (LAG), incident map (device_location from VARIANT), top locations, recent snapshots. Data is fetched from **GET /analytics**.

## Dashboard

- Submit transcript chunks; view current summary and timeline.
- **Live voice**: Start recording to add transcripts via speech. Every ~3 sentences are sent to the semantic engine; incidents update in real time. Each session (Start→Stop) = one caller. Requires the **Python voice server** (recommended) or the root Node `server.js` running on port 8080.
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
