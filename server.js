const http = require("http");
const WebSocket = require("ws");
const { createClient, LiveTranscriptionEvents } = require("@deepgram/sdk");

const PORT = 8080;
const DEEPGRAM_API_KEY = "0a8ff19dc755f5b0000a72d097fb6552f7913bbf";

const deepgram = createClient(DEEPGRAM_API_KEY);

const server = http.createServer((req, res) => {
  res.writeHead(200);
  res.end("Speech server running");
});

const wss = new WebSocket.Server({ server });

wss.on("connection", (socket) => {
  console.log("Client connected");

  const dgConnection = deepgram.listen.live({
    model: "nova-2",
    language: "en-US",
    encoding: "linear16",
    sample_rate: 16000,
    channels: 1,
    interim_results: true,
    punctuate: true,
  });

  dgConnection.on(LiveTranscriptionEvents.Open, () => {
    console.log("Deepgram connected");
  });

  dgConnection.on(LiveTranscriptionEvents.Transcript, (data) => {
    const transcript = data.channel.alternatives[0].transcript;
    if (!transcript) return;

    socket.send(JSON.stringify({
      transcript,
      isFinal: data.is_final
    }));
  });

  dgConnection.on(LiveTranscriptionEvents.Error, (err) => {
    console.error("Deepgram error:", err);
  });

  socket.on("message", (msg) => {
    dgConnection.send(msg);
  });

  socket.on("close", () => {
    dgConnection.finish();
    console.log("Client disconnected");
  });
});

server.listen(PORT, () => console.log("Server listening on 8080"));
