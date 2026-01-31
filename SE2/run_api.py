#!/usr/bin/env python3
"""
Run the Incident Summary API and dashboard.
Set OPENAI_API_KEY in environment (or .env) to use LLM extraction; otherwise regex is used.
"""
import os

# Load .env if python-dotenv is available (optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=port,
        reload=os.environ.get("RELOAD", "0") == "1",
    )
