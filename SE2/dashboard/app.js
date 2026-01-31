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
  const chunkResult = document.getElementById("chunk-result");
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

  var map = null;
  var mapMarkers = [];
  var deviceLat = null;
  var deviceLng = null;

  function initMap() {
    if (map || !mapEl || typeof L === "undefined") return;
    map = L.map("map").setView([51.5074, -0.1278], 15);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a>",
    }).addTo(map);
  }

  function updateMap(deviceLocation, locations) {
    initMap();
    if (!map) return;
    mapMarkers.forEach(function (m) {
      map.removeLayer(m);
    });
    mapMarkers = [];
    var all = [];
    if (deviceLocation && deviceLocation.lat != null && deviceLocation.lng != null && !isNaN(deviceLocation.lat) && !isNaN(deviceLocation.lng)) {
      var devMarker = L.marker([deviceLocation.lat, deviceLocation.lng], { icon: L.divIcon({ className: "device-marker", html: "<span class=\"device-dot\"></span>", iconSize: [20, 20] }) }).addTo(map);
      devMarker.bindPopup("<strong>Device</strong> " + (deviceLocation.confidence != null ? "(" + Number(deviceLocation.confidence).toFixed(2) + ")" : ""));
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
    updateMap(data.device_location, data.locations || []);
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
        }
        return null;
      }
      const data = await r.json();
      renderSummary(data);
      return data;
    } catch (e) {
      summaryLoading.textContent = "Failed to load: " + e.message;
      summaryContent.classList.add("hidden");
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
      var payload = { text: text, incident_id: incidentId };
      if (deviceLat != null && deviceLng != null) {
        payload.device_lat = deviceLat;
        payload.device_lng = deviceLng;
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
      var msg = "Added " + (data.claims_added || 0) + " claim(s). Summary updated.";
      if (data.claims_added === 0 && data.summary && data.summary.timeline_count > 0) {
        msg = "No new claims from this chunk (extractor may have fallen back). Summary unchanged.";
      }
      chunkResult.textContent = msg;
      chunkResult.className = "result-box success";
      renderSummary(data.summary);
      renderTimeline({ timeline: data.summary.timeline });
      chunkText.value = "";
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
        updateMap(d.device_location, d.locations || []);
      }
    });
  });

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
      updateMap(data.device_location, data.locations || []);
      chunkResult.textContent = "Demo locations added. Map updated.";
      chunkResult.className = "result-box success";
    } catch (err) {
      chunkResult.textContent = "Error: " + err.message;
      chunkResult.className = "result-box error";
    } finally {
      simulateLocationsBtn.disabled = false;
    }
  });

  checkHealth().then(function (ok) {
    if (ok) {
      const id = incidentIdInput.value.trim() || "incident-001";
      loadIncident(id).then(function (d) {
        if (d) {
          loadTimeline(id);
          updateMap(d.device_location, d.locations || []);
        }
      });
    }
  });
})();
