An uncertainty-aware emergency incident intelligence system that converts live or written call transcripts into a continuously updating incident summary with explicit confidence scores instead of fixed facts.

The system ingests transcript chunks (typed or via live voice using Deepgram), extracts structured claims such as location, hazards, people involved, and incident type, and stores them as probabilistic evidence. Confidence increases as multiple pieces of supporting evidence appear, creating a more reliable understanding over time.

To reduce AI errors, the extraction layer can use OpenAI with strict hallucination controls, or fall back to deterministic regex extraction when no API key is provided. Every extracted claim is logged in an append-only timeline, providing a transparent audit trail of how the incident picture evolved.

The backend API merges information from multiple callers and can automatically cluster related reports into the same incident using a combination of semantic similarity, time proximity, and geographic distance.

A live dashboard displays:

Current incident summary with confidence levels

Timeline of extracted evidence

Real-time updates as new transcript chunks arrive

Optionally, incident updates are stored in Snowflake for analytics, trend monitoring, and post-incident review.
