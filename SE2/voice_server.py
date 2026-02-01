"""
Real-time voice-to-text server for SE2 using Deepgram's API (Python only).

Accepts browser WebSocket connections (binary = linear16 16kHz audio, text = JSON e.g. location).
Streams audio to Deepgram live transcription, forwards transcripts to the client,
and POSTs every ~3 sentences to the SE2 /chunk API. Same protocol as the legacy Node server
so the dashboard and index.html work unchanged.

Requires: deepgram-sdk>=5.0.0 (or 3.x with different imports).

Run from SE2 dir:
  Set DEEPGRAM_API_KEY (and optionally VOICE_PORT=8080, SE2_API_URL=http://localhost:8000)
  python voice_server.py
  or: uvicorn voice_server:app --host 0.0.0.0 --port 8080
"""

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from queue import Empty, Queue

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

load_dotenv(override=True)

# Deepgram SDK 5.x (sync listen client)
try:
    from deepgram import DeepgramClient
    from deepgram.core.events import EventType
    from deepgram.extensions.types.sockets import ListenV1ResultsEvent
except ImportError:
    DeepgramClient = None
    EventType = None
    ListenV1ResultsEvent = None

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
LOG = logging.getLogger("voice_server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY") or os.environ.get("DEEPGRAM_API")
VOICE_PORT = int(os.environ.get("VOICE_PORT", "8080"))
SE2_API_URL = (os.environ.get("SE2_API_URL") or "http://localhost:8000").rstrip("/")
SENTENCES_PER_CHUNK = 3

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def generate_caller_id() -> str:
    return "caller-" + uuid.uuid4().hex[:16]


def split_into_sentences(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def post_chunk_to_se2(
    text: str,
    caller_id: str,
    caller_info: dict | None,
    device_lat: float | None,
    device_lng: float | None,
) -> dict:
    payload = {
        "text": text.strip(),
        "auto_cluster": True,
        "incident_id": "",
        "caller_id": caller_id,
        "caller_info": caller_info or None,
    }
    if device_lat is not None and device_lng is not None:
        payload["device_lat"] = device_lat
        payload["device_lng"] = device_lng
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{SE2_API_URL}/chunk",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    r.raise_for_status()
    return r.json()


def send_to_client_sync(loop: asyncio.AbstractEventLoop, ws: WebSocket, msg: dict) -> None:
    """Thread-safe: schedule sending a JSON message to the WebSocket from the main loop."""
    try:
        asyncio.run_coroutine_threadsafe(
            ws.send_text(json.dumps(msg)),
            loop,
        ).result(timeout=5.0)
    except Exception as e:
        LOG.warning("send_to_client_sync failed: %s", e)


# -----------------------------------------------------------------------------
# Deepgram worker thread (one per client) — SDK 5.x
# -----------------------------------------------------------------------------


def deepgram_worker(
    audio_queue: Queue,
    client_location: dict,
    loop: asyncio.AbstractEventLoop,
    ws: WebSocket,
    caller_id: str,
    session_started: str,
    closed: threading.Event,
) -> None:
    if not DeepgramClient or not EventType:
        LOG.error("Deepgram SDK not available. pip install deepgram-sdk")
        return
    if not DEEPGRAM_API_KEY:
        LOG.error("DEEPGRAM_API_KEY not set")
        return

    sentence_buffer = ""
    listener_thread: threading.Thread | None = None
    dg_socket_ref: list = []  # hold ref for finally close

    def on_open(_data):
        send_to_client_sync(
            loop,
            ws,
            {"type": "session", "caller_id": caller_id, "started_at": session_started},
        )
        LOG.info("[%s] Deepgram open", caller_id)

    def on_message(data):
        nonlocal sentence_buffer
        try:
            if not (ListenV1ResultsEvent and isinstance(data, ListenV1ResultsEvent)):
                if getattr(data, "type", None) != "Results":
                    return
            channel = getattr(data, "channel", None)
            if not channel or not getattr(channel, "alternatives", None):
                return
            alts = channel.alternatives
            transcript = (alts[0].transcript or "").strip() if alts else ""
            if not transcript:
                return
            is_final = getattr(data, "is_final", None) or getattr(data, "speech_final", False) or False
            LOG.info("[%s] transcript %s: %.80s", caller_id, "FINAL" if is_final else "interim", transcript)
            send_to_client_sync(loop, ws, {"type": "transcript", "transcript": transcript, "isFinal": is_final})
            if not is_final:
                return
            sentence_buffer = (sentence_buffer + " " + transcript).strip()
            sentences = split_into_sentences(sentence_buffer)
            chunks_to_send = len(sentences) // SENTENCES_PER_CHUNK
            for i in range(chunks_to_send):
                chunk_sentences = sentences[i * SENTENCES_PER_CHUNK : (i + 1) * SENTENCES_PER_CHUNK]
                chunk_text = " ".join(chunk_sentences)
                sentence_buffer = " ".join(sentences[(i + 1) * SENTENCES_PER_CHUNK :]).strip()
                try:
                    lat = client_location.get("lat")
                    lng = client_location.get("lng")
                    result_data = post_chunk_to_se2(
                        chunk_text,
                        caller_id,
                        {"started_at": session_started, "label": f"Caller {caller_id[-6:]}"},
                        float(lat) if lat is not None else None,
                        float(lng) if lng is not None else None,
                    )
                    send_to_client_sync(
                        loop,
                        ws,
                        {
                            "type": "incident_update",
                            "incident_id": result_data.get("incident_id", ""),
                            "summary": result_data.get("summary", {}),
                            "claims_added": result_data.get("claims_added", 0),
                        },
                    )
                except Exception as e:
                    LOG.exception("[%s] SE2 post failed: %s", caller_id, e)
                    send_to_client_sync(loop, ws, {"type": "error", "message": str(e)})
        except Exception as e:
            LOG.exception("[%s] on_message error: %s", caller_id, e)

    def on_error(err):
        LOG.error("[%s] Deepgram error: %s", caller_id, err)

    try:
        client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
        with client.listen.v1.connect(
            model="nova-2",
            language="en-US",
            encoding="linear16",
            sample_rate="16000",
            channels="1",
            interim_results="true",
            punctuate="true",
            smart_format="true",
        ) as dg_socket:
            dg_socket_ref.append(dg_socket)
            dg_socket.on(EventType.OPEN, on_open)
            dg_socket.on(EventType.MESSAGE, on_message)
            dg_socket.on(EventType.ERROR, on_error)

            def run_listener():
                dg_socket.start_listening()

            listener_thread = threading.Thread(target=run_listener, daemon=True)
            listener_thread.start()
            while not closed.is_set():
                try:
                    item = audio_queue.get(timeout=0.25)
                    if item is None:
                        break
                    dg_socket._send(item)
                except Empty:
                    continue
    except Exception as e:
        LOG.exception("[%s] Deepgram connection error: %s", caller_id, e)
    finally:
        try:
            if dg_socket_ref and hasattr(dg_socket_ref[0], "_websocket"):
                dg_socket_ref[0]._websocket.close()
        except Exception:
            pass
        if listener_thread:
            listener_thread.join(timeout=3.0)
        if sentence_buffer.strip():
            try:
                lat = client_location.get("lat")
                lng = client_location.get("lng")
                post_chunk_to_se2(
                    sentence_buffer.strip(),
                    caller_id,
                    {"started_at": session_started, "label": f"Caller {caller_id[-6:]}", "ended": True},
                    float(lat) if lat is not None else None,
                    float(lng) if lng is not None else None,
                )
            except Exception as e:
                LOG.exception("[%s] SE2 flush failed: %s", caller_id, e)
        LOG.info("[%s] worker exit", caller_id)


# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------

app = FastAPI(title="SE2 Voice Server", description="Real-time voice-to-text via Deepgram (Python)")


@app.get("/")
def root():
    return {"status": "ok", "service": "SE2 Voice Server", "deepgram": "live"}


@app.websocket("/")
async def voice_websocket(websocket: WebSocket):
    await websocket.accept()
    caller_id = generate_caller_id()
    session_started = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    audio_queue: Queue = Queue()
    client_location: dict = {}
    closed = threading.Event()
    loop = asyncio.get_event_loop()

    worker = threading.Thread(
        target=deepgram_worker,
        args=(audio_queue, client_location, loop, websocket, caller_id, session_started, closed),
        daemon=True,
    )
    worker.start()
    LOG.info("Client connected %s", caller_id)

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("type") != "websocket.receive":
                continue
            if "bytes" in msg:
                data = msg["bytes"]
                if data:
                    audio_queue.put(data)
            elif "text" in msg:
                try:
                    obj = json.loads(msg["text"])
                    if obj.get("type") == "location" and obj.get("lat") is not None and obj.get("lng") is not None:
                        client_location["lat"] = obj["lat"]
                        client_location["lng"] = obj["lng"]
                        LOG.info("[%s] device location set %s %s", caller_id, obj["lat"], obj["lng"])
                except json.JSONDecodeError:
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        closed.set()
        audio_queue.put(None)
        worker.join(timeout=8.0)
        LOG.info("Client disconnected %s", caller_id)


# -----------------------------------------------------------------------------
# Run standalone
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    if not DEEPGRAM_API_KEY:
        raise SystemExit("Set DEEPGRAM_API_KEY (or DEEPGRAM_API) in the environment or .env")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=VOICE_PORT)
