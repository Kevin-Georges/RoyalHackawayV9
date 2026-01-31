"""Embed report/incident summaries for semantic similarity (OpenAI text-embedding-3-small)."""

import logging
import os

logger = logging.getLogger("incident_api.clustering.embedding")

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

EMBEDDING_MODEL = "text-embedding-3-small"


def get_embedding(text: str) -> list[float] | None:
    """
    Return embedding vector for text using OpenAI. Returns None if no API key or error.
    """
    if not text or not text.strip():
        return None
    if OpenAI is None:
        logger.debug("openai not installed")
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.debug("no OPENAI_API_KEY")
        return None
    try:
        client = OpenAI(api_key=api_key)
        resp = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text.strip()[:8000],
        )
        return resp.data[0].embedding
    except Exception as e:
        logger.warning("embedding failed: %s", e)
        return None
