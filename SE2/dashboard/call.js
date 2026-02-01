(function () {
  function apiUrl(path) {
    var base = "";
    if (window.location.pathname.indexOf("/dashboard") === 0) {
      base = window.location.pathname.replace(/\/dashboard.*/, "") || "";
    }
    return (base || "") + path;
  }

  var TARGET_SAMPLE_RATE = 16000;
  var captionsLine = document.getElementById("captions-line");
  var captionsInterim = document.getElementById("captions-interim");
  var actionCall = document.getElementById("action-call");
  var actionHangup = document.getElementById("action-hangup");
  var actionMessages = document.getElementById("action-messages");
  var messagesModal = document.getElementById("messages-modal");
  var messagesClose = document.getElementById("messages-close");
  var messagesCancel = document.getElementById("messages-cancel");
  var messagesText = document.getElementById("messages-text");
  var messagesSend = document.getElementById("messages-send");

  var voiceSocket = null;
  var lastFinalLines = [];
  var MAX_CAPTION_LINES = 2;

  function getVoiceWsUrl() {
    var host = window.location.hostname || "localhost";
    return "ws://" + host + ":8080";
  }

  function resampleTo16k(input, sourceRate) {
    var ratio = sourceRate / TARGET_SAMPLE_RATE;
    var outLen = Math.floor(input.length / ratio);
    var out = new Float32Array(outLen);
    for (var i = 0; i < outLen; i++) {
      var idx = i * ratio;
      var lo = Math.floor(idx);
      var hi = Math.min(lo + 1, input.length - 1);
      var frac = idx - lo;
      out[i] = input[lo] * (1 - frac) + input[hi] * frac;
    }
    return out;
  }

  function convertFloat32ToInt16(buffer) {
    var buf = new Int16Array(buffer.length);
    for (var i = 0; i < buffer.length; i++) {
      var s = Math.max(-1, Math.min(1, buffer[i]));
      buf[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return buf.buffer;
  }

  function setCaptions(finalText, interimText) {
    if (finalText) {
      lastFinalLines.push(finalText);
      if (lastFinalLines.length > MAX_CAPTION_LINES) lastFinalLines.shift();
    }
    if (captionsLine) {
      captionsLine.textContent = lastFinalLines.length ? lastFinalLines.join(" ") : "";
    }
    if (captionsInterim) {
      captionsInterim.textContent = interimText || "";
    }
  }

  function openMessagesModal() {
    if (messagesModal) {
      messagesModal.setAttribute("aria-hidden", "false");
      if (messagesText) messagesText.focus();
    }
  }

  function closeMessagesModal() {
    if (messagesModal) messagesModal.setAttribute("aria-hidden", "true");
  }

  if (actionMessages) {
    actionMessages.addEventListener("click", openMessagesModal);
  }
  if (messagesClose) messagesClose.addEventListener("click", closeMessagesModal);
  if (messagesCancel) messagesCancel.addEventListener("click", closeMessagesModal);
  messagesModal.addEventListener("click", function (e) {
    if (e.target === messagesModal) closeMessagesModal();
  });

  if (messagesSend && messagesText) {
    messagesSend.addEventListener("click", async function () {
      var text = messagesText.value.trim();
      if (!text) return;
      messagesSend.disabled = true;
      try {
        var payload = { text: text, auto_cluster: true, incident_id: "", caller_id: "call-page", caller_info: { label: "Call page" } };
        var r = await fetch(apiUrl("/chunk"), {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (r.ok) {
          messagesText.value = "";
          closeMessagesModal();
        }
      } catch (e) {}
      messagesSend.disabled = false;
    });
  }

  actionCall.addEventListener("click", async function () {
    if (voiceSocket && voiceSocket.readyState === 1) return;
    actionCall.disabled = true;
    actionHangup.disabled = false;
    lastFinalLines = [];
    setCaptions("", "");

    try {
      var wsUrl = getVoiceWsUrl();
      voiceSocket = new WebSocket(wsUrl);
      voiceSocket._stream = null;
      voiceSocket._ctx = null;
      voiceSocket._input = null;
      voiceSocket._processor = null;

      voiceSocket.onopen = function () {
        navigator.geolocation.getCurrentPosition(
          function (pos) {
            voiceSocket.send(JSON.stringify({ type: "location", lat: pos.coords.latitude, lng: pos.coords.longitude }));
          },
          function () {},
          { timeout: 5000 }
        );
      };

      var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      voiceSocket._stream = stream;

      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      var sourceRate = ctx.sampleRate;
      if (ctx.state === "suspended") await ctx.resume();
      voiceSocket._ctx = ctx;

      var input = ctx.createMediaStreamSource(stream);
      var processor = ctx.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = function (e) {
        var inputData = e.inputBuffer.getChannelData(0);
        var resampled = sourceRate === TARGET_SAMPLE_RATE ? inputData : resampleTo16k(inputData, sourceRate);
        var pcmData = convertFloat32ToInt16(resampled);
        if (voiceSocket && voiceSocket.readyState === 1) voiceSocket.send(pcmData);
      };
      input.connect(processor);
      processor.connect(ctx.destination);
      voiceSocket._processor = processor;
      voiceSocket._input = input;

      voiceSocket.onmessage = function (ev) {
        var data;
        try {
          data = JSON.parse(ev.data);
        } catch (e) { return; }
        if (data.type === "transcript") {
          if (data.isFinal) {
            setCaptions(data.transcript || "", "");
          } else {
            setCaptions(null, data.transcript || "");
          }
        }
      };

      voiceSocket.onclose = function () {
        actionCall.disabled = false;
        actionHangup.disabled = true;
      };
    } catch (err) {
      actionCall.disabled = false;
      actionHangup.disabled = true;
      setCaptions("", "Error: " + err.message);
    }
  });

  actionHangup.addEventListener("click", function () {
    if (!voiceSocket) return;
    if (voiceSocket._processor) voiceSocket._processor.disconnect();
    if (voiceSocket._input) voiceSocket._input.disconnect();
    if (voiceSocket._stream) voiceSocket._stream.getTracks().forEach(function (t) { t.stop(); });
    voiceSocket.close();
    setCaptions(null, "");
  });

  var now = new Date();
  var timeEl = document.getElementById("status-time");
  if (timeEl) timeEl.textContent = now.getHours() + ":" + String(now.getMinutes()).padStart(2, "0");
})();
