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
        loadIncident(id).then(function (d) {
          if (d) {
            loadTimeline(id);
            updateMap(d.device_location, d.locations || []);
          }
        });
      });
    });
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
      var list = data.incidents || [];
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
    var byText = {};
    timeline.forEach(function (e) {
      var st = (e.source_text && e.source_text.trim()) || "";
      if (!st) return;
      if (!byText[st]) byText[st] = { time: e.time, source_text: st };
    });
    var list = Object.keys(byText).map(function (k) { return byText[k]; });
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
      return (
        "<div class=\"caller-card\">" +
        "<div class=\"caller-header\">Caller " + (i + 1) + (timeLabel ? " · " + escapeHtml(timeLabel) : "") + "</div>" +
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
      renderTimeline({ timeline: data.summary.timeline });
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
})();
