import {
  CATEGORY_COLORS,
  CATEGORY_RANK,
  cloneDefaultConfig,
  getDecisionAreaLabel,
  getDecisionRadiusNm
} from "./dss-config-schema.js";
import {
  buildPartnerState,
  escapeHtml,
  fallbackPartnerState,
  formatLocalTime,
  formatUtcTime,
  loadDssInputs
} from "./data-source-adapters.js";

const storageKey = "krno-partner-dss-admin-config";
const config = loadConfig();
const tooltip = document.getElementById("tooltip");
const metersPerNm = 1852;
let partnerState = fallbackPartnerState(config);
let map = null;
let radarLayer = null;
let lightningLayer = null;
let nowTimer = null;

const radarFrames = [
  { layer: "nexrad-n0q-900913-m20m", ageMinutes: 20 },
  { layer: "nexrad-n0q-900913-m15m", ageMinutes: 15 },
  { layer: "nexrad-n0q-900913-m10m", ageMinutes: 10 },
  { layer: "nexrad-n0q-900913-m05m", ageMinutes: 5 },
  { layer: "nexrad-n0q-900913", ageMinutes: 0 }
];
let radarFrame = radarFrames.length - 1;

function byId(id) {
  return document.getElementById(id);
}

function loadConfig() {
  try {
    const stored = localStorage.getItem(storageKey);
    return stored ? { ...cloneDefaultConfig(), ...JSON.parse(stored) } : cloneDefaultConfig();
  } catch {
    return cloneDefaultConfig();
  }
}

function setText(id, text) {
  const element = byId(id);
  if (element) element.textContent = text ?? "--";
}

function riskColor(category) {
  return CATEGORY_COLORS[category] || CATEGORY_COLORS.None;
}

function rank(category) {
  return CATEGORY_RANK[category] ?? 0;
}

function confidenceColor(label) {
  if (label === "High") return "#2fbf71";
  if (label === "Low") return "#d25555";
  return "#d7bc4a";
}

function updateHeaderClock() {
  const now = new Date();
  setText("updated-time", `Updated ${formatLocalTime(now.toISOString(), { zone: true })}`);
  setText("utc-time", formatUtcTime(now.toISOString()));
}

function renderHeader(state) {
  const profile = state.config.partnerProfile;
  setText("product-title", profile.productTitle);
  setText("product-subtitle", getDecisionAreaLabel(state.config));
  setText("obs-badge", state.obs.statusLabel);
  byId("obs-badge")?.classList.toggle("good", state.obs.statusLabel === "Obs Fresh");
  byId("obs-badge")?.classList.toggle("warn", state.obs.statusLabel === "Obs Aging");
  byId("obs-badge")?.classList.toggle("bad", state.obs.statusLabel === "Obs Stale");
  setText("model-badge", state.model.cycle || "NBM");
  updateHeaderClock();
}

function renderBanner(state) {
  const primary = state.primary;
  if (!primary) return;
  const category = primary.riskCategory;
  byId("banner-accent").style.background = riskColor(category);
  setText("primary-headline", primary.headline);
  setText(
    "primary-detail",
    `${category} • ${primary.displayName} • Peak ${primary.window} • ${decisionAreaShort(state.config)}`
  );
  setText("primary-action", primary.action);
  setText("primary-confidence", `Confidence ${primary.confidence.label}`);
  byId("primary-confidence").style.borderColor = confidenceColor(primary.confidence.label);
  attachTooltip(byId("primary-banner"), {
    title: primary.headline,
    subtitle: `${category} ${primary.displayName}`,
    risk: category,
    lines: [
      ["Peak Window", primary.window],
      ["Decision Trigger", primary.trigger],
      ["Action", primary.action],
      ["Confidence", primary.confidence.label],
      ["Primary Driver", primary.confidence.drivers?.[0] || "Forecast category support"]
    ]
  });
}

function decisionAreaShort(config) {
  if (config.forecastGeometry?.mode === "point") return "Point Forecast";
  const radius = config.forecastGeometry?.radius;
  if (radius) return `${radius.radiusValue} ${radius.radiusUnits} Decision Area`;
  const fallbackRadius = getDecisionRadiusNm(config);
  return fallbackRadius ? `${fallbackRadius} NM Decision Area` : "Decision Area";
}

function renderSinceLastUpdate(state) {
  setText("since-update", state.sinceLastUpdate);
}

function renderKeyMessages(state) {
  state.keyMessages.slice(0, 3).forEach((message, index) => {
    const number = index + 1;
    setText(`msg${number}-title`, message.title);
    setText(`msg${number}-text`, message.text);
  });
}

function renderLegend() {
  const legend = byId("legend");
  legend.innerHTML = "";
  ["Little to None", "Minor", "Moderate", "Major", "Extreme"].forEach(category => {
    const item = document.createElement("div");
    item.className = "legend-item";
    item.innerHTML = `<span class="swatch" style="background:${riskColor(category)}"></span>${escapeHtml(category)}`;
    legend.appendChild(item);
  });
}

function timeLabelFor(iso, index) {
  if (index % 2 !== 0) return "";
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return "";
  const day = date.toLocaleDateString("en-US", { timeZone: config.partnerProfile.timezone, weekday: "short" });
  const hour = date.toLocaleTimeString("en-US", { timeZone: config.partnerProfile.timezone, hour: "numeric", hour12: true }).replace(" ", "");
  return index % 8 === 0 ? `${day} ${hour}` : hour;
}

function cellText(category) {
  if (category === "Minor") return "MINOR";
  if (category === "Moderate") return "MOD";
  if (category === "Major") return "MAJOR";
  if (category === "Extreme") return "EXTREME";
  return "";
}

function renderTimeline(state) {
  const timeline = byId("timeline");
  timeline.innerHTML = "";
  const rows = state.operationalTimeline.rows;
  const blocks = rows[0]?.cells || state.summaries[0]?.cells || [];

  const corner = document.createElement("div");
  corner.className = "time-label";
  corner.textContent = "";
  timeline.appendChild(corner);

  blocks.forEach((cell, index) => {
    const label = document.createElement("div");
    label.className = "time-label";
    label.textContent = timeLabelFor(cell.validStartUtc, index);
    timeline.appendChild(label);
  });

  rows.forEach(row => {
    const rowLabel = document.createElement("div");
    rowLabel.className = "row-label";
    rowLabel.textContent = row.label;
    timeline.appendChild(rowLabel);

    row.cells.forEach(cell => {
      const category = cell.riskCategory;
      const item = document.createElement("button");
      item.type = "button";
      item.className = `cell ${rank(category) <= 1 ? "quiet" : ""}`;
      item.style.background = riskColor(category);
      item.textContent = cellText(category);
      item.setAttribute("aria-label", `${row.label}, ${category}, ${row.summary.window}`);
      attachTooltip(item, {
        title: row.label,
        subtitle: `${category} • ${row.summary.window}`,
        risk: category,
        lines: [
          ["Action", row.summary.action],
          ["Trigger", row.summary.trigger],
          ["Key Signal", cell.metric || row.summary.metric],
          ["Confidence", row.summary.confidence.label],
          ["Window", `${formatLocalTime(cell.validStartUtc)}-${formatLocalTime(cell.validEndUtc)}`]
        ]
      });
      timeline.appendChild(item);
    });
  });

  const noSignal = byId("no-signal");
  noSignal.textContent = state.operationalTimeline.noSignal.length
    ? `No Operational Signal: ${state.operationalTimeline.noSignal.join(" | ")}`
    : "";
  updateNowMarker(blocks);
  if (nowTimer) clearInterval(nowTimer);
  nowTimer = setInterval(() => updateNowMarker(blocks), 60000);
}

function updateNowMarker(blocks) {
  byId("timeline")?.querySelector(".now-line")?.remove();
  if (!Array.isArray(blocks) || !blocks.length) return;
  const start = new Date(blocks[0].validStartUtc).getTime();
  const end = new Date(blocks[blocks.length - 1].validEndUtc).getTime();
  const now = Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end) || now < start || now > end) return;
  const timeline = byId("timeline");
  const leftColumn = 178;
  const gridWidth = timeline.clientWidth - 24 - leftColumn;
  const fraction = Math.max(0, Math.min(1, (now - start) / (end - start)));
  const line = document.createElement("div");
  line.className = "now-line";
  line.style.left = `${12 + leftColumn + gridWidth * fraction}px`;
  timeline.appendChild(line);
}

function renderObs(state) {
  const obs = state.obs;
  setText("obs-wind", obs.windText);
  setText("obs-vis", obs.visibilityText);
  setText("obs-sky", obs.skyText);
  setText("obs-tempdp", obs.tempDpText);
  setText("obs-wx", obs.weatherText);
  setText("obs-rain", obs.rainText);
  setText("obs-time", obs.stale ? `STALE - ${obs.localTime}` : obs.localTime);
  const stale = byId("obs-stale");
  stale.hidden = !obs.stale;
  if (obs.stale) stale.textContent = `OBS STALE — Last update ${obs.localTime}`;

  const arrow = byId("wind-arrow");
  const calm = byId("wind-calm");
  const speed = Number(obs.windSpeedMph);
  if (!Number.isFinite(speed) || speed <= 0) {
    arrow.hidden = true;
    calm.hidden = false;
  } else {
    calm.hidden = true;
    arrow.hidden = false;
    const direction = Number(obs.windDirection);
    arrow.style.transform = Number.isFinite(direction)
      ? `rotate(${direction + 90}deg)`
      : "rotate(90deg)";
  }

  attachTooltip(byId("wind-rose"), {
    title: "Current Airfield Wind",
    subtitle: obs.windText,
    risk: "Little to None",
    lines: [
      ["Source", obs.source],
      ["Observed", `${obs.localTime} / ${obs.utcTime}`],
      ["Visibility", obs.visibilityText],
      ["Sky", obs.skyText]
    ]
  });
}

function destinationPoint(lat, lon, distanceNm, bearingDegrees) {
  const radiusMeters = 6371008.8;
  const distance = Number(distanceNm) * metersPerNm;
  const bearing = Number(bearingDegrees) * Math.PI / 180;
  const phi1 = Number(lat) * Math.PI / 180;
  const lambda1 = Number(lon) * Math.PI / 180;
  const angular = distance / radiusMeters;
  const phi2 = Math.asin(Math.sin(phi1) * Math.cos(angular) + Math.cos(phi1) * Math.sin(angular) * Math.cos(bearing));
  const lambda2 = lambda1 + Math.atan2(
    Math.sin(bearing) * Math.sin(angular) * Math.cos(phi1),
    Math.cos(angular) - Math.sin(phi1) * Math.sin(phi2)
  );
  return {
    lat: phi2 * 180 / Math.PI,
    lon: ((lambda2 * 180 / Math.PI + 540) % 360) - 180
  };
}

function initMap(state) {
  if (!window.L || map) return;
  const profile = state.config.partnerProfile;
  const centerLat = state.config.forecastGeometry?.radius?.centerLat ?? profile.latitude;
  const centerLon = state.config.forecastGeometry?.radius?.centerLon ?? profile.longitude;
  map = L.map("radar-map", {
    center: [centerLat, centerLon],
    zoom: 9,
    zoomControl: false,
    attributionControl: false,
    dragging: false,
    scrollWheelZoom: false,
    doubleClickZoom: false,
    boxZoom: false,
    keyboard: false,
    tap: false
  });
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 16
  }).addTo(map);
  const decisionRadius = getDecisionRadiusNm(state.config) || profile.decisionRadiusNm || 20;
  (state.config.mapLayers.ringsNm || [10, 20]).forEach(limit => {
    L.circle([centerLat, centerLon], {
      radius: limit * metersPerNm,
      color: limit === decisionRadius ? "rgba(255,211,92,0.85)" : "rgba(234,244,255,0.62)",
      weight: limit === decisionRadius ? 2 : 1,
      fill: false,
      interactive: false
    }).addTo(map);
  });
  L.circleMarker([centerLat, centerLon], {
    radius: 5,
    color: "#fff",
    fillColor: "#4fc3ff",
    fillOpacity: 0.95,
    weight: 2,
    interactive: false
  }).addTo(map);
  (state.config.mapLayers.cities || []).forEach(city => {
    L.marker([city.latitude, city.longitude], {
      interactive: false,
      icon: L.divIcon({
        className: "city-label",
        html: escapeHtml(city.name),
        iconSize: null
      })
    }).addTo(map);
  });
  map.fitBounds([[39.08, -120.24], [39.68, -118.74]], { padding: [8, 8], animate: false });
  setRadarFrame();
}

function setRadarFrame() {
  if (!map) return;
  if (radarLayer) map.removeLayer(radarLayer);
  const frame = radarFrames[radarFrame];
  radarLayer = L.tileLayer.wms("https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q.cgi", {
    layers: frame.layer,
    format: "image/png",
    transparent: true,
    opacity: 0.74,
    version: "1.1.1"
  }).addTo(map);
}

function updateLightningMarker(state) {
  if (!map) return;
  if (lightningLayer) {
    map.removeLayer(lightningLayer);
    lightningLayer = null;
  }
  const strike = state.lightning.nearest;
  if (!strike) return;
  let point = null;
  if (Number.isFinite(Number(strike.lat)) && Number.isFinite(Number(strike.lon))) {
    point = { lat: Number(strike.lat), lon: Number(strike.lon) };
  } else if (Number.isFinite(Number(strike.distance_nm)) && Number.isFinite(Number(strike.bearing_degrees))) {
    point = destinationPoint(state.config.partnerProfile.latitude, state.config.partnerProfile.longitude, strike.distance_nm, strike.bearing_degrees);
  }
  if (!point) return;
  lightningLayer = L.marker([point.lat, point.lon], {
    interactive: false,
    icon: L.divIcon({ className: "lightning-dot", html: "", iconSize: [14, 14] })
  }).addTo(map);
}

function renderLightning(state) {
  initMap(state);
  updateLightningMarker(state);
  setText("ring20", state.lightning.ring20Status);
  setText("ring10", state.lightning.ring10Status);
  setText("closest-strike", state.lightning.closestText);
  setText("strike-age", state.lightning.strikeAgeText);
  setText("last-scan", state.lightning.localScanTime);
  setText("trigger-action", state.lightning.action);
  attachTooltip(byId("trigger-action"), {
    title: "Lightning Decision Trigger",
    subtitle: state.lightning.ring20Status,
    risk: state.lightning.riskCategory,
    lines: [
      ["20 NM Ring", state.lightning.ring20Status],
      ["10 NM Ring", state.lightning.ring10Status],
      ["Closest Strike", state.lightning.closestText],
      ["Last Scan", state.lightning.localScanTime],
      ["Source", state.lightning.source]
    ]
  });
}

function renderAlerts(state) {
  const box = byId("alert-status");
  box.classList.toggle("active", state.alerts.count > 0);
  setText("alert-title", state.alerts.statusText);
  setText("alert-detail", state.alerts.detailText);
  setText("data-model", state.model.cycle);
  setText("data-build", `${state.model.buildText} build`);
  setText("data-status", `${state.obs.statusLabel} | Forecast ${state.model.dataHealth.status}`);
}

function tipHtml({ title, subtitle, lines = [] }) {
  return `
    <h3>${escapeHtml(title)}</h3>
    <div class="tip-sub">${escapeHtml(subtitle || "")}</div>
    <div class="tip-grid">
      ${lines.map(([label, value]) => `
        <div class="tip-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
      `).join("")}
    </div>
  `;
}

function positionTooltip(x, y) {
  const pad = 14;
  const rect = tooltip.getBoundingClientRect();
  let left = x + 16;
  let top = y + 16;
  if (left + rect.width + pad > window.innerWidth) left = x - rect.width - 16;
  if (top + rect.height + pad > window.innerHeight) top = y - rect.height - 16;
  tooltip.style.left = `${Math.max(pad, left)}px`;
  tooltip.style.top = `${Math.max(pad, top)}px`;
}

function showTooltip(payload, event) {
  if (!payload) return;
  tooltip.innerHTML = tipHtml(payload);
  tooltip.style.borderLeftColor = riskColor(payload.risk || "Little to None");
  tooltip.setAttribute("aria-hidden", "false");
  tooltip.classList.add("show");
  positionTooltip(event?.clientX ?? window.innerWidth / 2, event?.clientY ?? window.innerHeight / 2);
}

function hideTooltip() {
  tooltip.classList.remove("show");
  tooltip.setAttribute("aria-hidden", "true");
}

function attachTooltip(element, payload) {
  if (!element) return;
  element._partnerDssTooltip = payload;
  if (element._partnerDssTooltipBound) return;
  element._partnerDssTooltipBound = true;
  element.addEventListener("mouseenter", event => showTooltip(element._partnerDssTooltip, event));
  element.addEventListener("mousemove", event => positionTooltip(event.clientX, event.clientY));
  element.addEventListener("mouseleave", hideTooltip);
  element.addEventListener("focus", event => showTooltip(element._partnerDssTooltip, event));
  element.addEventListener("blur", hideTooltip);
}

function render(state) {
  partnerState = state;
  renderHeader(state);
  renderBanner(state);
  renderSinceLastUpdate(state);
  renderKeyMessages(state);
  renderLegend();
  renderTimeline(state);
  renderObs(state);
  renderLightning(state);
  renderAlerts(state);
}

async function refresh() {
  try {
    const inputs = await loadDssInputs();
    render(buildPartnerState(inputs, config));
  } catch (error) {
    console.info("Using fallback partner display state.", error);
    render(partnerState || fallbackPartnerState(config));
  }
}

render(partnerState);
refresh();
setInterval(updateHeaderClock, 60000);
setInterval(refresh, 60000);
setInterval(() => {
  radarFrame = (radarFrame + 1) % radarFrames.length;
  setRadarFrame();
}, 1400);
document.addEventListener("keydown", event => {
  if (event.key === "Escape") hideTooltip();
});
