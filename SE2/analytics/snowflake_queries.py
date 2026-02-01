"""
Snowflake-powered analytics queries for the SE2 analytics dashboard.

Uses Snowflake-specific features:
- VARIANT/JSON: GET_PATH, : dot notation, FLATTEN
- DATE_TRUNC for time bucketing
- QUALIFY + ROW_NUMBER() for "latest per incident"
- Window functions: LAG, LEAD for trends
- LISTAGG, ARRAY_AGG for aggregations
- Geospatial: ST_MAKEPOINT, TO_GEOGRAPHY (incident points for map)
- Result set → JSON-serializable dicts for API
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger("incident_api.analytics.snowflake_queries")

incident_table = lambda: os.environ.get("SNOWFLAKE_INCIDENTS_TABLE", "incident_snapshots").strip()
timeline_table = lambda: os.environ.get("SNOWFLAKE_TIMELINE_TABLE", "timeline_events").strip()
chunk_table = lambda: os.environ.get("SNOWFLAKE_CHUNKS_TABLE", "chunk_events").strip()


def _col_name(d) -> str:
    """Snowflake returns unquoted column names in uppercase; normalize to lowercase for API."""
    name = d[0] if d else ""
    return name.lower() if isinstance(name, str) else str(name)


def _cursor_to_list(cur) -> list[dict[str, Any]]:
    """Convert Snowflake cursor to list of dicts (column names = keys, lowercased)."""
    if cur.description is None:
        return []
    cols = [_col_name(d) for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _serialize(row: dict) -> dict:
    """Make row JSON-serializable (handle decimal, datetime, etc.)."""
    out = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, bool) and hasattr(v, "as_integer_ratio"):
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = str(v)
        elif isinstance(v, (dict, list)):
            out[k] = _serialize_value(v)
        else:
            out[k] = v
    return out


def _serialize_value(v):
    """Recurse for list/dict values."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "__float__") and not isinstance(v, bool):
        try:
            return float(v)
        except (TypeError, ValueError):
            return str(v)
    if isinstance(v, dict):
        return {k: _serialize_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_serialize_value(x) for x in v]
    return v


def get_kpis(conn) -> dict[str, Any]:
    """KPIs: total snapshots, distinct incidents, chunks, avg cluster score. Uses COUNT, COUNT(DISTINCT), AVG."""
    it, tt, ct = incident_table(), timeline_table(), chunk_table()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            (SELECT COUNT(*) FROM {it}) AS total_snapshots,
            (SELECT COUNT(DISTINCT incident_id) FROM {it}) AS distinct_incidents,
            (SELECT COUNT(*) FROM {ct}) AS total_chunks,
            (SELECT AVG(cluster_score) FROM {ct} WHERE cluster_score IS NOT NULL) AS avg_cluster_score,
            (SELECT COUNT(*) FROM {ct} WHERE cluster_new = TRUE) AS new_incidents_created,
            (SELECT COUNT(*) FROM {tt}) AS total_timeline_events
    """)
    row = cur.fetchone()
    cols = [_col_name(d) for d in cur.description] if cur.description else []
    cur.close()
    if not row:
        return {}
    return _serialize(dict(zip(cols, row)))


def get_incidents_over_time(conn, trunc: str = "HOUR") -> list[dict[str, Any]]:
    """Incidents over time: DATE_TRUNC bucketing. Returns buckets with count."""
    it = incident_table()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            DATE_TRUNC('{trunc}', created_at) AS time_bucket,
            COUNT(*) AS snapshot_count,
            COUNT(DISTINCT incident_id) AS incident_count
        FROM {it}
        WHERE created_at IS NOT NULL
        GROUP BY DATE_TRUNC('{trunc}', created_at)
        ORDER BY time_bucket
    """)
    rows = _cursor_to_list(cur)
    cur.close()
    return [_serialize(r) for r in rows]


def get_by_incident_type(conn) -> list[dict[str, Any]]:
    """Incidents by type: VARIANT extraction with GET_PATH. Latest snapshot per incident (QUALIFY + ROW_NUMBER)."""
    it = incident_table()
    cur = conn.cursor()
    cur.execute(f"""
        WITH latest AS (
            SELECT incident_id, snapshot, created_at,
                   ROW_NUMBER() OVER (PARTITION BY incident_id ORDER BY created_at DESC) AS rn
            FROM {it}
        )
        SELECT
            GET_PATH(snapshot, 'incident_type.value')::VARCHAR AS incident_type,
            COUNT(*) AS cnt
        FROM latest
        WHERE rn = 1 AND snapshot IS NOT NULL
        GROUP BY GET_PATH(snapshot, 'incident_type.value')
        ORDER BY cnt DESC
    """)
    rows = _cursor_to_list(cur)
    cur.close()
    return [_serialize(r) for r in rows]


def get_clustering_stats(conn) -> dict[str, Any]:
    """Clustering: avg score, new vs assigned counts. Uses AVG, SUM(CASE WHEN)."""
    ct = chunk_table()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            AVG(cluster_score) AS avg_score,
            SUM(CASE WHEN cluster_new = TRUE THEN 1 ELSE 0 END) AS new_count,
            SUM(CASE WHEN cluster_new = FALSE THEN 1 ELSE 0 END) AS assigned_count,
            COUNT(*) AS total
        FROM {ct}
        WHERE cluster_score IS NOT NULL
    """)
    row = cur.fetchone()
    cols = [_col_name(d) for d in cur.description] if cur.description else []
    cur.close()
    if not row:
        return {}
    return _serialize(dict(zip(cols, row)))


def get_timeline_volume_by_type(conn) -> list[dict[str, Any]]:
    """Timeline events by claim_type. Standard aggregation."""
    tt = timeline_table()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT claim_type, COUNT(*) AS cnt
        FROM {tt}
        GROUP BY claim_type
        ORDER BY cnt DESC
    """)
    rows = _cursor_to_list(cur)
    cur.close()
    return [_serialize(r) for r in rows]


def get_timeline_over_time(conn, trunc: str = "HOUR") -> list[dict[str, Any]]:
    """Timeline events over time: DATE_TRUNC."""
    tt = timeline_table()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            DATE_TRUNC('{trunc}', created_at) AS time_bucket,
            COUNT(*) AS event_count
        FROM {tt}
        WHERE created_at IS NOT NULL
        GROUP BY DATE_TRUNC('{trunc}', created_at)
        ORDER BY time_bucket
    """)
    rows = _cursor_to_list(cur)
    cur.close()
    return [_serialize(r) for r in rows]


def get_map_points(conn) -> list[dict[str, Any]]:
    """Geospatial: device_location lat/lng from VARIANT → points for map. Uses GET_PATH on snapshot."""
    it = incident_table()
    cur = conn.cursor()
    cur.execute(f"""
        WITH latest AS (
            SELECT incident_id, snapshot, created_at,
                   ROW_NUMBER() OVER (PARTITION BY incident_id ORDER BY created_at DESC) AS rn
            FROM {it}
        ),
        with_geo AS (
            SELECT incident_id,
                   GET_PATH(snapshot, 'device_location.lat')::FLOAT AS lat,
                   GET_PATH(snapshot, 'device_location.lng')::FLOAT AS lng,
                   GET_PATH(snapshot, 'incident_type.value')::VARCHAR AS incident_type
            FROM latest
            WHERE rn = 1
              AND GET_PATH(snapshot, 'device_location.lat') IS NOT NULL
              AND GET_PATH(snapshot, 'device_location.lng') IS NOT NULL
        )
        SELECT incident_id, lat, lng, incident_type FROM with_geo
    """)
    rows = _cursor_to_list(cur)
    cur.close()
    return [_serialize(r) for r in rows]


def get_recent_snapshots(conn, limit: int = 20) -> list[dict[str, Any]]:
    """Recent incident snapshots. QUALIFY not needed; ORDER BY created_at DESC LIMIT."""
    it = incident_table()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT incident_id, last_updated, snapshot, created_at
        FROM {it}
        ORDER BY created_at DESC
        LIMIT {int(limit)}
    """)
    rows = _cursor_to_list(cur)
    cur.close()
    out = []
    for r in rows:
        s = _serialize(r)
        if "snapshot" in s and s["snapshot"] is not None:
            snap = s["snapshot"]
            if isinstance(snap, str):
                try:
                    s["snapshot"] = json.loads(snap)
                except Exception:
                    s["snapshot"] = None
            elif hasattr(snap, "decode"):
                try:
                    s["snapshot"] = json.loads(snap.decode())
                except Exception:
                    s["snapshot"] = None
            if isinstance(s["snapshot"], (dict, list)):
                s["snapshot"] = _serialize_value(s["snapshot"])
        out.append(s)
    return out


def get_top_locations(conn, limit: int = 10) -> list[dict[str, Any]]:
    """Top locations from timeline_events (claim_type = 'location'). LISTAGG not needed; simple GROUP BY."""
    tt = timeline_table()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT value AS location, COUNT(*) AS cnt
        FROM {tt}
        WHERE claim_type = 'location' AND value IS NOT NULL AND TRIM(value) != ''
        GROUP BY value
        ORDER BY cnt DESC
        LIMIT {int(limit)}
    """)
    rows = _cursor_to_list(cur)
    cur.close()
    return [_serialize(r) for r in rows]


def get_hourly_trend(conn) -> list[dict[str, Any]]:
    """Hour-over-hour trend: LAG window function for previous hour count."""
    it = incident_table()
    cur = conn.cursor()
    cur.execute(f"""
        WITH hourly AS (
            SELECT
                DATE_TRUNC('hour', created_at) AS hour,
                COUNT(DISTINCT incident_id) AS incident_count
            FROM {it}
            WHERE created_at IS NOT NULL
            GROUP BY DATE_TRUNC('hour', created_at)
        )
        SELECT
            hour,
            incident_count,
            LAG(incident_count) OVER (ORDER BY hour) AS prev_hour_count,
            incident_count - COALESCE(LAG(incident_count) OVER (ORDER BY hour), 0) AS change
        FROM hourly
        ORDER BY hour
    """)
    rows = _cursor_to_list(cur)
    cur.close()
    return [_serialize(r) for r in rows]


def run_all_analytics(conn) -> dict[str, Any]:
    """Run all queries and return one payload for the analytics dashboard."""
    return {
        "kpis": get_kpis(conn),
        "incidents_over_time": get_incidents_over_time(conn, trunc="day"),
        "by_incident_type": get_by_incident_type(conn),
        "clustering": get_clustering_stats(conn),
        "timeline_by_type": get_timeline_volume_by_type(conn),
        "timeline_over_time": get_timeline_over_time(conn),
        "map_points": get_map_points(conn),
        "recent_snapshots": get_recent_snapshots(conn, limit=15),
        "top_locations": get_top_locations(conn, limit=10),
        "hourly_trend": get_hourly_trend(conn),
    }
