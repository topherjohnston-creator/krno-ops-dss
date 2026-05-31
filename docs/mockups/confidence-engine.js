import { CATEGORY_RANK, DEFAULT_CONFIDENCE_WEIGHTS, IMPACT_CATEGORIES } from "./dss-config-schema.js";
import { probabilityClarityScore as riskMatrixProbabilityClarityScore } from "./weather-risk-matrix.js";

const NEUTRAL_SCORE = 55;

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

export function probabilityClarityScore({ probability }) {
  const score = riskMatrixProbabilityClarityScore(probability);
  const value = Number(probability);
  if (!Number.isFinite(value)) {
    return {
      score,
      detail: "Probability input is unavailable; using neutral clarity."
    };
  }
  if (score >= 80) {
    return {
      score,
      detail: "Probability is clearly inside one likelihood range."
    };
  }
  return {
    score,
    detail: "Probability is near a likelihood boundary."
  };
}

export function runConsistencyScore({ previousRuns = [], currentRun }) {
  if (!Array.isArray(previousRuns) || !previousRuns.length) {
    return {
      score: NEUTRAL_SCORE,
      detail: "Limited cycle history available."
    };
  }

  const currentRank = categoryRank(currentRun?.riskCategory);
  const currentPeak = new Date(currentRun?.peakUtc || 0).getTime();
  const scores = previousRuns.map(run => {
    const rankPenalty = Math.abs(categoryRank(run.riskCategory) - currentRank) * 22;
    const runPeak = new Date(run.peakUtc || 0).getTime();
    let timingPenalty = 0;
    if (Number.isFinite(currentPeak) && Number.isFinite(runPeak) && currentPeak && runPeak) {
      const hours = Math.abs(currentPeak - runPeak) / 3600000;
      timingPenalty = hours <= 3 ? 0 : hours <= 6 ? 10 : 22;
    }
    return clamp(92 - rankPenalty - timingPenalty);
  });
  const score = scores.reduce((total, value) => total + value, 0) / scores.length;
  return {
    score,
    detail: score >= 80
      ? "Recent NBM cycles show similar category and timing."
      : "Recent NBM cycles show category or timing changes."
  };
}

export function spreadAgreementScore({ spread, riskCategory }) {
  if (!spread) {
    return {
      score: NEUTRAL_SCORE,
      detail: "Percentile or spread fields are not loaded."
    };
  }

  const categories = Array.isArray(spread.categories) ? spread.categories.filter(Boolean) : [];
  if (categories.length) {
    const assignedRank = categoryRank(riskCategory);
    const ranks = categories.map(categoryRank);
    const minRank = Math.min(...ranks);
    const maxRank = Math.max(...ranks);
    const width = maxRank - minRank;
    return {
      score: width === 0 ? 92 : width === 1 ? 74 : width === 2 ? 55 : 35,
      detail: width <= 1
        ? "Forecast spread stays near the assigned category."
        : "Forecast spread crosses multiple categories."
    };
  }

  const categoryWidth = Number(spread.categoryWidth);
  if (Number.isFinite(categoryWidth)) {
    return {
      score: categoryWidth <= 0 ? 90 : categoryWidth === 1 ? 72 : categoryWidth === 2 ? 52 : 35,
      detail: categoryWidth <= 1 ? "Spread supports the assigned category." : "Spread is broad."
    };
  }

  return {
    score: NEUTRAL_SCORE,
    detail: "Spread input is incomplete."
  };
}

export function timingPersistenceScore({ blocks = [], riskCategory }) {
  if (!Array.isArray(blocks) || !blocks.length) {
    return {
      score: NEUTRAL_SCORE,
      detail: "Timeline blocks are unavailable."
    };
  }

  const activeRank = categoryRank(riskCategory);
  if (activeRank <= 1) {
    const quietBlocks = blocks.filter(block => categoryRank(block.riskCategory) <= 1).length;
    return {
      score: clamp(52 + (quietBlocks / blocks.length) * 43),
      detail: "Quiet signal persists through most forecast blocks."
    };
  }

  let bestRun = 0;
  let run = 0;
  blocks.forEach(block => {
    if (categoryRank(block.riskCategory) >= activeRank) {
      run += 1;
      bestRun = Math.max(bestRun, run);
    } else {
      run = 0;
    }
  });

  const score = bestRun >= 3 ? 90 : bestRun === 2 ? 70 : bestRun === 1 ? 45 : NEUTRAL_SCORE;
  return {
    score,
    detail: bestRun >= 2
      ? "Signal appears in adjacent forecast blocks."
      : "Signal is isolated to one forecast block."
  };
}

export function dataHealthScore({ dataHealth }) {
  if (!dataHealth) {
    return {
      score: NEUTRAL_SCORE,
      detail: "Data health is not reported."
    };
  }
  const status = String(dataHealth.status || "").toLowerCase();
  if (status.includes("fresh") || status.includes("good")) {
    return { score: 100, detail: "NBM data are fresh and complete." };
  }
  if (status.includes("aging")) {
    return { score: 70, detail: "NBM data are aging but usable." };
  }
  if (status.includes("stale")) {
    return { score: 30, detail: "NBM data are stale." };
  }
  if (status.includes("missing")) {
    return { score: 10, detail: "Key NBM fields are missing." };
  }
  return { score: NEUTRAL_SCORE, detail: "Data health is neutral." };
}

export function calculateForecastConfidence(input = {}) {
  const weights = normalizeWeights(input.hazardConfig?.confidenceWeights || input.weights);
  const components = {
    probabilityClarity: probabilityClarityScore(input),
    runConsistency: runConsistencyScore(input),
    spreadAgreement: spreadAgreementScore(input),
    timingPersistence: timingPersistenceScore(input),
    dataHealth: dataHealthScore(input)
  };

  const score = clamp(Object.entries(weights).reduce((total, [key, weight]) => {
    return total + (components[key]?.score ?? NEUTRAL_SCORE) * Number(weight || 0);
  }, 0));

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
    detail: drivers[0] || "Forecast confidence uses NBM probability clarity, spread, timing, run consistency, and data health."
  };
}
