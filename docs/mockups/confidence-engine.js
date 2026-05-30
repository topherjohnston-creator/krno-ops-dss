import { CATEGORY_RANK, DEFAULT_CONFIDENCE_WEIGHTS, IMPACT_CATEGORIES } from "./dss-config-schema.js";

const DEFAULT_COMPONENT_SCORE = 60;

function clamp(value, min = 0, max = 100) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return min;
  return Math.max(min, Math.min(max, numeric));
}

function normalizeWeights(weights = {}) {
  const merged = { ...DEFAULT_CONFIDENCE_WEIGHTS, ...weights };
  const sum = Object.values(merged).reduce((total, value) => total + Number(value || 0), 0) || 1;
  return Object.fromEntries(Object.entries(merged).map(([key, value]) => [key, Number(value || 0) / sum]));
}

export function categoryRank(category) {
  return CATEGORY_RANK[category] ?? 0;
}

export function categoryFromRank(rank) {
  const numeric = Math.max(0, Math.min(5, Math.round(Number(rank) || 0)));
  return IMPACT_CATEGORIES[numeric] || "None";
}

export function confidenceLabel(score) {
  const value = clamp(score);
  if (value >= 80) return "High";
  if (value >= 55) return "Medium";
  return "Low";
}

function sortedProbabilityBreakpoints(hazardConfig = {}) {
  const probabilityThresholds = hazardConfig.probabilityThresholds || {};
  const impactThresholds = hazardConfig.impactThresholds || {};
  const thresholds = {
    Minor: probabilityThresholds.Minor ?? impactThresholds.Minor,
    Moderate: probabilityThresholds.Moderate ?? impactThresholds.Moderate,
    Major: probabilityThresholds.Major ?? impactThresholds.Major,
    Extreme: probabilityThresholds.Extreme ?? impactThresholds.Extreme
  };
  return Object.entries(thresholds)
    .filter(([, value]) => Number.isFinite(Number(value)))
    .map(([category, value]) => ({ category, value: Number(value), rank: categoryRank(category) }))
    .sort((a, b) => a.value - b.value);
}

function probabilityCategory(probability, hazardConfig = {}) {
  const prob = Number(probability);
  if (!Number.isFinite(prob)) return null;
  let category = "Little to None";
  for (const item of sortedProbabilityBreakpoints(hazardConfig)) {
    if (prob >= item.value) category = item.category;
  }
  return category;
}

export function probabilitySupportScore({ probability, riskCategory, hazardConfig }) {
  const prob = Number(probability);
  const assignedRank = categoryRank(riskCategory);
  const breakpoints = sortedProbabilityBreakpoints(hazardConfig);

  if (!Number.isFinite(prob) || !breakpoints.length) {
    return {
      score: DEFAULT_COMPONENT_SCORE,
      detail: "No probability distribution available; using neutral forecast support."
    };
  }

  const estimatedCategory = probabilityCategory(prob, hazardConfig);
  const estimatedRank = categoryRank(estimatedCategory);
  const minorPoint = breakpoints.find(item => item.category === "Minor")?.value ?? 10;

  if (assignedRank <= 1) {
    if (prob <= Math.max(1, minorPoint * 0.25)) {
      return { score: 92, detail: "Low exceedance probability supports a quiet forecast." };
    }
    if (prob < minorPoint) {
      return { score: 76, detail: "Exceedance probability remains below the first action category." };
    }
    return { score: 48, detail: "Probability suggests an action category may be close." };
  }

  const relevant = breakpoints.filter(item => Math.abs(item.rank - assignedRank) <= 1);
  const nearestDistance = relevant.reduce((best, item) => Math.min(best, Math.abs(prob - item.value)), Infinity);
  const nearestScale = Math.max(8, Math.min(22, minorPoint));
  let score = 58 + clamp(nearestDistance / nearestScale, 0, 1) * 32;

  if (Math.abs(estimatedRank - assignedRank) >= 2) score -= 28;
  else if (estimatedRank !== assignedRank) score -= 14;

  return {
    score: clamp(score),
    detail: estimatedRank === assignedRank
      ? "Probability supports the displayed action category."
      : "Probability is close to a neighboring action category."
  };
}

export function spreadAgreementScore({ spread, riskCategory, hazardConfig }) {
  if (!spread) {
    return {
      score: DEFAULT_COMPONENT_SCORE,
      detail: "Percentile or member spread is not loaded for this prototype."
    };
  }

  const categories = Array.isArray(spread.categories) ? spread.categories.filter(Boolean) : [];
  if (categories.length) {
    const assignedRank = categoryRank(riskCategory);
    const matching = categories.filter(category => Math.abs(categoryRank(category) - assignedRank) === 0).length;
    const near = categories.filter(category => Math.abs(categoryRank(category) - assignedRank) <= 1).length;
    return {
      score: clamp(40 + (matching / categories.length) * 42 + (near / categories.length) * 18),
      detail: matching === categories.length
        ? "Spread members agree on the displayed category."
        : "Spread members cross one or more neighboring categories."
    };
  }

  const p10 = Number(spread.p10);
  const p90 = Number(spread.p90);
  if (Number.isFinite(p10) && Number.isFinite(p90)) {
    const lowCategory = probabilityCategory(p10, hazardConfig) || "Little to None";
    const highCategory = probabilityCategory(p90, hazardConfig) || "Little to None";
    const width = Math.abs(categoryRank(highCategory) - categoryRank(lowCategory));
    return {
      score: width === 0 ? 90 : width === 1 ? 72 : width === 2 ? 52 : 36,
      detail: width <= 1
        ? "Percentile spread stays near the displayed category."
        : "Percentile spread crosses multiple action categories."
    };
  }

  return {
    score: DEFAULT_COMPONENT_SCORE,
    detail: "Spread input was incomplete."
  };
}

export function runConsistencyScore({ currentRun, previousRuns = [] }) {
  if (!Array.isArray(previousRuns) || !previousRuns.length) {
    return {
      score: DEFAULT_COMPONENT_SCORE,
      detail: "Previous cycles are not loaded in this prototype."
    };
  }

  const currentRank = categoryRank(currentRun?.riskCategory);
  const currentPeak = new Date(currentRun?.peakUtc || 0).getTime();
  const scores = previousRuns.map(run => {
    const rankDiff = Math.abs(categoryRank(run.riskCategory) - currentRank);
    let timingPenalty = 0;
    const runPeak = new Date(run.peakUtc || 0).getTime();
    if (Number.isFinite(currentPeak) && Number.isFinite(runPeak) && currentPeak && runPeak) {
      const hourDiff = Math.abs(currentPeak - runPeak) / 3600000;
      timingPenalty = hourDiff <= 3 ? 0 : hourDiff <= 6 ? 8 : 18;
    }
    return clamp(92 - rankDiff * 22 - timingPenalty);
  });

  const score = scores.reduce((total, value) => total + value, 0) / scores.length;
  return {
    score,
    detail: score >= 80
      ? "Recent cycles support a stable category and timing."
      : "Recent cycles show category or timing changes."
  };
}

export function spatialConsistencyScore({ spatial }) {
  if (!spatial) {
    return {
      score: DEFAULT_COMPONENT_SCORE,
      detail: "Decision-area spatial sampling is not loaded in this prototype."
    };
  }

  const coverage = clamp(Number(spatial.coveragePercent), 0, 100);
  const gradient = clamp(Number(spatial.gradientPenalty ?? 0), 0, 40);
  const score = clamp(45 + coverage * 0.55 - gradient);
  return {
    score,
    detail: coverage >= 70
      ? "Signal is broad across the decision area."
      : "Signal is localized within the decision area."
  };
}

export function timingPersistenceScore({ blocks = [], riskCategory }) {
  const activeRank = categoryRank(riskCategory);
  if (!Array.isArray(blocks) || !blocks.length) {
    return {
      score: DEFAULT_COMPONENT_SCORE,
      detail: "Timeline blocks are not available."
    };
  }

  if (activeRank <= 1) {
    const quietBlocks = blocks.filter(block => categoryRank(block.riskCategory) <= 1).length;
    return {
      score: clamp(52 + (quietBlocks / blocks.length) * 43),
      detail: "Quiet forecast persistence is based on the full timeline."
    };
  }

  let bestRun = 0;
  let currentRun = 0;
  blocks.forEach(block => {
    if (categoryRank(block.riskCategory) >= activeRank) {
      currentRun += 1;
      bestRun = Math.max(bestRun, currentRun);
    } else {
      currentRun = 0;
    }
  });

  const score = bestRun >= 4 ? 88 : bestRun === 3 ? 78 : bestRun === 2 ? 64 : 46;
  return {
    score,
    detail: bestRun >= 2
      ? "Signal persists across adjacent forecast blocks."
      : "Signal is isolated to a short forecast window."
  };
}

export function dataHealthScore({ dataHealth }) {
  if (!dataHealth) {
    return {
      score: DEFAULT_COMPONENT_SCORE,
      detail: "Forecast data health is not reported."
    };
  }

  const status = String(dataHealth.status || "").toLowerCase();
  if (status.includes("fresh") || status.includes("good")) {
    return { score: 92, detail: "Forecast data are fresh and complete." };
  }
  if (status.includes("aging")) {
    return { score: 70, detail: "Forecast data are aging but usable." };
  }
  if (status.includes("stale")) {
    return { score: 42, detail: "Forecast data are stale." };
  }
  if (status.includes("missing")) {
    return { score: 20, detail: "Forecast data are missing required fields." };
  }
  return { score: DEFAULT_COMPONENT_SCORE, detail: "Forecast data health is neutral." };
}

export function optionalAgreementScore(input, key) {
  const value = input?.[key];
  if (!value) return null;
  const score = clamp(value.score ?? value);
  return {
    score,
    detail: value.detail || "Optional agreement component applied."
  };
}

export function calculateForecastConfidence(input = {}) {
  const weights = normalizeWeights(input.hazardConfig?.confidenceWeights || input.weights);
  const components = {
    probabilitySupport: probabilitySupportScore(input),
    runConsistency: runConsistencyScore(input),
    spreadAgreement: spreadAgreementScore(input),
    spatialConsistency: spatialConsistencyScore(input),
    timingPersistence: timingPersistenceScore(input),
    dataHealth: dataHealthScore(input)
  };

  const sourceAgreement = optionalAgreementScore(input, "sourceAgreement");
  const obsRadarAgreement = optionalAgreementScore(input, "obsRadarAgreement");
  if (sourceAgreement) components.sourceAgreement = sourceAgreement;
  if (obsRadarAgreement) components.obsRadarAgreement = obsRadarAgreement;

  let score = Object.entries(weights).reduce((total, [key, weight]) => {
    return total + (components[key]?.score ?? DEFAULT_COMPONENT_SCORE) * Number(weight || 0);
  }, 0);

  if (sourceAgreement) score = score * 0.9 + sourceAgreement.score * 0.1;
  if (obsRadarAgreement) score = score * 0.9 + obsRadarAgreement.score * 0.1;

  score = clamp(score);
  return {
    score,
    label: confidenceLabel(score),
    components,
    drivers: Object.values(components)
      .map(component => component.detail)
      .filter(Boolean)
      .slice(0, 4)
  };
}

export function summarizeConfidence(confidence) {
  const label = confidence?.label || "Medium";
  const drivers = Array.isArray(confidence?.drivers) ? confidence.drivers : [];
  return {
    label,
    shortText: `Confidence ${label}`,
    detail: drivers[0] || "Forecast confidence uses NBM probability, spread, timing, and consistency signals."
  };
}
