"""
Optional Snowflake analytics sink.

When SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD (or SNOWFLAKE_PRIVATE_KEY_PATH),
SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA are set, every incident update
(from POST /chunk) is written to Snowflake for analytics:

- incident_snapshots: one row per update (incident_id, last_updated, snapshot JSON, created_at)
- timeline_events: one row per new timeline event (incident_id, event_time, claim_type, value, ...)
- chunk_events: one row per chunk (incident_id, chunk_preview, cluster_score, cluster_new, ...)

Run analytics in Snowflake: incidents by type, by time, by location; clustering effectiveness;
timeline volume; etc. No-op if Snowflake env is not set.
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger("incident_api.analytics.snowflake")

try:
    import snowflake.connector
except ImportError:
    snowflake = None  # type: ignore


def _snowflake_configured() -> bool:
    if snowflake is None:
        return False
    account = os.environ.get("SNOWFLAKE_ACCOUNT", "").strip()
    user = os.environ.get("SNOWFLAKE_USER", "").strip()
    password = os.environ.get("SNOWFLAKE_PASSWORD", "").strip()
    return bool(account and user and password)


def _get_conn():
    """Lazy connection; raises if not configured or connection fails."""
    if not _snowflake_configured():
        raise ValueError("Snowflake not configured (set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD)")
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"].strip(),
        user=os.environ["SNOWFLAKE_USER"].strip(),
        password=os.environ["SNOWFLAKE_PASSWORD"].strip(),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "").strip() or None,
        database=os.environ.get("SNOWFLAKE_DATABASE", "").strip() or None,
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "").strip() or None,
        role=os.environ.get("SNOWFLAKE_ROLE", "").strip() or None,
    )


def _ensure_tables(conn) -> None:
    incident_table = os.environ.get("SNOWFLAKE_INCIDENTS_TABLE", "incident_snapshots").strip()
    timeline_table = os.environ.get("SNOWFLAKE_TIMELINE_TABLE", "timeline_events").strip()
    chunk_table = os.environ.get("SNOWFLAKE_CHUNKS_TABLE", "chunk_events").strip()

    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {incident_table} (
            incident_id VARCHAR(128),
            last_updated VARCHAR(64),
            snapshot VARIANT,
            created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {timeline_table} (
            incident_id VARCHAR(128),
            event_time VARCHAR(64),
            claim_type VARCHAR(64),
            value VARCHAR(512),
            confidence FLOAT,
            source_text VARCHAR(1024),
            caller_id VARCHAR(128),
            created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {chunk_table} (
            incident_id VARCHAR(128),
            chunk_preview VARCHAR(1024),
            cluster_score FLOAT,
            cluster_new BOOLEAN,
            device_lat FLOAT,
            device_lng FLOAT,
            caller_id VARCHAR(128),
            ingested_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
    """)
    cur.close()


def sink_incident_after_chunk(
    incident_id: str,
    summary: dict,
    timeline_new_events: list[dict],
    chunk_meta: dict[str, Any],
) -> None:
    """
    Write one incident snapshot, new timeline events, and one chunk event to Snowflake.
    No-op if Snowflake env is not set. Logs and swallows errors so the API never fails.
    """
    if not _snowflake_configured():
        return
    try:
        conn = _get_conn()
        _ensure_tables(conn)
        incident_table = os.environ.get("SNOWFLAKE_INCIDENTS_TABLE", "incident_snapshots").strip()
        timeline_table = os.environ.get("SNOWFLAKE_TIMELINE_TABLE", "timeline_events").strip()
        chunk_table = os.environ.get("SNOWFLAKE_CHUNKS_TABLE", "chunk_events").strip()

        cur = conn.cursor()
        occurred_at = (chunk_meta.get("occurred_at") or "").strip() or None  # optional ISO timestamp for demo/backfill

        # Incident snapshot (one row per chunk = history of state over time)
        # Use INSERT...SELECT so PARSE_JSON works (VALUES clause can reject it)
        snapshot_json = json.dumps(summary)
        last_updated = summary.get("last_updated") or ""
        if occurred_at:
            cur.execute(
                f"INSERT INTO {incident_table} (incident_id, last_updated, snapshot, created_at) SELECT %s, %s, PARSE_JSON(%s), %s",
                (incident_id, last_updated, snapshot_json, occurred_at),
            )
        else:
            cur.execute(
                f"INSERT INTO {incident_table} (incident_id, last_updated, snapshot) SELECT %s, %s, PARSE_JSON(%s)",
                (incident_id, last_updated, snapshot_json),
            )

        # New timeline events
        for ev in timeline_new_events:
            if occurred_at:
                cur.execute(
                    f"""INSERT INTO {timeline_table}
                        (incident_id, event_time, claim_type, value, confidence, source_text, caller_id, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        incident_id,
                        ev.get("time", ""),
                        ev.get("claim_type", ""),
                        (ev.get("value") or "")[:512],
                        float(ev.get("confidence", 0)),
                        (ev.get("source_text") or "")[:1024],
                        ev.get("caller_id") or None,
                        occurred_at,
                    ),
                )
            else:
                cur.execute(
                    f"""INSERT INTO {timeline_table}
                        (incident_id, event_time, claim_type, value, confidence, source_text, caller_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        incident_id,
                        ev.get("time", ""),
                        ev.get("claim_type", ""),
                        (ev.get("value") or "")[:512],
                        float(ev.get("confidence", 0)),
                        (ev.get("source_text") or "")[:1024],
                        ev.get("caller_id") or None,
                    ),
                )

        # Chunk event (for volume, clustering, device analytics)
        if occurred_at:
            cur.execute(
                f"""INSERT INTO {chunk_table}
                    (incident_id, chunk_preview, cluster_score, cluster_new, device_lat, device_lng, caller_id, ingested_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    incident_id,
                    (chunk_meta.get("chunk_preview") or "")[:1024],
                    chunk_meta.get("cluster_score"),
                    chunk_meta.get("cluster_new"),
                    chunk_meta.get("device_lat"),
                    chunk_meta.get("device_lng"),
                    chunk_meta.get("caller_id") or None,
                    occurred_at,
                ),
            )
        else:
            cur.execute(
                f"""INSERT INTO {chunk_table}
                    (incident_id, chunk_preview, cluster_score, cluster_new, device_lat, device_lng, caller_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    incident_id,
                    (chunk_meta.get("chunk_preview") or "")[:1024],
                    chunk_meta.get("cluster_score"),
                    chunk_meta.get("cluster_new"),
                    chunk_meta.get("device_lat"),
                    chunk_meta.get("device_lng"),
                    chunk_meta.get("caller_id") or None,
                ),
            )

        cur.close()
        conn.close()
        logger.debug("snowflake sink ok incident_id=%s", incident_id)
    except Exception as e:
        logger.warning("snowflake sink failed: %s", e, exc_info=True)
