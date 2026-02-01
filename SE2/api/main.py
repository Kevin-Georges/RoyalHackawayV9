"""
FastAPI backend: ingest transcript chunks, serve incident state and timeline.
Probabilities are updatable via merge logic; LLM extraction includes hallucination handling.
"""

import logging
import os
import re
from contextlib import asynccontextmanager

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


from dotenv import load_dotenv

from core.models import Incident
from core.engine import apply_claims, get_incident_state_dict, add_demo_locations, set_device_location
from extractors.regex_extractor import extract_claims as regex_extract_claims
from extractors.openai_extractor import extract_claims as openai_extract_claims
from extractors.judge import judge_support_scores
from clustering.assigner import (
    claims_to_summary_text,
    find_best_incident,
    new_incident_id,
)

load_dotenv(override=True)

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("incident_api")

# -----------------------------------------------------------------------------
# Store (in-memory; one incident per id for MVP)
# -----------------------------------------------------------------------------
incidents: dict[str, Incident] = {}
# Cache incident embeddings for clustering (invalidated when incident is updated)
incident_embedding_cache: dict[str, list[float]] = {}


def get_extractor():
    """Use OpenAI if key is set, else regex."""
    if os.environ.get("OPENAI_API_KEY"):
        return openai_extract_claims
    return regex_extract_claims


def _claim_id(claim_type: str, value: str) -> str:
    return f"{claim_type}::{str(value).strip().lower()}"


def _boost_repeated_mention(chunk_text: str, state_before: dict, claims: list[dict], judge_scores: dict[str, float]) -> dict[str, float]:
    """
    When the chunk explicitly mentions a claim value (e.g. 'fire' in text and incident_type is fire),
    boost support so confidence actually increases. Prevents confidence stuck at ~0.66.
    """
    chunk_lower = (chunk_text or "").lower()
    scores = dict(judge_scores)
    REPEAT_BOOST = 0.85  # minimum support when chunk clearly mentions the value

    def word_in_chunk(value: str) -> bool:
        if not value:
            return False
        v = str(value).lower().strip()
        return bool(re.search(r"\b" + re.escape(v) + r"\b", chunk_lower))

    for c in claims:
        ctype = c.get("claim_type")
        value = c.get("value")
        if not ctype or value is None:
            continue
        cid = _claim_id(ctype, value)
        if word_in_chunk(value):
            scores[cid] = max(scores.get(cid, 0.55), REPEAT_BOOST)
    for loc in (state_before.get("locations") or []):
        if isinstance(loc, dict):
            v = loc.get("value")
            if v and word_in_chunk(v):
                cid = _claim_id("location", v)
                scores[cid] = max(scores.get(cid, 0.55), REPEAT_BOOST)
    inc = state_before.get("incident_type")
    if isinstance(inc, dict):
        v = inc.get("value")
        if v and word_in_chunk(v):
            cid = _claim_id("incident_type", v)
            scores[cid] = max(scores.get(cid, 0.55), REPEAT_BOOST)
    pe = state_before.get("people_estimate")
    if isinstance(pe, dict):
        v = pe.get("value")
        if v is not None and (word_in_chunk(str(v)) or _people_value_in_chunk(chunk_lower, v)):
            cid = _claim_id("people_count", str(v))
            scores[cid] = max(scores.get(cid, 0.55), REPEAT_BOOST)
    for h in (state_before.get("hazards") or []):
        if isinstance(h, dict):
            v = h.get("value")
            if v and word_in_chunk(v):
                cid = _claim_id("hazard", v)
                scores[cid] = max(scores.get(cid, 0.55), REPEAT_BOOST)
    return scores


def _people_value_in_chunk(chunk_lower: str, value: str) -> bool:
    """Check if people count value (e.g. 2-3) is mentioned in chunk (e.g. 'two or three')."""
    v = str(value).lower()
    if v in chunk_lower:
        return True
    num_words = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "2-3": "two three", "3-4": "three four"}
    for k, words in num_words.items():
        if k in v and words in chunk_lower:
            return True
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # cleanup if needed
    pass


app = FastAPI(title="Incident Summary API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Request/response models
# -----------------------------------------------------------------------------
class ChunkRequest(BaseModel):
    text: str
    incident_id: str = "incident-001"
    device_lat: Optional[float] = None
    device_lng: Optional[float] = None
    auto_cluster: bool = False  # if True, assign to best-matching incident or create new (embedding + LLM + time)
    caller_id: Optional[str] = None  # unique per voice session (start→stop); groups multiple chunks from same caller
    caller_info: Optional[dict] = None  # optional: started_at, label, etc. for display


class ChunkResponse(BaseModel):
    incident_id: str
    summary: dict
    claims_added: int
    cluster_score: Optional[float] = None  # set when auto_cluster=True (combined match score)
    cluster_new: Optional[bool] = None  # True if this report created a new incident


# -----------------------------------------------------------------------------
# No-cache for dynamic API responses (avoid 304 for stale data)
# -----------------------------------------------------------------------------
NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.post("/chunk", response_model=ChunkResponse)
def process_chunk(body: ChunkRequest):
    """Ingest a transcript chunk; run extraction and update incident state. Probabilities are merged (updatable)."""
    text = (body.text or "").strip()
    text_preview = (text[:80] + "…") if len(text) > 80 else text
    cluster_score: Optional[float] = None
    cluster_new: Optional[bool] = None

    logger.info("chunk received incident_id=%s auto_cluster=%s text_len=%d preview=%r",
                body.incident_id, body.auto_cluster, len(text), text_preview or "(empty)")

    if not text:
        logger.warning("chunk rejected: empty text")
        return JSONResponse(
            status_code=400,
            content={"detail": "text is required and cannot be empty"},
            headers=NO_CACHE_HEADERS,
        )

    incident_id = body.incident_id or "incident-001"

    if body.auto_cluster:
        # Assign to best-matching incident or create new (embedding + LLM + time)
        from datetime import datetime
        extract_fn = get_extractor()
        quick_claims = extract_fn(body.text, context=None)
        if not quick_claims and extract_fn is openai_extract_claims:
            quick_claims = regex_extract_claims(body.text, context=None)
        report_summary = claims_to_summary_text(
            quick_claims,
            chunk_preview=text[:200],
            device_lat=float(body.device_lat) if body.device_lat is not None else None,
            device_lng=float(body.device_lng) if body.device_lng is not None else None,
        )
        report_time_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        incident_entries = [
            (iid, get_incident_state_dict(inc), inc.last_updated)
            for iid, inc in incidents.items()
        ]
        best_id, score = find_best_incident(
            report_summary,
            report_time_iso,
            incident_entries,
            report_lat=float(body.device_lat) if body.device_lat is not None else None,
            report_lng=float(body.device_lng) if body.device_lng is not None else None,
            embedding_cache=incident_embedding_cache,
        )
        cluster_score = round(score, 4)
        if best_id is not None:
            incident_id = best_id
            cluster_new = False
            logger.info("cluster assigned to incident_id=%s score=%s", incident_id, cluster_score)
        else:
            incident_id = new_incident_id()
            incidents[incident_id] = Incident(incident_id=incident_id)
            cluster_new = True
            logger.info("cluster created new incident_id=%s score=%s", incident_id, cluster_score)

    if incident_id not in incidents:
        incidents[incident_id] = Incident(incident_id=incident_id)
        logger.info("incident created incident_id=%s", incident_id)

    incident = incidents[incident_id]

    if body.device_lat is not None and body.device_lng is not None:
        set_device_location(incident, float(body.device_lat), float(body.device_lng))
        logger.info("device_location set lat=%s lng=%s", body.device_lat, body.device_lng)

    extract_fn = get_extractor()
    extractor_name = "openai" if extract_fn is openai_extract_claims else "regex"
    context = get_incident_state_dict(incident)
    claims = extract_fn(body.text, context=context)

    if not claims and extractor_name == "openai":
        logger.warning("openai returned 0 claims; falling back to regex")
        claims = regex_extract_claims(body.text, context=None)
        extractor_name = "regex (fallback)"

    logger.info("extraction extractor=%s claims_count=%d claim_types=%s", extractor_name, len(claims), [c.get("claim_type") for c in claims])

    # Inject caller_id/caller_info into claims for timeline grouping (voice sessions)
    caller_id = body.caller_id
    caller_info = body.caller_info
    if caller_id or caller_info:
        for c in claims:
            if caller_id:
                c["caller_id"] = caller_id
            if caller_info:
                c["caller_info"] = caller_info

    state_before = get_incident_state_dict(incident)
    judge_scores = judge_support_scores(state_before, body.text, claims) if claims else {}
    judge_scores = _boost_repeated_mention(body.text, state_before, claims, judge_scores)
    logger.info("judge returned %d support scores", len(judge_scores))

    n_before = len(incident.timeline)
    apply_claims(incident, claims, judge_scores=judge_scores)
    n_after = len(incident.timeline)
    claims_added = n_after - n_before
    summary = get_incident_state_dict(incident)

    # Invalidate embedding cache for this incident so next clustering uses updated summary
    incident_embedding_cache.pop(incident_id, None)

    logger.info("chunk applied incident_id=%s claims_added=%d timeline_len=%d", incident_id, claims_added, n_after)

    resp_content = ChunkResponse(
        incident_id=incident_id,
        summary=summary,
        claims_added=claims_added,
    ).model_dump()
    if cluster_score is not None:
        resp_content["cluster_score"] = cluster_score
    if cluster_new is not None:
        resp_content["cluster_new"] = cluster_new
    return JSONResponse(content=resp_content, headers=NO_CACHE_HEADERS)


@app.get("/incident/{incident_id}")
def get_incident_state(incident_id: str):
    """Return full incident state (summary + timeline)."""
    if incident_id not in incidents:
        logger.debug("get_incident_state not_found incident_id=%s", incident_id)
        raise HTTPException(status_code=404, detail="Incident not found")
    return JSONResponse(content=get_incident_state_dict(incidents[incident_id]), headers=NO_CACHE_HEADERS)


@app.get("/incident/{incident_id}/timeline")
def get_incident_timeline(incident_id: str):
    """Return timeline only."""
    if incident_id not in incidents:
        raise HTTPException(status_code=404, detail="Incident not found")
    return JSONResponse(
        content={"incident_id": incident_id, "timeline": [e.to_dict() for e in incidents[incident_id].timeline]},
        headers=NO_CACHE_HEADERS,
    )


# Predefined demo locations (lat, lng) for geo-spatial demo — e.g. building floors / areas
DEMO_LOCATIONS = [
    {"value": "Second floor", "lat": 51.5074, "lng": -0.1278},
    {"value": "First floor", "lat": 51.5080, "lng": -0.1285},
    {"value": "Third floor", "lat": 51.5068, "lng": -0.1272},
    {"value": "Basement", "lat": 51.5070, "lng": -0.1280},
    {"value": "Roof", "lat": 51.5076, "lng": -0.1275},
]


@app.post("/incident/{incident_id}/demo-locations")
def post_demo_locations(incident_id: str):
    """Add simulated locations with lat/lng for map demo. Creates incident if missing."""
    if incident_id not in incidents:
        incidents[incident_id] = Incident(incident_id=incident_id)
    add_demo_locations(incidents[incident_id], DEMO_LOCATIONS)
    return JSONResponse(
        content=get_incident_state_dict(incidents[incident_id]),
        headers=NO_CACHE_HEADERS,
    )


@app.get("/incidents")
def list_incidents(summaries: bool = False):
    """List known incidents. If summaries=true, returns full summary per incident for dashboard cards."""
    ids = list(incidents.keys())
    if not summaries:
        return JSONResponse(content={"incident_ids": ids}, headers=NO_CACHE_HEADERS)
    out = [
        {"incident_id": iid, "summary": get_incident_state_dict(incidents[iid])}
        for iid in ids
    ]
    return JSONResponse(content={"incident_ids": ids, "incidents": out}, headers=NO_CACHE_HEADERS)


@app.get("/health")
def health():
    return JSONResponse(
        content={"status": "ok", "extractor": "openai" if os.environ.get("OPENAI_API_KEY") else "regex"},
        headers=NO_CACHE_HEADERS,
    )


# -----------------------------------------------------------------------------
# Serve dashboard static files
# -----------------------------------------------------------------------------
dashboard_path = os.path.join(os.path.dirname(__file__), "..", "dashboard")
if os.path.isdir(dashboard_path):
    app.mount("/dashboard", StaticFiles(directory=dashboard_path, html=True), name="dashboard")
    # Redirect / to dashboard
    @app.get("/")
    def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/dashboard/")
