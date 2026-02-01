const http = require("http");
const path = require("path");
const WebSocket = require("ws");
const { createClient, LiveTranscriptionEvents } = require("@deepgram/sdk");
const crypto = require("crypto");
require("dotenv").config({ path: path.join(__dirname, ".env") });

const PORT = 8080;
const DEEPGRAM_API_KEY = process.env.DEEPGRAM_API_KEY || process.env.DEEPGRAM_API;
const SE2_API_URL = process.env.SE2_API_URL || "http://localhost:8000";

if (!DEEPGRAM_API_KEY) {
  console.error("Missing Deepgram API key. Set DEEPGRAM_API_KEY or DEEPGRAM_API in .env");
  process.exit(1);
}
const SENTENCES_PER_CHUNK = 3;

const deepgram = createClient(DEEPGRAM_API_KEY);

function generateCallerId() {
  return "caller-" + crypto.randomBytes(8).toString("hex");
}

function splitIntoSentences(text) {
  if (!text || !text.trim()) return [];
  return text
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

async function postChunkToSE2(text, callerId, callerInfo, deviceLat, deviceLng) {
  const payload = {
    text: text.trim(),
    auto_cluster: true,
    incident_id: "",
    caller_id: callerId,
    caller_info: callerInfo || undefined,
  };
  if (deviceLat != null && deviceLng != null) {
    payload.device_lat = deviceLat;
    payload.device_lng = deviceLng;
  }
  const res = await fetch(`${SE2_API_URL}/chunk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`SE2 chunk failed: ${res.status} ${err}`);
  }
  return res.json();
}

const server = http.createServer((req, res) => {
  res.writeHead(200);
  res.end("Speech server running");
});

const wss = new WebSocket.Server({ server });

wss.on("connection", (socket) => {
  const callerId = generateCallerId();
  const sessionStarted = new Date().toISOString();
  let sentenceBuffer = "";
  let deviceLat = null;
  let deviceLng = null;

  console.log("Client connected", callerId);

  const dgConnection = deepgram.listen.live({
    model: "nova-2",
    language: "en-US",
    encoding: "linear16",
    sample_rate: 16000,
    channels: 1,
    interim_results: true,
    punctuate: true,
    smart_format: true,
  });

  let dgReady = false;
  const audioBuffer = [];

  dgConnection.on(LiveTranscriptionEvents.Open, () => {
    console.log(`[TRACE] [${callerId}] Deepgram Open event fired`);
    dgReady = true;
    const n = audioBuffer.length;
    for (const chunk of audioBuffer) {
      dgConnection.send(chunk);
    }
    audioBuffer.length = 0;
    console.log(`[TRACE] [${callerId}] Deepgram ready. Flushed ${n} buffered audio chunk(s) to Deepgram.`);
    socket.send(
      JSON.stringify({
        type: "session",
        caller_id: callerId,
        started_at: sessionStarted,
      })
    );
    console.log(`[TRACE] [${callerId}] Sent session message to client.`);
  });

  dgConnection.on(LiveTranscriptionEvents.Transcript, async (data) => {
    const channel = data.channel ?? data.channels?.[0];
    const alt = Array.isArray(channel?.alternatives)
      ? channel.alternatives[0]
      : channel?.alternatives && typeof channel.alternatives === "object"
        ? Object.values(channel.alternatives)[0]
        : null;
    let transcript =
      (alt && (alt.transcript ?? alt.Transcript ?? "")) || "";
    if (!transcript && typeof data.transcript === "string")
      transcript = data.transcript;
    if (!transcript || !transcript.trim()) return;

    const isFinal = data.is_final ?? data.speech_final ?? false;
    console.log(`[TRACE] [${callerId}] transcript ${isFinal ? "FINAL" : "interim"}: "${transcript.slice(0, 80)}${transcript.length > 80 ? "…" : ""}"`);

    const payload = { type: "transcript", transcript, isFinal };
    socket.send(JSON.stringify(payload));
    console.log(`[TRACE] [${callerId}] Sent transcript to client. isFinal=${isFinal} len=${transcript.length}`);

    if (!isFinal) return;

    sentenceBuffer = (sentenceBuffer + " " + transcript).trim();
    const sentences = splitIntoSentences(sentenceBuffer);
    const chunksToSend = Math.floor(sentences.length / SENTENCES_PER_CHUNK);

    console.log(`[${callerId}] buffer: ${sentences.length} sentence(s), sending ${chunksToSend} chunk(s)`);

    for (let i = 0; i < chunksToSend; i++) {
      const chunkSentences = sentences.slice(
        i * SENTENCES_PER_CHUNK,
        (i + 1) * SENTENCES_PER_CHUNK
      );
      const chunkText = chunkSentences.join(" ");
      sentenceBuffer = sentences
        .slice((i + 1) * SENTENCES_PER_CHUNK)
        .join(" ")
        .trim();

      try {
        console.log(`[${callerId}] POST chunk to SE2 (${chunkText.length} chars): "${chunkText.slice(0, 60)}${chunkText.length > 60 ? "…" : ""}"`);
        const result = await postChunkToSE2(
          chunkText,
          callerId,
          { started_at: sessionStarted, label: `Caller ${callerId.slice(-6)}` },
          deviceLat,
          deviceLng
        );
        console.log(`[${callerId}] SE2 OK incident_id=${result.incident_id} claims_added=${result.claims_added}`);
        socket.send(
          JSON.stringify({
            type: "incident_update",
            incident_id: result.incident_id,
            summary: result.summary,
            claims_added: result.claims_added,
          })
        );
      } catch (err) {
        console.error(`[${callerId}] SE2 post failed:`, err.message || err);
        socket.send(
          JSON.stringify({
            type: "error",
            message: err.message || "Failed to process chunk",
          })
        );
      }
    }
  });

  dgConnection.on(LiveTranscriptionEvents.Error, (err) => {
    console.error(`[TRACE] [${callerId}] Deepgram Error:`, err?.message ?? err, err);
  });

  let audioChunkCount = 0;
  socket.on("message", (msg) => {
    const isBinary =
      Buffer.isBuffer(msg) ||
      (msg && typeof msg === "object" && msg.buffer instanceof ArrayBuffer);
    if (isBinary) {
      const len = msg.byteLength ?? msg.length;
      if (!len) {
        console.log(`[TRACE] [${callerId}] Ignored empty binary message`);
        return;
      }
      const buf = Buffer.isBuffer(msg) ? msg : Buffer.from(msg);
      audioChunkCount++;
      if (audioChunkCount <= 3 || audioChunkCount % 100 === 0) {
        console.log(`[TRACE] [${callerId}] Received binary audio chunk #${audioChunkCount} len=${len} dgReady=${dgReady}`);
      }
      if (dgReady) {
        dgConnection.send(buf);
      } else {
        audioBuffer.push(buf);
        if (audioBuffer.length <= 5 || audioBuffer.length % 50 === 0) {
          console.log(`[TRACE] [${callerId}] Buffering audio. buffer.length=${audioBuffer.length}`);
        }
      }
    } else {
      try {
        const str = msg.toString();
        const obj = JSON.parse(str);
        console.log(`[TRACE] [${callerId}] Received JSON message type=${obj.type || "(none)"}`, str.slice(0, 120));
        if (obj.type === "location" && obj.lat != null && obj.lng != null) {
          deviceLat = obj.lat;
          deviceLng = obj.lng;
          console.log(`[TRACE] [${callerId}] Device location set: ${deviceLat}, ${deviceLng}`);
        }
      } catch (e) {
        console.log(`[TRACE] [${callerId}] Message not JSON (or parse failed):`, typeof msg, (msg + "").slice(0, 80), e.message);
      }
    }
  });

  socket.on("close", async () => {
    console.log(`[TRACE] [${callerId}] Client socket closed. sentenceBuffer length=${sentenceBuffer.trim().length}`);
    dgConnection.requestClose();
    if (sentenceBuffer.trim()) {
      try {
        console.log(`[TRACE] [${callerId}] Flushing final chunk: "${sentenceBuffer.trim().slice(0, 80)}${sentenceBuffer.trim().length > 80 ? "…" : ""}"`);
        const result = await postChunkToSE2(
          sentenceBuffer.trim(),
          callerId,
          { started_at: sessionStarted, label: `Caller ${callerId.slice(-6)}`, ended: true },
          deviceLat,
          deviceLng
        );
        console.log(`[TRACE] [${callerId}] Flushed OK incident_id=${result.incident_id}`);
      } catch (err) {
        console.error("[TRACE] SE2 flush failed:", err);
      }
    }
    console.log("[TRACE] Client disconnected", callerId);
  });
});

server.listen(PORT, () =>
  console.log(`Server listening on ${PORT} (SE2: ${SE2_API_URL})`)
);
