import { CATEGORY_RANK, CATEGORY_COLORS, cloneDefaultConfig } from "./dss-config-schema.js";
import { calculateForecastConfidence, categoryFromRank, categoryRank } from "./confidence-engine.js";

const KRNO_TIMEZONE = "America/Los_Angeles";

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

function rankForDetail(detail) {
  return categoryRank(detail?.risk_label || detail?.risk || detail?.level);
}

function sourceKeysForHazard(hazard) {
  return Array.isArray(hazard.sourceKeys) ? hazard.sourceKeys : [hazard.sourceKey];
}

function bestDetailForBlock(blockHazards, sourceKeys) {
  const candidates = sourceKeys.map(source => blockHazards?.[source]).filter(Boolean);
  if (!candidates.length) return null;
  return candidates.sort((a, b) => rankForDetail(b) - rankForDetail(a))[0];
}

function threatForHazard(threats, hazard) {
  const candidates = sourceKeysForHazard(hazard).map(source => threats?.[source]).filter(Boolean);
  if (!candidates.length) return null;
  return candidates.sort((a, b) => categoryRank(b.risk_label || b.risk) - categoryRank(a.risk_label || a.risk))[0];
}

function blockCellsForHazard(timeline, hazard) {
  const blocks = Array.isArray(timeline?.blocks) ? timeline.blocks.slice(0, 24) : [];
  const blockHazards = Array.isArray(timeline?.block_hazards) ? timeline.block_hazards.slice(0, 24) : [];
  const sourceKeys = sourceKeysForHazard(hazard);
  return blocks.map((block, index) => {
    const detail = bestDetailForBlock(blockHazards[index], sourceKeys);
    const sourceRanks = sourceKeys.map(source => Number(block?.[source] ?? detail?.risk ?? 0));
    const rank = Math.max(...sourceRanks, rankForDetail(detail), 0);
    const riskCategory = normalizeCategory(detail?.risk_label || rank);
    return {
      blockIndex: index,
      validStartUtc: block.valid_start_utc,
      validEndUtc: block.valid_end_utc,
      riskCategory,
      rank: categoryRank(riskCategory),
      detail,
      metric: detail?.metric || detail?.display_value || "",
      probability: detail?.probability ?? detail?.prob ?? detail?.values?.probability ?? detail?.values?.prob ?? null
    };
  });
}

function highestContiguousWindow(cells, timezone) {
  if (!cells.length) return { label: "Timing TBD", cells: [] };
  const maxRank = Math.max(...cells.map(cell => cell.rank));
  if (maxRank <= 1) {
    const first = cells[0];
    return { label: "No action window", cells: [first] };
  }

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
  const windowCells = best.length ? best : cells.filter(cell => cell.rank === maxRank).slice(0, 1);
  return {
    label: formatWindow(windowCells[0].validStartUtc, windowCells[windowCells.length - 1].validEndUtc, timezone),
    cells: windowCells
  };
}

function metricForThreat(threat, hazard) {
  if (!threat) return hazard.noSignalText || "No signal";
  if (threat.display_value) return String(threat.display_value);
  if (threat.metric) return String(threat.metric).replace(/^Peak 3-hour thunder probability$/i, `Thunder ${Math.round(Number(threat.probability ?? 0))}%`);
  return hazard.noSignalText || "No signal";
}

function probabilityForThreat(threat) {
  const value = threat?.probability ?? threat?.prob ?? null;
  return Number.isFinite(Number(value)) ? Number(value) : null;
}

function dataHealthForTimeline(timeline) {
  const generated = timeline?.generated_utc || timeline?.updated_utc || timeline?.cycle_utc_iso;
  const ageHours = generated ? (Date.now() - new Date(generated).getTime()) / 3600000 : Infinity;
  const complete = Array.isArray(timeline?.blocks) && timeline.blocks.length >= 24 && Array.isArray(timeline?.block_hazards) && timeline.block_hazards.length >= 24;
  if (!complete) return { status: "missing", generated, ageHours };
  if (ageHours <= 6) return { status: "fresh", generated, ageHours };
  if (ageHours <= 10) return { status: "aging", generated, ageHours };
  return { status: "stale", generated, ageHours };
}

function makeConfidenceInput({ hazard, threat, cells, timeline }) {
  const riskCategory = normalizeCategory(threat?.risk_label || threat?.risk || Math.max(...cells.map(cell => cell.rank), 0));
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
    spatial: threat?.spatial || null,
    blocks: cells.map(cell => ({ riskCategory: cell.riskCategory })),
    dataHealth: dataHealthForTimeline(timeline)
  };
}

function actionFor(hazard, category) {
  return hazard.actionTextByImpact?.[category] || hazard.noSignalText || "Continue monitoring.";
}

function impactPhrase(hazard, category) {
  if (categoryRank(category) <= 1) return hazard.noSignalText || "No operational restrictions expected.";
  if (hazard.hazardId === "rampLightning") return "Ramp lightning monitoring may be needed.";
  if (hazard.hazardId === "airfieldWind") return "Gusts may affect exposed ramp operations.";
  if (hazard.hazardId === "visibilityCeiling") return "Reduced visibility may affect airfield movement.";
  if (hazard.hazardId === "rainDrainage") return "Ponding or drainage checks may be needed.";
  if (hazard.hazardId === "winterOps") return "Runway treatment or winter staffing may be needed.";
  if (hazard.hazardId === "flashFreeze") return "Wet pavement could freeze.";
  if (hazard.hazardId === "temperature") return "Crew or equipment precautions may be needed.";
  return "Weather may affect operations.";
}

function headlineFor(hazard, category) {
  if (categoryRank(category) <= 1) return "CONTINUE ROUTINE AIRFIELD MONITORING";
  if (hazard.hazardId === "rampLightning") return "MONITOR RAMP LIGHTNING POTENTIAL";
  if (hazard.hazardId === "airfieldWind") return "MONITOR AIRFIELD WIND GUSTS";
  if (hazard.hazardId === "visibilityCeiling") return "MONITOR AIRFIELD VISIBILITY";
  if (hazard.hazardId === "rainDrainage") return "MONITOR RAIN AND DRAINAGE";
  if (hazard.hazardId === "winterOps") return "PREPARE WINTER OPERATIONS CHECKS";
  if (hazard.hazardId === "flashFreeze") return "MONITOR PAVEMENT FREEZE POTENTIAL";
  if (hazard.hazardId === "temperature") return "MONITOR TEMPERATURE IMPACTS";
  return "MONITOR WEATHER IMPACTS";
}

export function buildHazardSummaries(config, timeline, threats) {
  return (config.hazards || [])
    .filter(hazard => hazard.enabled && hazard.showOnPartnerDisplay)
    .map(hazard => {
      const cells = blockCellsForHazard(timeline, hazard);
      const threat = threatForHazard(threats?.threats || threats || {}, hazard);
      const riskCategory = normalizeCategory(threat?.risk_label || threat?.risk || Math.max(...cells.map(cell => cell.rank), 0));
      const window = highestContiguousWindow(cells, config.partnerProfile.timezone);
      const confidence = calculateForecastConfidence(makeConfidenceInput({ hazard, threat, cells, timeline }));
      return {
        hazardId: hazard.hazardId,
        sourceKey: hazard.sourceKey,
        displayName: hazard.displayName,
        operationalRowName: hazard.operationalRowName,
        priority: hazard.priority ?? 99,
        riskCategory,
        rank: categoryRank(riskCategory),
        metric: metricForThreat(threat, hazard),
        window: window.label,
        action: actionFor(hazard, riskCategory),
        impact: impactPhrase(hazard, riskCategory),
        headline: headlineFor(hazard, riskCategory),
        trigger: hazard.decisionTriggerText,
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

export function buildSinceLastUpdate(primary, alerts) {
  const alertCount = Array.isArray(alerts?.alerts) ? alerts.alerts.length : 0;
  if (alertCount) return `${alertCount} official alert${alertCount === 1 ? "" : "s"} active. Review alert panel.`;
  if (!primary || primary.rank <= 1) return "No significant operational signal. No official alerts.";
  return `${primary.displayName} remains the main watch item. No official alerts.`;
}

function parseSkyFromMetar(metar) {
  const tokens = String(metar || "").split(/\s+/);
  const sky = tokens.filter(token => /^(FEW|SCT|BKN|OVC|VV|CLR|SKC)\d{0,3}/.test(token));
  return sky.length ? sky.join(" / ") : "Not reported";
}

function parseWeatherFromMetar(metar) {
  const wxTokens = String(metar || "").split(/\s+/).filter(token => /^[-+]?((RA|SN|DZ|FG|BR|TS|SH|FZ|UP|GR|GS|PL){2,})$/.test(token));
  if (!wxTokens.length) return "None";
  return wxTokens.join(" ").replace("-RA", "Light Rain").replace("RA", "Rain").replace("SN", "Snow").replace("TS", "Thunder");
}

export function formatWindValue(direction, speed, gust) {
  const mph = Number(speed);
  const gustMph = Number(gust);
  if (!Number.isFinite(mph) || mph <= 0) return "CALM";
  const dirText = direction == null || String(direction).toUpperCase() === "VRB" ? "VRB" : `${Math.round(Number(direction))}°`;
  if (Number.isFinite(gustMph) && gustMph > mph) return `${dirText} ${Math.round(mph)}G${Math.round(gustMph)} mph`;
  return `${dirText} ${Math.round(mph)} mph`;
}

export function adaptObservation(obs = {}) {
  const observedUtc = obs.observed_utc || obs.generated_utc;
  const ageMinutes = observedUtc ? (Date.now() - new Date(observedUtc).getTime()) / 60000 : Infinity;
  const stale = !Number.isFinite(ageMinutes) || ageMinutes > 30;
  const windText = formatWindValue(obs.wind_dir_deg, obs.wind_speed_mph ?? (Number(obs.wind_speed_kt) * 1.15078), obs.wind_gust_mph ?? (Number(obs.wind_gust_kt) * 1.15078));
  const visibility = Number(obs.visibility_sm);
  const temp = Number(obs.temperature_f);
  const dew = Number(obs.dewpoint_f);
  const rain = Number(obs.precip_1hr_in || 0);
  return {
    source: obs.source || "Observation feed",
    observedUtc,
    localTime: formatLocalTime(observedUtc, { zone: true }),
    utcTime: formatUtcTime(observedUtc),
    stale,
    ageMinutes,
    statusLabel: stale ? "Obs Stale" : ageMinutes > 15 ? "Obs Aging" : "Obs Fresh",
    windDirection: obs.wind_dir_deg,
    windSpeedMph: Number(obs.wind_speed_mph ?? (Number(obs.wind_speed_kt) * 1.15078)),
    windGustMph: Number(obs.wind_gust_mph ?? (Number(obs.wind_gust_kt) * 1.15078)),
    windText,
    visibilityText: Number.isFinite(visibility) ? (visibility >= 10 ? "10+ SM" : `${visibility.toFixed(1)} SM`) : "--",
    skyText: obs.sky_condition || parseSkyFromMetar(obs.metar),
    tempDpText: Number.isFinite(temp) && Number.isFinite(dew) ? `${Math.round(temp)} / ${Math.round(dew)}°F` : "--",
    weatherText: obs.present_weather || parseWeatherFromMetar(obs.metar),
    rainText: Number.isFinite(rain) ? `${rain.toFixed(2)} in` : "--",
    metar: obs.metar || ""
  };
}

export function adaptLightning(lightning = {}, config = cloneDefaultConfig()) {
  const nearest = lightning.nearest_strike || null;
  const rings = lightning.rings || {};
  const within20 = Number(rings.within_20_nm?.count || 0);
  const within10 = Number(rings.within_10_nm?.count || 0);
  const age = nearest?.age_minutes;
  return {
    source: lightning.source || "Lightning feed",
    generatedUtc: lightning.generated_utc,
    localScanTime: formatLocalTime(lightning.generated_utc, { zone: true }),
    ring20Status: within20 > 0 ? "Strike detected" : "Clear",
    ring10Status: within10 > 0 ? "Strike detected" : "Clear",
    closestText: nearest ? `${Number(nearest.distance_nm).toFixed(1)} NM ${nearest.bearing_cardinal || ""}`.trim() : "None detected",
    strikeAgeText: Number.isFinite(Number(age)) ? `${Math.round(Number(age))} min ago` : "--",
    action: within20 > 0 ? "Consider ramp lightning procedures." : "Continue monitoring.",
    nearest,
    ringsNm: config.mapLayers?.ringsNm || [10, 20],
    riskCategory: within10 > 0 ? "Major" : within20 > 0 ? "Moderate" : "None"
  };
}

export function adaptAlerts(alerts = {}) {
  const list = Array.isArray(alerts.alerts) ? alerts.alerts : [];
  return {
    generatedUtc: alerts.generated_utc,
    count: list.length,
    list,
    statusText: list.length ? `${list.length} Active Alert${list.length === 1 ? "" : "s"}` : "All Clear",
    detailText: list.length ? "Review official products for KRNO." : "No active official alerts for KRNO."
  };
}

export async function loadDssInputs() {
  const [timeline, threats, observations, lightning, alerts] = await Promise.all([
    fetchJson("../nbm_timeline.json"),
    fetchJson("../nbm_threats.json"),
    fetchJson("../observations.json"),
    fetchJson("../lightning.json"),
    fetchJson("../alerts.json")
  ]);
  return { timeline, threats, observations, lightning, alerts };
}

export function buildPartnerState(inputs, config = cloneDefaultConfig()) {
  const summaries = buildHazardSummaries(config, inputs.timeline, inputs.threats);
  const primary = primaryConcernFromSummaries(summaries);
  const operationalTimeline = buildOperationalRows(config, inputs.timeline, summaries);
  const obs = adaptObservation(inputs.observations);
  const lightning = adaptLightning(inputs.lightning, config);
  const alerts = adaptAlerts(inputs.alerts);
  const keyMessages = buildKeyMessages(primary, config);
  const dataHealth = dataHealthForTimeline(inputs.timeline);
  return {
    config,
    primary,
    summaries,
    operationalTimeline,
    obs,
    lightning,
    alerts,
    keyMessages,
    sinceLastUpdate: buildSinceLastUpdate(primary, alerts),
    model: {
      cycle: inputs.timeline?.cycle || inputs.threats?.cycle || "NBM",
      generatedUtc: inputs.timeline?.generated_utc || inputs.threats?.generated_utc,
      buildText: formatUtcTime(inputs.timeline?.generated_utc || inputs.threats?.generated_utc),
      dataHealth
    },
    categoryColors: CATEGORY_COLORS
  };
}

export function fallbackPartnerState(config = cloneDefaultConfig()) {
  const now = new Date();
  const blocks = Array.from({ length: 24 }, (_, index) => {
    const start = new Date(now.getTime() + index * 3 * 3600000);
    const end = new Date(start.getTime() + 3 * 3600000);
    return {
      valid_start_utc: start.toISOString(),
      valid_end_utc: end.toISOString(),
      WIND: 1,
      LIGHTNING: index >= 12 && index <= 14 ? 2 : 1,
      RAIN: 1,
      VISIBILITY: 1,
      SNOW: 1,
      FZRA: 1,
      FLASH_FREEZE: 1,
      TEMPERATURE: 1
    };
  });
  const block_hazards = blocks.map((block, index) => ({
    LIGHTNING: {
      risk: block.LIGHTNING,
      risk_label: normalizeCategory(block.LIGHTNING),
      metric: index >= 12 && index <= 14 ? "Thunder 12%" : "Thunder 2%",
      prob: index >= 12 && index <= 14 ? 12 : 2,
      valid_start_utc: block.valid_start_utc,
      valid_end_utc: block.valid_end_utc
    }
  }));
  return buildPartnerState({
    timeline: {
      cycle: "NBM",
      generated_utc: now.toISOString(),
      blocks,
      block_hazards
    },
    threats: {
      threats: {
        LIGHTNING: {
          risk: 2,
          risk_label: "Minor",
          display_value: "Thunder 12%",
          prob: 12,
          peak_valid_utc: blocks[12].valid_start_utc
        }
      }
    },
    observations: {
      observed_utc: now.toISOString(),
      wind_speed_mph: 8,
      wind_dir_deg: 280,
      visibility_sm: 10,
      temperature_f: 62,
      dewpoint_f: 38,
      precip_1hr_in: 0,
      metar: "KRNO AUTO 28007KT 10SM FEW065 17/03"
    },
    lightning: {
      generated_utc: now.toISOString(),
      rings: {},
      nearest_strike: null
    },
    alerts: { alerts: [] }
  }, config);
}
