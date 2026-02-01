(function () {
  function apiUrl(path) {
    var base = "";
    if (window.location.pathname.indexOf("/dashboard") === 0) {
      base = window.location.pathname.replace(/\/dashboard.*/, "") || "";
    }
    return base + path;
  }

  const apiStatus = document.getElementById("api-status");
  const chunkForm = document.getElementById("chunk-form");
  const chunkText = document.getElementById("chunk-text");
  const incidentIdInput = document.getElementById("incident-id");
  const autoClusterCheckbox = document.getElementById("auto-cluster");
  const incidentIdRow = document.getElementById("incident-id-row");
  const chunkResult = document.getElementById("chunk-result");
  const incidentsLoading = document.getElementById("incidents-loading");
  const incidentsCards = document.getElementById("incidents-cards");
  const incidentsEmpty = document.getElementById("incidents-empty");
  const summaryLoading = document.getElementById("summary-loading");
  const summaryContent = document.getElementById("summary-content");
  const refreshSummaryBtn = document.getElementById("refresh-summary");
  const timelineLoading = document.getElementById("timeline-loading");
  const timelineContent = document.getElementById("timeline-content");
  const timelineList = document.getElementById("timeline-list");
  const simulateLocationsBtn = document.getElementById("simulate-locations");
  const mapEl = document.getElementById("map");
  const useMyLocationBtn = document.getElementById("use-my-location");
  const deviceLocationStatus = document.getElementById("device-location-status");
  const detailOverlay = document.getElementById("detail-overlay");
  const detailClose = document.getElementById("detail-close");
  const detailBackdrop = document.getElementById("detail-backdrop");
  const fabAddText = document.getElementById("fab-add-text");
  const ingestFloating = document.getElementById("ingest-floating");

  var map = null;
  var mapMarkers = [];
  var deviceLat = null;
  var deviceLng = null;
  var voiceSocket = null;
  var voiceCallerId = null;
  var voiceIncidentUpdatePending = null;
  var voiceIncidentUpdateLastRun = 0;
  var VOICE_INCIDENT_UPDATE_THROTTLE_MS = 400;

  function initMap() {
    if (map || !mapEl || typeof L === "undefined") return;
    map = L.map("map").setView([51.5074, -0.1278], 15);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a>",
    }).addTo(map);
  }

  function updateMap(deviceLocation, locations, incidentType) {
    initMap();
    if (!map) return;
    mapMarkers.forEach(function (m) {
      map.removeLayer(m);
    });
    mapMarkers = [];
    var all = [];
    var typeLabel = (incidentType && String(incidentType).trim()) ? (String(incidentType).charAt(0).toUpperCase() + String(incidentType).slice(1).toLowerCase()) : "Incident";
    if (deviceLocation && deviceLocation.lat != null && deviceLocation.lng != null && !isNaN(deviceLocation.lat) && !isNaN(deviceLocation.lng)) {
      var devMarker = L.marker([deviceLocation.lat, deviceLocation.lng]).addTo(map);
      devMarker.bindPopup("<strong class=\"map-popup-header\">" + escapeHtml(typeLabel) + "</strong>" + (deviceLocation.confidence != null ? " <span class=\"conf\">(" + Number(deviceLocation.confidence).toFixed(2) + ")</span>" : ""));
      mapMarkers.push(devMarker);
      all.push([deviceLocation.lat, deviceLocation.lng]);
    }
    locations = locations || [];
    var withCoords = locations.filter(function (loc) {
      return loc.lat != null && loc.lng != null && !isNaN(loc.lat) && !isNaN(loc.lng);
    });
    withCoords.forEach(function (loc) {
      var marker = L.marker([loc.lat, loc.lng]).addTo(map);
      marker.bindPopup("<strong>" + escapeHtml(loc.value) + "</strong> <span class=\"conf\">(" + (loc.confidence != null ? Number(loc.confidence).toFixed(2) : "—") + ")</span>");
      mapMarkers.push(marker);
      all.push([loc.lat, loc.lng]);
    });
    if (all.length === 1) {
      map.setView(all[0], 16);
    } else if (all.length > 1) {
      var bounds = L.latLngBounds(all);
      map.fitBounds(bounds, { padding: [30, 30] });
    }
  }

  function setApiStatus(ok, msg) {
    apiStatus.textContent = msg || (ok ? "API connected" : "API error");
    apiStatus.className = "api-status " + (ok ? "ok" : "err");
  }

  var fetchOpts = { cache: "no-store" };

  function toggleIncidentIdRow() {
    if (!incidentIdRow) return;
    if (autoClusterCheckbox && autoClusterCheckbox.checked) {
      incidentIdRow.classList.add("hidden");
    } else {
      incidentIdRow.classList.remove("hidden");
    }
  }

  function cardPreview(summary) {
    if (!summary) return "";
    var parts = [];
    if (summary.incident_type && summary.incident_type.value) {
      parts.push(summary.incident_type.value);
    }
    if (summary.locations && summary.locations.length) {
      parts.push(summary.locations.map(function (l) { return l.value; }).join(", "));
    }
    if (summary.last_updated) {
      parts.push(summary.last_updated.replace("T", " ").slice(0, 19));
    }
    return parts.join(" · ") || "—";
  }

  function renderIncidentCards(incidentsList) {
    if (!incidentsCards) return;
    if (!incidentsList || incidentsList.length === 0) {
      incidentsCards.classList.add("hidden");
      if (incidentsEmpty) {
        incidentsEmpty.classList.remove("hidden");
        incidentsEmpty.textContent = "No incidents yet. Submit a transcript with auto-cluster on.";
      }
      return;
    }
    incidentsCards.innerHTML = incidentsList.map(function (item) {
      var id = item.incident_id || "";
      var sum = item.summary || {};
      var preview = cardPreview(sum);
      var typeLabel = sum.incident_type && sum.incident_type.value ? escapeHtml(sum.incident_type.value) : "—";
      var timelineCount = sum.timeline_count != null ? sum.timeline_count : (sum.timeline ? sum.timeline.length : 0);
      return (
        "<div class=\"incident-card\" data-incident-id=\"" + escapeHtml(id) + "\" role=\"button\" tabindex=\"0\">" +
        "<div class=\"incident-card-id\">" + escapeHtml(id) + "</div>" +
        "<div class=\"incident-card-type\">" + typeLabel + "</div>" +
        "<div class=\"incident-card-preview\">" + escapeHtml(preview) + "</div>" +
        "<div class=\"incident-card-meta\">" + timelineCount + " event(s) · " + (sum.last_updated || "—") + "</div>" +
        "</div>"
      );
    }).join("");
    incidentsCards.classList.remove("hidden");
    if (incidentsEmpty) incidentsEmpty.classList.add("hidden");
    incidentsCards.querySelectorAll(".incident-card").forEach(function (card) {
      card.addEventListener("click", function () {
        var id = card.getAttribute("data-incident-id");
        if (!id) return;
        incidentsCards.querySelectorAll(".incident-card").forEach(function (c) { c.classList.remove("selected"); });
        card.classList.add("selected");
        if (incidentIdInput) incidentIdInput.value = id;
        if (detailOverlay) detailOverlay.setAttribute("aria-hidden", "false");
        loadIncident(id).then(function (d) {
          if (d) {
            loadTimeline(id);
            var t = d.incident_type && d.incident_type.value ? d.incident_type.value : null;
            updateMap(d.device_location, d.locations || [], t);
          }
        });
      });
    });
  }

  function closeDetailOverlay() {
    if (detailOverlay) detailOverlay.setAttribute("aria-hidden", "true");
  }
  if (detailClose) detailClose.addEventListener("click", closeDetailOverlay);
  if (detailBackdrop) detailBackdrop.addEventListener("click", closeDetailOverlay);
  if (fabAddText && ingestFloating) {
    var chunkFormEl = document.getElementById("chunk-form");
    fabAddText.addEventListener("click", function () {
      ingestFloating.classList.toggle("visible");
      if (chunkFormEl) chunkFormEl.classList.toggle("hidden", !ingestFloating.classList.contains("visible"));
    });
  }

  var INCIDENT_PRIORITY = { fire: 1, medical: 2, assault: 3, "gas leak": 4, accident: 5, collapse: 6, flood: 7, overdose: 8, "break-in": 9, missing: 10, suicide: 11 };
  function incidentSortKey(item) {
    var type = (item.summary && item.summary.incident_type && item.summary.incident_type.value) ? String(item.summary.incident_type.value).toLowerCase() : "";
    var priority = INCIDENT_PRIORITY[type] != null ? INCIDENT_PRIORITY[type] : 99;
    var updated = (item.summary && item.summary.last_updated) ? item.summary.last_updated : "";
    return priority + ":" + (updated ? "\x00" + updated : "");
  }

  async function loadIncidentsList() {
    if (!incidentsLoading || !incidentsCards) return;
    incidentsLoading.classList.remove("hidden");
    incidentsCards.classList.add("hidden");
    if (incidentsEmpty) incidentsEmpty.classList.add("hidden");
    try {
      var url = apiUrl("/incidents?summaries=true&_=" + Date.now());
      const r = await fetch(url, { cache: "no-store", headers: { Pragma: "no-cache" } });
      if (!r.ok) throw new Error("Failed to load incidents");
      const data = await r.json();
      var list = (data.incidents || []).slice().sort(function (a, b) {
        return incidentSortKey(a).localeCompare(incidentSortKey(b));
      });
      renderIncidentCards(list);
    } catch (e) {
      if (incidentsEmpty) {
        incidentsEmpty.textContent = "Could not load incidents: " + e.message;
        incidentsEmpty.classList.remove("hidden");
      }
    } finally {
      incidentsLoading.classList.add("hidden");
    }
  }

  async function checkHealth() {
    try {
      const r = await fetch(apiUrl("/health"), fetchOpts);
      const data = await r.json();
      setApiStatus(r.ok, "API connected (" + (data.extractor || "unknown") + ")");
      return r.ok;
    } catch (e) {
      setApiStatus(false, "API unreachable: " + e.message);
      return false;
    }
  }

  function withSubscript(value, confidence) {
    if (value == null || value === "") return "—";
    var c = confidence != null ? Number(confidence).toFixed(2) : "—";
    return "<span class=\"value\">" + escapeHtml(String(value)) + "</span> <span class=\"conf\">(" + c + ")</span>";
  }

  function renderSummary(data) {
    if (!data) return;
    document.getElementById("sum-incident-id").textContent = data.incident_id || "—";
    document.getElementById("sum-last-updated").textContent = data.last_updated || "—";
    var deviceEl = document.getElementById("sum-device-location");
    if (data.device_location && data.device_location.value) {
      deviceEl.innerHTML = withSubscript(data.device_location.value, data.device_location.confidence);
    } else {
      deviceEl.textContent = "—";
    }
    var locationsEl = document.getElementById("sum-locations");
    if (data.locations && data.locations.length) {
      locationsEl.innerHTML = data.locations.map(function (loc) {
        return "<span class=\"location-item\">" + withSubscript(loc.value, loc.confidence) + "</span>";
      }).join(", ");
    } else {
      locationsEl.textContent = "—";
    }
    document.getElementById("sum-incident-type").innerHTML = data.incident_type ? withSubscript(data.incident_type.value, data.incident_type.confidence) : "—";
    document.getElementById("sum-people").innerHTML = data.people_estimate ? withSubscript(data.people_estimate.value, data.people_estimate.confidence) : "—";
    var hazardsEl = document.getElementById("sum-hazards");
    if (data.hazards && data.hazards.length) {
      hazardsEl.innerHTML = data.hazards.map(function (h) {
        return "<span class=\"location-item\">" + withSubscript(h.value, h.confidence) + "</span>";
      }).join(", ");
    } else {
      hazardsEl.textContent = "—";
    }
    summaryLoading.classList.add("hidden");
    summaryContent.classList.remove("hidden");
    var incType = data.incident_type && data.incident_type.value ? data.incident_type.value : null;
    updateMap(data.device_location, data.locations || [], incType);
    renderTranscripts(data.timeline || []);
  }

  function uniqueCallersFromTimeline(timeline) {
    if (!timeline || !timeline.length) return [];
    var byCaller = {};
    timeline.forEach(function (e) {
      var st = (e.source_text && e.source_text.trim()) || "";
      if (!st) return;
      var cid = e.caller_id || ("legacy-" + st);
      if (!byCaller[cid]) {
        byCaller[cid] = {
          caller_id: e.caller_id,
          caller_info: e.caller_info,
          time: e.time,
          source_texts: [],
        };
      }
      if (byCaller[cid].source_texts.indexOf(st) === -1) {
        byCaller[cid].source_texts.push(st);
      }
      if (!byCaller[cid].time || (e.time && e.time < byCaller[cid].time)) {
        byCaller[cid].time = e.time;
      }
    });
    var list = Object.keys(byCaller).map(function (k) {
      var c = byCaller[k];
      return {
        caller_id: c.caller_id,
        caller_info: c.caller_info,
        time: c.time,
        source_text: c.source_texts.join(" "),
      };
    });
    list.sort(function (a, b) { return (a.time || "").localeCompare(b.time || ""); });
    return list;
  }

  function renderTranscripts(timeline) {
    var section = document.getElementById("transcripts-section");
    var listEl = document.getElementById("transcripts-list");
    if (!section || !listEl) return;
    var callers = uniqueCallersFromTimeline(timeline);
    if (callers.length === 0) {
      section.classList.add("hidden");
      return;
    }
    section.classList.remove("hidden");
    listEl.innerHTML = callers.map(function (c, i) {
      var timeLabel = (c.time || "").replace("T", " ").slice(0, 19);
      var header = "Caller " + (i + 1);
      if (c.caller_info && c.caller_info.label) header = escapeHtml(c.caller_info.label);
      else if (c.caller_id) header = "Caller " + escapeHtml(String(c.caller_id).slice(-8));
      if (timeLabel) header += " · " + escapeHtml(timeLabel);
      return (
        "<div class=\"caller-card\">" +
        "<div class=\"caller-header\">" + header + "</div>" +
        "<div class=\"caller-transcript\">" + escapeHtml(c.source_text) + "</div>" +
        "</div>"
      );
    }).join("");
  }

  function renderTimeline(data) {
    if (!data || !data.timeline || !data.timeline.length) {
      timelineLoading.textContent = "No timeline yet.";
      timelineContent.classList.add("hidden");
      return;
    }
    timelineLoading.classList.add("hidden");
    timelineContent.classList.remove("hidden");
    timelineList.innerHTML = data.timeline.map(function (e) {
      return (
        "<li>" +
        "<span class=\"event-time\">" + (e.time || "") + "</span>" +
        "<span class=\"event-type\">" + (e.claim_type || "") + "</span>" +
        "<span class=\"event-value\">" + (e.value || "") + "</span>" +
        "<span class=\"event-conf\">(" + (e.confidence != null ? Number(e.confidence).toFixed(2) : "") + ")</span>" +
        (e.source_text ? "<span class=\"event-source\">" + escapeHtml(e.source_text) + "</span>" : "") +
        "</li>"
      );
    }).join("");
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  async function loadIncident(id) {
    try {
      const r = await fetch(apiUrl("/incident/" + encodeURIComponent(id)), fetchOpts);
      if (!r.ok) {
        if (r.status === 404) {
          summaryLoading.textContent = "Incident not found. Process a chunk first.";
          summaryContent.classList.add("hidden");
          renderTranscripts([]);
        }
        return null;
      }
      const data = await r.json();
      renderSummary(data);
      return data;
    } catch (e) {
      summaryLoading.textContent = "Failed to load: " + e.message;
      summaryContent.classList.add("hidden");
      renderTranscripts([]);
      return null;
    }
  }

  async function loadTimeline(id) {
    try {
      const r = await fetch(apiUrl("/incident/" + encodeURIComponent(id) + "/timeline"), fetchOpts);
      if (!r.ok) return;
      const data = await r.json();
      renderTimeline(data);
    } catch (e) {
      timelineLoading.textContent = "Failed to load timeline.";
      timelineContent.classList.add("hidden");
    }
  }

  function getCurrentLocationAsync() {
    return new Promise(function (resolve) {
      if (!navigator.geolocation) {
        resolve(null);
        return;
      }
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude });
        },
        function () {
          resolve(null);
        },
        { timeout: 5000, maximumAge: 0, enableHighAccuracy: false }
      );
    });
  }

  chunkForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const text = chunkText.value.trim();
    const incidentId = incidentIdInput.value.trim() || "incident-001";
    if (!text) {
      chunkResult.textContent = "Enter some transcript text.";
      chunkResult.className = "result-box error";
      return;
    }
    chunkResult.textContent = "Processing…";
    chunkResult.className = "result-box";
    try {
      var autoCluster = !!(autoClusterCheckbox && autoClusterCheckbox.checked);
      var lat = deviceLat;
      var lng = deviceLng;
      if (autoCluster) {
        var fresh = await getCurrentLocationAsync();
        if (fresh) {
          lat = fresh.lat;
          lng = fresh.lng;
          deviceLat = lat;
          deviceLng = lng;
          if (deviceLocationStatus) deviceLocationStatus.textContent = "Location used for clustering.";
        }
      }
      var payload = { text: text, incident_id: autoCluster ? "" : incidentId, auto_cluster: autoCluster };
      if (lat != null && lng != null) {
        payload.device_lat = lat;
        payload.device_lng = lng;
      }
      const r = await fetch(apiUrl("/chunk"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await r.json();
      if (!r.ok) {
        chunkResult.textContent = data.detail || "Request failed.";
        chunkResult.className = "result-box error";
        return;
      }
      if (data.skipped) {
        chunkResult.textContent = "No incident content detected — chunk skipped. No incident created or updated.";
        chunkResult.className = "result-box";
        return;
      }
      var msg = "Added " + (data.claims_added || 0) + " claim(s). Summary updated.";
      if (data.claims_added === 0 && data.summary && data.summary.timeline_count > 0) {
        msg = "No new claims from this chunk (extractor may have fallen back). Summary unchanged.";
      }
      if (data.cluster_score != null) {
        msg += " Match score: " + Number(data.cluster_score).toFixed(2);
        if (data.cluster_new) msg += " (new incident)";
        else msg += " (assigned to existing)";
      }
      chunkResult.textContent = msg;
      chunkResult.className = "result-box success";
      if (data.incident_id && incidentIdInput) {
        incidentIdInput.value = data.incident_id;
      }
      renderSummary(data.summary);
      renderTimeline({ timeline: data.summary ? data.summary.timeline : [] });
      chunkText.value = "";
      loadIncidentsList().then(function () {
        var card = incidentsCards && incidentsCards.querySelector(".incident-card[data-incident-id=\"" + (data.incident_id || "") + "\"]");
        if (card) {
          incidentsCards.querySelectorAll(".incident-card").forEach(function (c) { c.classList.remove("selected"); });
          card.classList.add("selected");
        }
      }).catch(function (err) {
        console.error("Refresh incidents list failed:", err);
      });
    } catch (err) {
      chunkResult.textContent = "Error: " + err.message;
      chunkResult.className = "result-box error";
    }
  });

  if (useMyLocationBtn && deviceLocationStatus) {
    useMyLocationBtn.addEventListener("click", function () {
      deviceLocationStatus.textContent = "Getting location…";
      if (!navigator.geolocation) {
        deviceLocationStatus.textContent = "Geolocation not supported.";
        return;
      }
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          deviceLat = pos.coords.latitude;
          deviceLng = pos.coords.longitude;
          deviceLocationStatus.textContent = "Location set. Will send with next chunk.";
        },
        function () {
          deviceLocationStatus.textContent = "Location denied or unavailable.";
        }
      );
    });
  }

  refreshSummaryBtn.addEventListener("click", function () {
    const id = incidentIdInput.value.trim() || "incident-001";
    loadIncident(id).then(function (d) {
      if (d) {
        loadTimeline(id);
        var t = d.incident_type && d.incident_type.value ? d.incident_type.value : null;
        updateMap(d.device_location, d.locations || [], t);
      }
    });
  });

  var simulateIncidentsBtn = document.getElementById("simulate-incidents");
  function demoOccurredAt(i, total) {
    var now = new Date();
    var daysAgo = Math.floor((i * 1.3) % 10);
    var hourOffset = (i * 3) % 24;
    var d = new Date(now);
    d.setUTCDate(d.getUTCDate() - daysAgo);
    d.setUTCHours(d.getUTCHours() - hourOffset, d.getUTCMinutes(), d.getUTCSeconds(), 0);
    return d.toISOString().slice(0, 19) + "Z";
  }
  var DEMO_CHUNKS = [
    { text: "There's a fire on the third floor of the east wing.", device_lat: 51.5074, device_lng: -0.1278 },
    { text: "Smoke is spreading. We need evacuation.", device_lat: 51.5074, device_lng: -0.1278 },
    { text: "At least two people are trapped near the stairwell.", device_lat: 51.5074, device_lng: -0.1278 },
    { text: "Medical emergency in the main lobby. Someone collapsed.", device_lat: 51.515, device_lng: -0.142 },
    { text: "We think it might be a heart attack. Need ambulance.", device_lat: 51.515, device_lng: -0.142 },
    { text: "Fire in building B, second floor. Multiple people inside.", device_lat: 51.52, device_lng: -0.11 },
    { text: "Report of an assault near the car park. One person injured.", device_lat: 51.50, device_lng: -0.14 },
    { text: "Security is on the way. Suspect may have left the area.", device_lat: 51.50, device_lng: -0.14 },
    { text: "Smell of gas on the first floor. Possible gas leak.", device_lat: 51.508, device_lng: -0.13 },
    { text: "We've evacuated that corridor. Fire brigade en route.", device_lat: 51.508, device_lng: -0.13 },
    { text: "Update: fire on third floor is under control but building still evacuating.", device_lat: 51.5074, device_lng: -0.1278 },
    { text: "Ambulance has arrived at the lobby for the medical.", device_lat: 51.515, device_lng: -0.142 },
    { text: "Gas leak isolated. First floor clear.", device_lat: 51.508, device_lng: -0.13 },
  ];
  if (simulateIncidentsBtn) {
    simulateIncidentsBtn.addEventListener("click", async function () {
      simulateIncidentsBtn.disabled = true;
      if (chunkResult) chunkResult.textContent = "Simulating " + DEMO_CHUNKS.length + " chunks…";
      var ok = 0;
      for (var i = 0; i < DEMO_CHUNKS.length; i++) {
        var chunk = DEMO_CHUNKS[i];
        var payload = { text: chunk.text, auto_cluster: true, incident_id: "", caller_id: "demo-seed", caller_info: { label: "Demo seed" }, occurred_at: demoOccurredAt(i, DEMO_CHUNKS.length) };
        if (chunk.device_lat != null && chunk.device_lng != null) {
          payload.device_lat = chunk.device_lat;
          payload.device_lng = chunk.device_lng;
        }
        try {
          var r = await fetch(apiUrl("/chunk"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), cache: "no-store" });
          if (r.ok) ok++;
        } catch (e) {}
        await new Promise(function (res) { setTimeout(res, 250); });
      }
      if (chunkResult) chunkResult.textContent = "Simulated " + ok + "/" + DEMO_CHUNKS.length + " chunks. Refresh incidents or open Snowflake Analytics.";
      if (chunkResult) chunkResult.className = "result-box success";
      simulateIncidentsBtn.disabled = false;
      loadIncidentsList();
    });
  }

  simulateLocationsBtn.addEventListener("click", async function () {
    const id = incidentIdInput.value.trim() || "incident-001";
    simulateLocationsBtn.disabled = true;
    try {
      const r = await fetch(apiUrl("/incident/" + encodeURIComponent(id) + "/demo-locations"), {
        method: "POST",
        cache: "no-store",
      });
      if (!r.ok) {
        chunkResult.textContent = "Demo locations failed: " + (await r.json().catch(function () { return {}; })).detail || r.status;
        chunkResult.className = "result-box error";
        return;
      }
      const data = await r.json();
      renderSummary(data);
      renderTimeline({ timeline: data.timeline });
      var t = data.incident_type && data.incident_type.value ? data.incident_type.value : null;
      updateMap(data.device_location, data.locations || [], t);
      chunkResult.textContent = "Demo locations added. Map updated.";
      chunkResult.className = "result-box success";
    } catch (err) {
      chunkResult.textContent = "Error: " + err.message;
      chunkResult.className = "result-box error";
    } finally {
      simulateLocationsBtn.disabled = false;
    }
  });

  if (autoClusterCheckbox) {
    autoClusterCheckbox.addEventListener("change", toggleIncidentIdRow);
  }
  toggleIncidentIdRow();

  var refreshIncidentsBtn = document.getElementById("refresh-incidents");
  if (refreshIncidentsBtn) {
    refreshIncidentsBtn.addEventListener("click", function () {
      loadIncidentsList();
    });
  }

  checkHealth().then(function (ok) {
    if (ok) {
      loadIncidentsList();
    }
  });

  // Live voice panel (only on pages that have voice UI, e.g. call.html has its own)
  var voiceStartBtn = document.getElementById("voice-start");
  var voiceStopBtn = document.getElementById("voice-stop");
  var voiceStatus = document.getElementById("voice-status");
  var voiceCallerEl = document.getElementById("voice-caller");
  var voiceLiveEl = document.getElementById("voice-live");
  var voiceTranscriptEl = document.getElementById("voice-transcript");
  var hasVoicePanel = !!(voiceStartBtn && voiceStopBtn);

  var TARGET_SAMPLE_RATE = 16000;

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

  function appendVoiceTranscript(text, isFinal) {
    console.log("[TRACE dashboard] appendVoiceTranscript:", isFinal ? "FINAL" : "interim", "\"" + (text || "").slice(0, 60) + (text && text.length > 60 ? "…" : "") + "\"");
    if (isFinal) {
      if (voiceTranscriptEl && text) {
        var span = document.createElement("div");
        span.textContent = text;
        span.className = "transcript-final";
        voiceTranscriptEl.appendChild(span);
      }
      if (voiceLiveEl) voiceLiveEl.textContent = "";
    } else {
      if (voiceLiveEl) voiceLiveEl.textContent = text || "";
    }
  }

  if (hasVoicePanel) voiceStartBtn.addEventListener("click", async function () {
    if (voiceSocket && voiceSocket.readyState === 1) return;
    voiceStartBtn.disabled = true;
    voiceStopBtn.disabled = false;
    voiceStatus.textContent = "Connecting…";
    voiceTranscriptEl.innerHTML = "";
    if (voiceLiveEl) voiceLiveEl.textContent = "";
    voiceCallerEl.classList.add("hidden");

    try {
      // Match index.html order exactly: WebSocket first, then getUserMedia, then AudioContext + graph.
      var wsUrl = getVoiceWsUrl();
      console.log("[TRACE dashboard] WebSocket connecting to", wsUrl);
      voiceSocket = new WebSocket(wsUrl);
      voiceSocket._stream = null;
      voiceSocket._ctx = null;
      voiceSocket._input = null;
      voiceSocket._processor = null;

      voiceSocket.onopen = function () {
        console.log("[TRACE dashboard] WebSocket OPEN.");
        voiceStatus.textContent = "Recording…";
        navigator.geolocation.getCurrentPosition(
          function (pos) {
            console.log("[TRACE dashboard] Sending location", pos.coords.latitude, pos.coords.longitude);
            voiceSocket.send(JSON.stringify({ type: "location", lat: pos.coords.latitude, lng: pos.coords.longitude }));
          },
          function () {},
          { timeout: 5000 }
        );
      };

      console.log("[TRACE dashboard] getUserMedia…");
      var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      voiceSocket._stream = stream;

      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      var sourceRate = ctx.sampleRate;
      if (ctx.state === "suspended") {
        await ctx.resume();
      }
      console.log("[TRACE dashboard] AudioContext sampleRate=" + sourceRate + " state=" + ctx.state);
      voiceSocket._ctx = ctx;

      var input = ctx.createMediaStreamSource(stream);
      var processor = ctx.createScriptProcessor(4096, 1, 1);
      var sendCount = 0;
      processor.onaudioprocess = function (e) {
        var inputData = e.inputBuffer.getChannelData(0);
        var resampled = sourceRate === TARGET_SAMPLE_RATE
          ? inputData
          : resampleTo16k(inputData, sourceRate);
        var pcmData = convertFloat32ToInt16(resampled);
        if (voiceSocket && voiceSocket.readyState === 1) {
          voiceSocket.send(pcmData);
          sendCount++;
          if (sendCount <= 3 || sendCount % 100 === 0) {
            console.log("[TRACE dashboard] Sent audio chunk #" + sendCount + " bytes=" + (pcmData.byteLength || pcmData.length));
          }
        }
      };
      input.connect(processor);
      processor.connect(ctx.destination);
      voiceSocket._processor = processor;
      voiceSocket._input = input;

      voiceSocket.onmessage = function (ev) {
        var raw = (typeof ev.data === "string") ? ev.data : "(binary " + (ev.data.byteLength || ev.data.length) + " bytes)";
        console.log("[TRACE dashboard] Message received:", raw.slice(0, 100));
        var data;
        try {
          data = JSON.parse(ev.data);
        } catch (e) {
          console.log("[TRACE dashboard] Message parse failed:", e.message);
          return;
        }
        console.log("[TRACE dashboard] Parsed type=" + (data.type || "(none)") + " transcript=" + (data.transcript ? "\"" + data.transcript.slice(0, 40) + "…\"" : "—") + " isFinal=" + data.isFinal);
        if (data.type === "session") {
          voiceCallerId = data.caller_id;
          voiceCallerEl.textContent = "Caller: " + (data.caller_id || "").slice(-8);
          voiceCallerEl.classList.remove("hidden");
          console.log("[TRACE dashboard] Session set caller_id=" + (data.caller_id || "").slice(-8));
        } else if (data.type === "transcript") {
          appendVoiceTranscript(data.transcript, data.isFinal);
        } else if (data.type === "incident_update") {
          if (data.incident_id && incidentIdInput) incidentIdInput.value = data.incident_id;
          var payload = { incident_id: data.incident_id, summary: data.summary };
          function runIncidentUpdate() {
            voiceIncidentUpdateLastRun = Date.now();
            voiceIncidentUpdatePending = null;
            var d = payload;
            if (!d || !d.summary) return;
            renderSummary(d.summary);
            renderTimeline({ timeline: d.summary.timeline });
            var incType = d.summary.incident_type && d.summary.incident_type.value ? d.summary.incident_type.value : null;
            updateMap(d.summary.device_location, d.summary.locations || [], incType);
            loadIncidentsList().then(function () {
              var card = incidentsCards && incidentsCards.querySelector(".incident-card[data-incident-id=\"" + (d.incident_id || "") + "\"]");
              if (card) {
                incidentsCards.querySelectorAll(".incident-card").forEach(function (c) { c.classList.remove("selected"); });
                card.classList.add("selected");
              }
            });
          }
          var now = Date.now();
          if (voiceIncidentUpdatePending != null) {
            clearTimeout(voiceIncidentUpdatePending);
          }
          var delay = 0;
          var elapsed = now - voiceIncidentUpdateLastRun;
          if (elapsed < VOICE_INCIDENT_UPDATE_THROTTLE_MS) {
            delay = VOICE_INCIDENT_UPDATE_THROTTLE_MS - elapsed;
          }
          voiceIncidentUpdatePending = setTimeout(runIncidentUpdate, delay);
        } else if (data.type === "error") {
          console.log("[TRACE dashboard] Error from server:", data.message);
          voiceStatus.textContent = "Error: " + (data.message || "Unknown");
        } else {
          console.log("[TRACE dashboard] Unhandled message type:", data.type);
        }
      };

      voiceSocket.onerror = function (e) {
        console.log("[TRACE dashboard] WebSocket error", e);
      };

      voiceSocket.onclose = function () {
        console.log("[TRACE dashboard] WebSocket CLOSED");
        voiceStartBtn.disabled = false;
        voiceStopBtn.disabled = true;
        voiceStatus.textContent = "Stopped";
      };
    } catch (err) {
      console.log("[TRACE dashboard] Start failed:", err.message);
      voiceStatus.textContent = "Error: " + err.message;
      voiceStartBtn.disabled = false;
      voiceStopBtn.disabled = true;
    }
  });

  if (hasVoicePanel) voiceStopBtn.addEventListener("click", function () {
    if (!voiceSocket) return;
    if (voiceIncidentUpdatePending != null) {
      clearTimeout(voiceIncidentUpdatePending);
      voiceIncidentUpdatePending = null;
    }
    if (voiceSocket._processor) voiceSocket._processor.disconnect();
    if (voiceSocket._input) voiceSocket._input.disconnect();
    if (voiceSocket._stream) voiceSocket._stream.getTracks().forEach(function (t) {
      t.stop();
    });
    voiceSocket.close();
    if (voiceLiveEl) voiceLiveEl.textContent = "";
  });
})();
