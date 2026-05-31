import { CATEGORY_RANK, CATEGORY_COLORS, cloneDefaultConfig } from "./dss-config-schema.js";
import { calculateForecastConfidence, categoryFromRank, categoryRank } from "./confidence-engine.js";
import { getLikelihoodCategory } from "./weather-risk-matrix.js";

const KRNO_TIMEZONE = "America/Los_Angeles";
const DATA_PATHS = {
  currentObs: ["../data/krno/current_obs.json", "../observations.json"],
  timeline: ["../data/krno/timeline.json", "../nbm_timeline.json"],
  threats: ["../data/krno/threats.json", "../nbm_threats.json"],
  primaryAction: ["../data/krno/primary_action.json"],
  lightningStatus: ["../data/krno/lightning_status.json", "../lightning.json"],
  alerts: ["../data/krno/alerts.json", "../alerts.json"],
  dataHealth: ["../data/krno/data_health.json"]
};

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export async function fetchJson(path) {
  const separator = path.includes("?") ? "&" : "?";
  const response = await fetch(`${path}${separator}t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Unable to load ${path}`);
  return response.json();
}

async function fetchFirstJson(paths, fallback, label) {
  const errors = [];
  for (const path of paths) {
    try {
      return await fetchJson(path);
    } catch (error) {
      errors.push(`${path}: ${error.message}`);
    }
  }
  console.warn(`Using fallback ${label}.`, errors);
  return typeof fallback === "function" ? fallback() : fallback;
}

export function formatLocalTime(iso, options = {}) {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return "--";
  return date.toLocaleTimeString("en-US", {
    timeZone: options.timezone || KRNO_TIMEZONE,
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZoneName: options.zone ? "short" : undefined
  });
}

export function formatUtcTime(iso) {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return "--";
  return `${String(date.getUTCHours()).padStart(2, "0")}${String(date.getUTCMinutes()).padStart(2, "0")}Z`;
}

export function formatWindow(startIso, endIso, timezone = KRNO_TIMEZONE) {
  const start = new Date(startIso);
  const end = new Date(endIso);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime())) return "Timing TBD";
  const day = start.toLocaleDateString("en-US", { timeZone: timezone, weekday: "short" });
  const startTime = start.toLocaleTimeString("en-US", { timeZone: timezone, hour: "numeric", hour12: true }).replace(" ", "");
  const endDay = end.toLocaleDateString("en-US", { timeZone: timezone, weekday: "short" });
  const endTime = end.toLocaleTimeString("en-US", { timeZone: timezone, hour: "numeric", hour12: true }).replace(" ", "");
  return day === endDay ? `${day} ${startTime}-${endTime}` : `${day} ${startTime}-${endDay} ${endTime}`;
}

export function normalizeCategory(value) {
  if (typeof value === "string" && value in CATEGORY_RANK) return value;
  return categoryFromRank(value);
}

function parseSkyFromMetar(metar) {
  const tokens = String(metar || "").split(/\s+/);
  const sky = tokens.filter(token => /^(FEW|SCT|BKN|OVC|VV|CLR|SKC)\d{0,3}/.test(token));
  return sky.length ? sky.join(" / ") : "Not reported";
}

function parseWeatherFromMetar(metar) {
  const wxTokens = String(metar || "").split(/\s+/).filter(token => /^[-+]?((RA|SN|DZ|FG|BR|TS|SH|FZ|UP|GR|GS|PL){2,})$/.test(token));
  if (!wxTokens.length) return "None";
  return wxTokens.join(" ")
    .replace("-RA", "Light Rain")
    .replace("RA", "Rain")
    .replace("SN", "Snow")
    .replace("TS", "Thunder");
}

export function formatWindValue(direction, speed, gust) {
  const mph = Number(speed);
  const gustMph = Number(gust);
  if (!Number.isFinite(mph) || mph <= 0) return "CALM";
  const dirText = direction == null || String(direction).toUpperCase() === "VRB" ? "VRB" : `${Math.round(Number(direction))}°`;
  if (Number.isFinite(gustMph) && gustMph > mph) return `${dirText} ${Math.round(mph)}G${Math.round(gustMph)} mph`;
  return `${dirText} ${Math.round(mph)} mph`;
}

function isoFromCompactUtc(value) {
  if (!value) return null;
  const text = String(value);
  if (text.includes("T")) return text.replace(/Z?$/, "Z");
  const date = new Date(text);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}

export function adaptObservation(obs = {}) {
  const observedUtc = isoFromCompactUtc(obs.validUtc || obs.observed_utc || obs.generated_utc);
  const ageMinutes = Number.isFinite(Number(obs.ageMinutes))
    ? Number(obs.ageMinutes)
    : observedUtc ? (Date.now() - new Date(observedUtc).getTime()) / 60000 : Infinity;
  const status = obs.status || (ageMinutes <= 15 ? "Fresh" : ageMinutes <= 45 ? "Aging" : "Stale");
  const stale = status === "Stale" || !Number.isFinite(ageMinutes) || ageMinutes > 45;
  const speed = obs.windSpeedMph ?? obs.wind_speed_mph ?? (Number(obs.wind_speed_kt) * 1.15078);
  const gust = obs.windGustMph ?? obs.wind_gust_mph ?? (Number(obs.wind_gust_kt) * 1.15078);
  const direction = obs.windDirectionDeg ?? obs.wind_dir_deg;
  const visibility = Number(obs.visibilitySm ?? obs.visibility_sm);
  const temp = Number(obs.temperatureF ?? obs.temperature_f);
  const dew = Number(obs.dewpointF ?? obs.dewpoint_f);
  const rain = Number(obs.precip1hrIn ?? obs.precip_1hr_in ?? 0);
  return {
    source: obs.source || "KRNO observation feed",
    observedUtc,
    localTime: obs.validLocal || formatLocalTime(observedUtc, { zone: true }),
    utcTime: formatUtcTime(observedUtc),
    stale,
    ageMinutes,
    statusLabel: stale ? "Obs Stale" : status === "Aging" ? "Obs Aging" : "Obs Fresh",
    windDirection: direction,
    windSpeedMph: Number(speed),
    windGustMph: Number(gust),
    windText: formatWindValue(direction, speed, gust),
    visibilityText: Number.isFinite(visibility) ? (visibility >= 10 ? "10+ SM" : `${visibility.toFixed(1)} SM`) : "--",
    skyText: obs.skyCondition || obs.sky_condition || parseSkyFromMetar(obs.rawMetar || obs.metar),
    tempDpText: Number.isFinite(temp) && Number.isFinite(dew) ? `${Math.round(temp)} / ${Math.round(dew)}°F` : "--",
    weatherText: obs.presentWeather || obs.present_weather || parseWeatherFromMetar(obs.rawMetar || obs.metar),
    rainText: Number.isFinite(rain) ? `${rain.toFixed(2)} in` : "--",
    metar: obs.rawMetar || obs.metar || ""
  };
}

function sourceKeysForHazard(hazard) {
  if (Array.isArray(hazard.sourceKeys)) return hazard.sourceKeys;
  return [hazard.sourceKey].filter(Boolean);
}

function bestLegacyDetail(blockHazards, hazard) {
  const candidates = sourceKeysForHazard(hazard).map(source => blockHazards?.[source]).filter(Boolean);
  if (!candidates.length) return null;
  return candidates.sort((a, b) => categoryRank(b.risk_label || b.risk) - categoryRank(a.risk_label || a.risk))[0];
}

function cellsForHazard(timeline, hazard) {
  const blocks = Array.isArray(timeline?.blocks) ? timeline.blocks : [];
  if (!blocks.length) return [];

  if (blocks.some(block => block.hazardId)) {
    return blocks
      .filter(block => block.hazardId === hazard.hazardId)
      .slice(0, 24)
      .map((block, index) => ({
        blockIndex: index,
        validStartUtc: block.validStartUtc,
        validEndUtc: block.validEndUtc,
        riskCategory: normalizeCategory(block.risk),
        rank: categoryRank(normalizeCategory(block.risk)),
        metric: metricFromBlock(block),
        probability: block.probability,
        confidence: block.confidence || "Medium",
        action: block.action,
        likelihood: block.likelihood
      }));
  }

  const blockHazards = Array.isArray(timeline?.block_hazards) ? timeline.block_hazards.slice(0, 24) : [];
  return blocks.map((block, index) => {
    const detail = bestLegacyDetail(blockHazards[index], hazard);
    const ranks = sourceKeysForHazard(hazard).map(source => Number(block?.[source] ?? detail?.risk ?? 0));
    const rank = Math.max(...ranks, 0);
    const category = normalizeCategory(detail?.risk_label || rank);
    return {
      blockIndex: index,
      validStartUtc: block.valid_start_utc,
      validEndUtc: block.valid_end_utc,
      riskCategory: category,
      rank: categoryRank(category),
      metric: detail?.display_value || detail?.metric || "",
      probability: detail?.probability ?? detail?.prob ?? null,
      confidence: "Medium",
      action: detail?.action
    };
  });
}

function metricFromBlock(block) {
  if (block.metric) return block.metric;
  if (Number.isFinite(Number(block.probability))) return `${Math.round(Number(block.probability))}%`;
  return "";
}

function highestContiguousWindow(cells, timezone) {
  if (!cells.length) return { label: "Timing TBD", cells: [] };
  const maxRank = Math.max(...cells.map(cell => cell.rank));
  if (maxRank <= 1) return { label: "No action window", cells: [cells[0]] };

  let best = [];
  let current = [];
  cells.forEach(cell => {
    if (cell.rank === maxRank) {
      current.push(cell);
      if (current.length > best.length) best = current.slice();
    } else {
      current = [];
    }
  });
  const active = best.length ? best : cells.filter(cell => cell.rank === maxRank).slice(0, 1);
  return {
    label: formatWindow(active[0].validStartUtc, active[active.length - 1].validEndUtc, timezone),
    cells: active
  };
}

function threatForHazard(threats, hazard) {
  const list = Array.isArray(threats?.hazards) ? threats.hazards : null;
  if (list) return list.find(item => item.hazardId === hazard.hazardId);
  const legacy = threats?.threats || threats || {};
  const candidates = sourceKeysForHazard(hazard).map(source => legacy?.[source]).filter(Boolean);
  if (!candidates.length) return null;
  return candidates.sort((a, b) => categoryRank(b.risk_label || b.risk) - categoryRank(a.risk_label || a.risk))[0];
}

function probabilityForThreat(threat) {
  const value = threat?.probability ?? threat?.prob ?? null;
  return Number.isFinite(Number(value)) ? Number(value) : null;
}

function dataHealthForTimeline(timeline, dataHealth) {
  if (dataHealth?.forecastStatus) {
    return {
      status: String(dataHealth.forecastStatus).toLowerCase(),
      generated: dataHealth.lastBuildUtc,
      ageHours: dataHealth.lastBuildUtc ? (Date.now() - new Date(dataHealth.lastBuildUtc).getTime()) / 3600000 : Infinity
    };
  }
  const generated = timeline?.generatedAtUtc || timeline?.generated_utc || timeline?.updated_utc || timeline?.cycle_utc_iso;
  const ageHours = generated ? (Date.now() - new Date(generated).getTime()) / 3600000 : Infinity;
  const complete = Array.isArray(timeline?.blocks) && timeline.blocks.length >= 24;
  if (!complete) return { status: "missing", generated, ageHours };
  if (ageHours <= 6) return { status: "fresh", generated, ageHours };
  if (ageHours <= 10) return { status: "aging", generated, ageHours };
  return { status: "stale", generated, ageHours };
}

function actionFor(hazard, category, threat) {
  return threat?.action || hazard.actionTextByImpact?.[category] || hazard.noSignalText || "Continue monitoring.";
}

function impactPhrase(hazard, category, threat) {
  if (threat?.summary) return threat.summary;
  if (categoryRank(category) <= 1) return hazard.noSignalText || "No operational restrictions expected.";
  if (hazard.hazardId === "rampLightning") return "Isolated lightning may require ramp monitoring.";
  if (hazard.hazardId === "airfieldWind") return "Gusts may affect exposed ramp operations.";
  if (hazard.hazardId === "visibility") return "Reduced visibility may affect airfield movement.";
  if (hazard.hazardId === "rainDrainage") return "Ponding or drainage checks may be needed.";
  if (hazard.hazardId === "snowWinterOps") return "Treatment or winter staffing may be needed.";
  if (hazard.hazardId === "freezingRain") return "Surface icing checks may be needed.";
  if (hazard.hazardId === "flashFreeze") return "Wet pavement could freeze.";
  if (hazard.hazardId === "temperature") return "Crew or equipment precautions may be needed.";
  return "Weather may affect operations.";
}

function headlineFor(hazard, category, primaryAction) {
  if (primaryAction?.primaryAction) return primaryAction.primaryAction.toUpperCase();
  if (categoryRank(category) <= 1) return "CONTINUE ROUTINE AIRFIELD MONITORING";
  if (hazard.hazardId === "rampLightning") return "MONITOR RAMP LIGHTNING POTENTIAL";
  if (hazard.hazardId === "airfieldWind") return "MONITOR AIRFIELD WIND GUSTS";
  if (hazard.hazardId === "visibility") return "MONITOR AIRFIELD VISIBILITY";
  if (hazard.hazardId === "rainDrainage") return "MONITOR RAIN AND DRAINAGE";
  if (hazard.hazardId === "snowWinterOps") return "PREPARE WINTER OPERATIONS CHECKS";
  if (hazard.hazardId === "freezingRain") return "MONITOR FREEZING RAIN POTENTIAL";
  if (hazard.hazardId === "flashFreeze") return "MONITOR PAVEMENT FREEZE POTENTIAL";
  if (hazard.hazardId === "temperature") return "MONITOR TEMPERATURE IMPACTS";
  return "MONITOR WEATHER IMPACTS";
}

function makeConfidenceInput({ hazard, threat, cells, riskCategory, timeline, dataHealth }) {
  const window = highestContiguousWindow(cells, KRNO_TIMEZONE);
  return {
    hazardConfig: hazard,
    probability: probabilityForThreat(threat),
    riskCategory,
    currentRun: {
      riskCategory,
      peakUtc: threat?.peak_valid_utc || window.cells[0]?.validStartUtc
    },
    previousRuns: [],
    spread: threat?.spread || null,
    blocks: cells.map(cell => ({ riskCategory: cell.riskCategory })),
    dataHealth: dataHealthForTimeline(timeline, dataHealth)
  };
}

export function buildHazardSummaries(config, timeline, threats, dataHealth) {
  return (config.hazards || [])
    .filter(hazard => hazard.enabled && hazard.showOnPartnerDisplay)
    .map(hazard => {
      const cells = cellsForHazard(timeline, hazard);
      const threat = threatForHazard(threats, hazard);
      const cellMaxRank = cells.length ? Math.max(...cells.map(cell => cell.rank)) : 0;
      const riskCategory = normalizeCategory(threat?.risk || threat?.risk_label || cellMaxRank);
      const window = threat?.peakWindow ? { label: threat.peakWindow, cells: [] } : highestContiguousWindow(cells, config.partnerProfile.timezone);
      const confidence = threat?.confidence
        ? { label: threat.confidence, score: threat.confidenceScore, drivers: [] }
        : calculateForecastConfidence(makeConfidenceInput({ hazard, threat, cells, riskCategory, timeline, dataHealth }));
      return {
        hazardId: hazard.hazardId,
        sourceKey: hazard.sourceKey,
        displayName: threat?.displayName || hazard.displayName,
        operationalRowName: threat?.operationalRowName || hazard.operationalRowName,
        priority: hazard.priority ?? 99,
        riskCategory,
        rank: categoryRank(riskCategory),
        metric: threat?.metric || threat?.display_value || threat?.summary || hazard.noSignalText || "",
        window: window.label,
        action: actionFor(hazard, riskCategory, threat),
        impact: impactPhrase(hazard, riskCategory, threat),
        headline: headlineFor(hazard, riskCategory),
        trigger: threat?.decisionTrigger || hazard.decisionTriggerText,
        confidence,
        cells,
        threat
      };
    })
    .sort((a, b) => b.rank - a.rank || a.priority - b.priority);
}

export function buildOperationalRows(config, timeline, summaries) {
  const rows = [];
  const noSignal = [];
  const summaryById = Object.fromEntries(summaries.map(summary => [summary.hazardId, summary]));

  (config.hazards || [])
    .filter(hazard => hazard.enabled && hazard.showOnPartnerDisplay)
    .sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99))
    .forEach(hazard => {
      const summary = summaryById[hazard.hazardId];
      if (!summary) return;
      const shouldShow = hazard.alwaysShowOnTimeline || summary.rank >= CATEGORY_RANK.Minor;
      if (shouldShow) {
        rows.push({
          hazardId: hazard.hazardId,
          label: hazard.operationalRowName,
          summary,
          cells: summary.cells
        });
      } else {
        noSignal.push(hazard.operationalRowName);
      }
    });

  return { rows, noSignal };
}

export function primaryConcernFromSummaries(summaries) {
  if (!summaries.length) return null;
  return summaries.slice().sort((a, b) => b.rank - a.rank || a.priority - b.priority)[0];
}

export function buildKeyMessages(primary, config) {
  const area = `${config.partnerProfile.decisionRadiusNm} NM Decision Area`;
  if (!primary) {
    return [
      { title: "Hazard / Impact", text: "No operational weather signal in the next 72 hours." },
      { title: "Timing", text: "Routine monitoring through the valid period." },
      { title: "Action / Confidence", text: "Continue normal operations. Confidence Medium." }
    ];
  }
  return [
    {
      title: "Hazard / Impact",
      text: primary.rank <= 1 ? primary.impact : `${primary.impact} ${area}.`
    },
    {
      title: "Timing",
      text: primary.rank <= 1 ? "No action window in the next 72 hours." : `Highest concern ${primary.window}.`
    },
    {
      title: "Action / Confidence",
      text: `${primary.action} Confidence ${primary.confidence.label}.`
    }
  ];
}

function mergePrimaryAction(primary, primaryAction) {
  if (!primaryAction || !primary) return primary;
  const riskCategory = normalizeCategory(primaryAction.risk || primary.riskCategory);
  return {
    ...primary,
    headline: String(primaryAction.primaryAction || primary.headline).toUpperCase(),
    displayName: primaryAction.primaryHazard || primary.displayName,
    riskCategory,
    rank: categoryRank(riskCategory),
    window: primaryAction.peakWindow || primary.window,
    action: primaryAction.actionLine || primary.action,
    confidence: { ...primary.confidence, label: primaryAction.confidence || primary.confidence.label },
    trigger: primaryAction.decisionTrigger || primary.trigger
  };
}

export function buildSinceLastUpdate(primary, alerts, primaryAction) {
  if (primaryAction?.sinceLastUpdate) return primaryAction.sinceLastUpdate;
  const alertCount = Array.isArray(alerts?.list) ? alerts.list.length : 0;
  if (alertCount) return `${alertCount} official alert${alertCount === 1 ? "" : "s"} active. Review alert panel.`;
  if (!primary || primary.rank <= 1) return "No significant operational signal. No official alerts.";
  return `${primary.displayName} remains the main watch item. No official alerts.`;
}

export function adaptLightning(lightning = {}, config = cloneDefaultConfig()) {
  const nearest = lightning.nearest_strike || lightning.nearest || null;
  const rings = lightning.rings || {};
  const within20 = Number(rings.within_20_nm?.count || 0);
  const within10 = Number(rings.within_10_nm?.count || 0);
  const clear20 = lightning.ring20Status || (within20 > 0 ? "Strike detected" : "Clear");
  const clear10 = lightning.ring10Status || (within10 > 0 ? "Strike detected" : "Clear");
  const closestDistance = lightning.closestStrikeDistanceNm ?? nearest?.distance_nm;
  const closestDirection = lightning.closestStrikeDirection ?? nearest?.bearing_cardinal;
  const closestText = lightning.closestLightningText
    || (Number.isFinite(Number(closestDistance)) ? `${Number(closestDistance).toFixed(1)} NM ${closestDirection || ""}`.trim() : "None detected");
  const age = lightning.closestStrikeAgeMinutes ?? nearest?.age_minutes;
  return {
    source: lightning.source || "GLM lightning feed",
    generatedUtc: lightning.lastScanUtc || lightning.generated_utc,
    localScanTime: lightning.lastScanLocal || formatLocalTime(lightning.lastScanUtc || lightning.generated_utc, { zone: true }),
    ring20Status: clear20,
    ring10Status: clear10,
    closestText,
    strikeAgeText: Number.isFinite(Number(age)) ? `${Math.round(Number(age))} min ago` : "--",
    action: lightning.action || (clear20 === "Strike detected" ? "Consider ramp lightning procedures." : "Continue monitoring."),
    nearest,
    ringsNm: config.mapLayers?.ringsNm || [10, 20],
    riskCategory: clear10 === "Strike detected" ? "Major" : clear20 === "Strike detected" ? "Moderate" : "None"
  };
}

export function adaptAlerts(alerts = {}) {
  const list = Array.isArray(alerts.activeAlerts) ? alerts.activeAlerts : Array.isArray(alerts.alerts) ? alerts.alerts : [];
  return {
    generatedUtc: alerts.updatedUtc || alerts.generated_utc,
    count: list.length,
    list,
    statusText: alerts.status || (list.length ? `${list.length} Active Alert${list.length === 1 ? "" : "s"}` : "All Clear"),
    detailText: alerts.summary || (list.length ? "Review official products for KRNO." : "No active official alerts for KRNO.")
  };
}

function adaptModel(inputs) {
  const health = inputs.dataHealth || {};
  const generatedUtc = health.lastBuildUtc || inputs.timeline?.generatedAtUtc || inputs.timeline?.generated_utc || inputs.threats?.generatedAtUtc || inputs.threats?.generated_utc;
  return {
    cycle: health.forecastCycle || inputs.timeline?.forecastCycle || inputs.timeline?.cycle || inputs.threats?.forecastCycle || inputs.threats?.cycle || "NBM",
    generatedUtc,
    buildText: formatUtcTime(generatedUtc),
    dataHealth: dataHealthForTimeline(inputs.timeline, health),
    statusSummary: health.statusSummary || ""
  };
}

function makeFallbackTimeline(config) {
  const now = new Date();
  const blocks = [];
  const hazards = [
    ["rampLightning", "Ramp Lightning"],
    ["airfieldWind", "Airfield Wind"],
    ["visibility", "Visibility"],
    ["rainDrainage", "Rain / Drainage"],
    ["snowWinterOps", "Snow / Winter Ops"],
    ["freezingRain", "Freezing Rain"],
    ["flashFreeze", "Flash Freeze"],
    ["temperature", "Temperature"]
  ];
  hazards.forEach(([hazardId, rowName]) => {
    for (let index = 0; index < 24; index += 1) {
      const start = new Date(now.getTime() + index * 3 * 3600000);
      const end = new Date(start.getTime() + 3 * 3600000);
      const activeLightning = hazardId === "rampLightning" && index >= 12 && index <= 14;
      const risk = activeLightning ? "Minor" : "Little to None";
      const probability = activeLightning ? 18 : hazardId === "rampLightning" ? 4 : null;
      blocks.push({
        hazardId,
        operationalRowName: rowName,
        validStartUtc: start.toISOString(),
        validEndUtc: end.toISOString(),
        risk,
        riskValue: categoryRank(risk),
        probability,
        impactLevel: activeLightning ? 2 : 1,
        likelihood: probability == null ? null : getLikelihoodCategory(probability),
        action: activeLightning ? "Monitor ramp lightning potential." : "Routine awareness.",
        confidence: "Medium",
        confidenceScore: 62
      });
    }
  });
  return {
    generatedAtUtc: now.toISOString(),
    forecastCycle: "NBM fallback",
    blocks,
    config
  };
}

function makeFallbackThreats() {
  return {
    hazards: [
      {
        hazardId: "rampLightning",
        displayName: "Lightning",
        operationalRowName: "Ramp Lightning",
        risk: "Minor",
        peakWindow: "Sun 2PM-8PM",
        probability: 18,
        confidence: "Medium",
        action: "Monitor ramp lightning potential.",
        decisionTrigger: "Lightning within 20 NM decision ring",
        summary: "Isolated lightning may require ramp monitoring."
      },
      {
        hazardId: "airfieldWind",
        displayName: "Wind",
        operationalRowName: "Airfield Wind",
        risk: "Little to None",
        peakWindow: "No action window",
        confidence: "Medium",
        action: "Routine awareness.",
        decisionTrigger: "Gusts affecting ramp or aircraft handling",
        summary: "No ground wind restrictions expected."
      },
      {
        hazardId: "visibility",
        displayName: "Visibility",
        operationalRowName: "Visibility",
        risk: "Little to None",
        peakWindow: "No action window",
        confidence: "Medium",
        action: "Routine awareness.",
        decisionTrigger: "Reduced visibility affecting airfield movement",
        summary: "Low visibility procedures are unlikely."
      },
      {
        hazardId: "rainDrainage",
        displayName: "Rain",
        operationalRowName: "Rain / Drainage",
        risk: "Little to None",
        peakWindow: "No action window",
        confidence: "Medium",
        action: "Routine awareness.",
        decisionTrigger: "Rain rates causing drainage or ponding concerns",
        summary: "Drainage impacts are unlikely."
      }
    ]
  };
}

function makeFallbackObs() {
  const now = new Date();
  return {
    station: "KRNO",
    validLocal: formatLocalTime(now.toISOString(), { zone: true }),
    validUtc: now.toISOString(),
    ageMinutes: 0,
    status: "Fresh",
    windDirectionDeg: 280,
    windSpeedMph: 8,
    windGustMph: null,
    visibilitySm: 10,
    skyCondition: "FEW090",
    temperatureF: 66,
    dewpointF: 35,
    presentWeather: "None",
    precip1hrIn: 0,
    rawMetar: "KRNO AUTO 28008KT 10SM FEW090 19/02"
  };
}

function makeFallbackLightning() {
  const now = new Date();
  return {
    decisionRadiusNm: 20,
    ring20Status: "Clear",
    ring10Status: "Clear",
    closestStrikeDistanceNm: null,
    closestStrikeDirection: null,
    closestStrikeAgeMinutes: null,
    closestLightningText: "None detected",
    lastScanLocal: formatLocalTime(now.toISOString(), { zone: true }),
    lastScanUtc: now.toISOString(),
    action: "Continue monitoring"
  };
}

function makeFallbackPrimary() {
  return {
    primaryAction: "Monitor Ramp Lightning Potential",
    primaryHazard: "Lightning",
    risk: "Minor",
    peakWindow: "Sun 2-8 PM",
    decisionArea: "20 NM Decision Area",
    confidence: "Medium",
    actionLine: "Continue routine operations; monitor lightning ring and updates.",
    sinceLastUpdate: "Lightning remains the main watch item. No official alerts."
  };
}

function makeFallbackAlerts() {
  return {
    status: "All Clear",
    activeAlerts: [],
    summary: "No active official alerts for KRNO.",
    updatedUtc: new Date().toISOString()
  };
}

function makeFallbackHealth() {
  const now = new Date();
  return {
    obsStatus: "Fresh",
    obsAgeMinutes: 0,
    forecastStatus: "Fresh",
    forecastCycle: "NBM fallback",
    lastBuildUtc: now.toISOString(),
    statusSummary: `Obs fresh | NBM fallback | Build ${formatUtcTime(now.toISOString())}`
  };
}

export async function loadDssInputs(config = cloneDefaultConfig()) {
  const fallbackTimeline = () => makeFallbackTimeline(config);
  const [
    currentObs,
    timeline,
    threats,
    primaryAction,
    lightningStatus,
    alerts,
    dataHealth
  ] = await Promise.all([
    fetchFirstJson(DATA_PATHS.currentObs, makeFallbackObs, "current observations"),
    fetchFirstJson(DATA_PATHS.timeline, fallbackTimeline, "timeline"),
    fetchFirstJson(DATA_PATHS.threats, makeFallbackThreats, "threats"),
    fetchFirstJson(DATA_PATHS.primaryAction, makeFallbackPrimary, "primary action"),
    fetchFirstJson(DATA_PATHS.lightningStatus, makeFallbackLightning, "lightning status"),
    fetchFirstJson(DATA_PATHS.alerts, makeFallbackAlerts, "alerts"),
    fetchFirstJson(DATA_PATHS.dataHealth, makeFallbackHealth, "data health")
  ]);
  return { currentObs, timeline, threats, primaryAction, lightningStatus, alerts, dataHealth };
}

export function buildPartnerState(inputs, config = cloneDefaultConfig()) {
  const summaries = buildHazardSummaries(config, inputs.timeline, inputs.threats, inputs.dataHealth);
  const calculatedPrimary = primaryConcernFromSummaries(summaries);
  const primary = mergePrimaryAction(calculatedPrimary, inputs.primaryAction);
  const operationalTimeline = buildOperationalRows(config, inputs.timeline, summaries);
  const obs = adaptObservation(inputs.currentObs || inputs.observations);
  const lightning = adaptLightning(inputs.lightningStatus || inputs.lightning, config);
  const alerts = adaptAlerts(inputs.alerts);
  const keyMessages = inputs.primaryAction ? [
    { title: "Hazard / Impact", text: calculatedPrimary?.impact || "No operational weather signal in the next 72 hours." },
    { title: "Timing", text: primary?.rank <= 1 ? "No action window in the next 72 hours." : `Highest concern ${primary?.window || inputs.primaryAction.peakWindow}.` },
    { title: "Action / Confidence", text: `${primary?.action || inputs.primaryAction.actionLine} Confidence ${primary?.confidence?.label || inputs.primaryAction.confidence}.` }
  ] : buildKeyMessages(primary, config);
  return {
    config,
    primary,
    summaries,
    operationalTimeline,
    obs,
    lightning,
    alerts,
    keyMessages,
    sinceLastUpdate: buildSinceLastUpdate(primary, alerts, inputs.primaryAction),
    model: adaptModel(inputs),
    categoryColors: CATEGORY_COLORS
  };
}

export function fallbackPartnerState(config = cloneDefaultConfig()) {
  return buildPartnerState({
    currentObs: makeFallbackObs(),
    timeline: makeFallbackTimeline(config),
    threats: makeFallbackThreats(),
    primaryAction: makeFallbackPrimary(),
    lightningStatus: makeFallbackLightning(),
    alerts: makeFallbackAlerts(),
    dataHealth: makeFallbackHealth()
  }, config);
}
