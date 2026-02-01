"""Analytics sink: optional Snowflake (and future backends) for incident/chunk analytics."""

from analytics.snowflake_sink import sink_incident_after_chunk

__all__ = ["sink_incident_after_chunk"]
