(function () {
  function apiUrl(path) {
    var base = "";
    if (window.location.pathname.indexOf("/dashboard") === 0) {
      base = window.location.pathname.replace(/\/dashboard.*/, "") || "";
    }
    return (base || "") + path;
  }

  var statusEl = document.getElementById("analytics-status");
  var charts = {};

  function setStatus(ok, msg) {
    if (!statusEl) return;
    statusEl.textContent = msg || (ok ? "Snowflake connected" : "Error");
    statusEl.className = "api-status " + (ok ? "ok" : "err");
  }

  function escapeHtml(s) {
    if (s == null) return "";
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function safeNum(v) {
    if (v == null || v === undefined) return "—";
    var n = Number(v);
    return isNaN(n) ? "—" : n;
  }

  function renderKpis(kpis) {
    var el = document.getElementById("kpi-cards");
    if (!el) return;
    kpis = kpis || {};
    var items = [
      { label: "Total snapshots", value: safeNum(kpis.total_snapshots) },
      { label: "Distinct incidents", value: safeNum(kpis.distinct_incidents) },
      { label: "Total chunks", value: safeNum(kpis.total_chunks) },
      { label: "Avg cluster score", value: kpis.avg_cluster_score != null && kpis.avg_cluster_score !== undefined ? Number(kpis.avg_cluster_score).toFixed(2) : "—" },
      { label: "New incidents created", value: safeNum(kpis.new_incidents_created) },
      { label: "Timeline events", value: safeNum(kpis.total_timeline_events) },
    ];
    el.innerHTML = items.map(function (i) {
      return '<div class="kpi-card"><div class="value">' + escapeHtml(String(i.value)) + '</div><div class="label">' + escapeHtml(i.label) + '</div></div>';
    }).join("");
  }

  function renderChart(id, type, labels, datasets) {
    var canvas = document.getElementById(id);
    if (!canvas) return;
    if (charts[id]) charts[id].destroy();
    var ctx = canvas.getContext("2d");
    charts[id] = new Chart(ctx, {
      type: type,
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: type === "bar" || type === "line" } },
        scales: type === "line" || type === "bar" ? {
          x: { ticks: { maxRotation: 45 } },
          y: { beginAtZero: true },
        } : {},
      },
    });
  }

  function renderOverTime(data) {
    if (!data || !data.length) return;
    var labels = data.map(function (r) {
      var t = r.time_bucket;
      if (t && t.indexOf("T") !== -1) return t.slice(0, 16).replace("T", " ");
      return t || "";
    });
    renderChart("chart-over-time", "line", labels, [
      { label: "Snapshot count", data: data.map(function (r) { return r.snapshot_count; }), borderColor: "#58a6ff", fill: false, tension: 0.2 },
      { label: "Incident count", data: data.map(function (r) { return r.incident_count; }), borderColor: "#3fb950", fill: false, tension: 0.2 },
    ]);
  }

  function renderByType(data) {
    if (!data || !data.length) return;
    var labels = data.map(function (r) { return escapeHtml(String(r.incident_type || "—")); });
    renderChart("chart-by-type", "bar", labels, [
      { label: "Incidents", data: data.map(function (r) { return r.cnt; }), backgroundColor: "rgba(88, 166, 255, 0.7)" },
    ]);
  }

  function renderHourlyTrend(data) {
    if (!data || !data.length) return;
    var labels = data.map(function (r) {
      var t = r.hour;
      if (t && t.indexOf("T") !== -1) return t.slice(0, 16).replace("T", " ");
      return t || "";
    });
    renderChart("chart-hourly-trend", "line", labels, [
      { label: "Incident count", data: data.map(function (r) { return r.incident_count; }), borderColor: "#58a6ff", fill: false, tension: 0.2 },
      { label: "Change vs prev hour", data: data.map(function (r) { return r.change != null ? r.change : 0; }), borderColor: "#d29922", fill: false, tension: 0.2 },
    ]);
  }

  function renderClustering(clustering) {
    var el = document.getElementById("clustering-stats");
    if (!el) return;
    clustering = clustering || {};
    if (clustering.total == null && clustering.avg_score == null) {
      el.innerHTML = "<p class='muted'>No chunk data yet.</p>";
      return;
    }
    el.innerHTML = [
      { label: "Avg cluster score", value: clustering.avg_score != null && clustering.avg_score !== undefined ? Number(clustering.avg_score).toFixed(3) : "—" },
      { label: "New incidents", value: safeNum(clustering.new_count) },
      { label: "Assigned to existing", value: safeNum(clustering.assigned_count) },
      { label: "Total chunks", value: safeNum(clustering.total) },
    ].map(function (r) {
      return '<div class="row"><span>' + escapeHtml(r.label) + '</span><span>' + escapeHtml(String(r.value)) + '</span></div>';
    }).join("");
  }

  function renderTimelineByType(data) {
    var el = document.getElementById("timeline-by-type");
    if (!el) return;
    if (!data || !data.length) {
      el.innerHTML = "<p class='muted'>No timeline events yet.</p>";
      return;
    }
    el.innerHTML = data.map(function (r) {
      return '<div class="row"><span>' + escapeHtml(r.claim_type || "") + '</span><span>' + escapeHtml(String(r.cnt)) + '</span></div>';
    }).join("");
  }

  var mapInstance = null;
  function renderMap(points) {
    var el = document.getElementById("map");
    if (!el) return;
    if (typeof L === "undefined") {
      el.innerHTML = "<p class='muted' style='padding:1rem'>Map unavailable (Leaflet not loaded).</p>";
      return;
    }
    if (mapInstance) {
      mapInstance.remove();
      mapInstance = null;
    }
    if (!points || !points.length) {
      el.innerHTML = "<p class='muted' style='padding:1rem'>No locations with lat/lng yet.</p>";
      return;
    }
    el.innerHTML = "";
    mapInstance = L.map("map").setView([51.5, -0.1], 6);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
    }).addTo(mapInstance);
    points.forEach(function (p) {
      var lat = p.lat, lng = p.lng;
      if (lat == null || lng == null) return;
      var label = (p.incident_type || "Incident") + " (" + (p.incident_id || "").slice(-8) + ")";
      L.marker([lat, lng]).addTo(mapInstance).bindPopup(escapeHtml(label));
    });
    if (points.length === 1) {
      mapInstance.setView([points[0].lat, points[0].lng], 14);
    } else if (points.length > 1) {
      var bounds = L.latLngBounds(points.map(function (p) { return [p.lat, p.lng]; }));
      mapInstance.fitBounds(bounds, { padding: [20, 20] });
    }
  }

  function renderTopLocations(data) {
    var el = document.getElementById("top-locations");
    if (!el) return;
    if (!data || !data.length) {
      el.innerHTML = "<p class='muted'>No locations yet.</p>";
      return;
    }
    el.innerHTML = data.map(function (r) {
      return '<div class="item"><span class="value">' + escapeHtml(r.location) + '</span> <span class="muted">' + escapeHtml(String(r.cnt)) + '</span></div>';
    }).join("");
  }

  function renderRecentSnapshots(data) {
    var el = document.getElementById("recent-snapshots");
    if (!el) return;
    if (!data || !data.length) {
      el.innerHTML = "<p class='muted'>No snapshots yet.</p>";
      return;
    }
    el.innerHTML = data.slice(0, 10).map(function (r) {
      var snap = r.snapshot;
      if (typeof snap === "string") {
        try { snap = JSON.parse(snap); } catch (e) { snap = null; }
      }
      var typeVal = snap && snap.incident_type && snap.incident_type.value ? snap.incident_type.value : "—";
      var timeStr = (snap && snap.last_updated) ? String(snap.last_updated).slice(0, 16) : (r.created_at ? String(r.created_at).slice(0, 16) : "");
      var preview = typeVal + (timeStr ? " · " + timeStr : "");
      return '<div class="item"><div class="incident-id">' + escapeHtml(r.incident_id || "") + '</div><div class="preview">' + escapeHtml(preview) + '</div></div>';
    }).join("");
  }

  function load() {
    setStatus(false, "Loading…");
    fetch(apiUrl("/analytics"), { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error(res.status === 503 ? "Snowflake not configured" : res.statusText);
        return res.json();
      })
      .then(function (data) {
        setStatus(true);
        renderKpis(data.kpis || {});
        renderOverTime(data.incidents_over_time || []);
        renderByType(data.by_incident_type || []);
        renderHourlyTrend(data.hourly_trend || []);
        renderClustering(data.clustering || {});
        renderTimelineByType(data.timeline_by_type || []);
        renderMap(data.map_points || []);
        renderTopLocations(data.top_locations || []);
        renderRecentSnapshots(data.recent_snapshots || []);
      })
      .catch(function (err) {
        setStatus(false, err.message || "Failed to load");
        document.getElementById("kpi-cards").innerHTML = "<p class='muted'>" + escapeHtml(err.message) + "</p>";
      });
  }

  load();
  setInterval(load, 60000);
})();
