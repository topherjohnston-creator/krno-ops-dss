export const LIKELIHOOD_CATEGORIES = [
  "Extremely Unlikely",
  "Unlikely",
  "About as Likely as Not",
  "Likely",
  "Very Likely"
];

export const PARTNER_RISK_CATEGORIES = [
  "None",
  "Little to None",
  "Minor",
  "Moderate",
  "Major",
  "Extreme"
];

export const IMPACT_LEVEL_TO_CATEGORY = {
  1: "Little to None",
  2: "Minor",
  3: "Moderate",
  4: "Major",
  5: "Extreme"
};

export const DEFAULT_RISK_MATRIX = {
  1: {
    "Extremely Unlikely": "Little to None",
    Unlikely: "Little to None",
    "About as Likely as Not": "Little to None",
    Likely: "Little to None",
    "Very Likely": "Little to None"
  },
  2: {
    "Extremely Unlikely": "Little to None",
    Unlikely: "Little to None",
    "About as Likely as Not": "Minor",
    Likely: "Minor",
    "Very Likely": "Minor"
  },
  3: {
    "Extremely Unlikely": "Minor",
    Unlikely: "Minor",
    "About as Likely as Not": "Minor",
    Likely: "Moderate",
    "Very Likely": "Moderate"
  },
  4: {
    "Extremely Unlikely": "Minor",
    Unlikely: "Minor",
    "About as Likely as Not": "Moderate",
    Likely: "Major",
    "Very Likely": "Major"
  },
  5: {
    "Extremely Unlikely": "Moderate",
    Unlikely: "Moderate",
    "About as Likely as Not": "Major",
    Likely: "Major",
    "Very Likely": "Extreme"
  }
};

export function clamp(value, min = 0, max = 100) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return min;
  return Math.max(min, Math.min(max, numeric));
}

export function getLikelihoodCategory(probability) {
  const value = clamp(probability);
  if (value < 10) return "Extremely Unlikely";
  if (value < 33) return "Unlikely";
  if (value <= 66) return "About as Likely as Not";
  if (value <= 90) return "Likely";
  return "Very Likely";
}

export function getRiskCategory(impactLevel, probability, matrix = DEFAULT_RISK_MATRIX) {
  const level = Math.max(1, Math.min(5, Math.round(Number(impactLevel) || 1)));
  const likelihood = getLikelihoodCategory(probability);
  return matrix?.[level]?.[likelihood] || IMPACT_LEVEL_TO_CATEGORY[level] || "Little to None";
}

export function probabilityClarityScore(probability) {
  const value = Number(probability);
  if (!Number.isFinite(value)) return 55;
  const edges = [10, 33, 66, 90];
  const minDistance = Math.min(...edges.map(edge => Math.abs(value - edge)));
  if (minDistance >= 15) return 95;
  if (minDistance >= 8) return 80;
  if (minDistance >= 4) return 65;
  return 45;
}

export function likelihoodSummary(probability) {
  const value = clamp(probability);
  return {
    probability: value,
    likelihood: getLikelihoodCategory(value)
  };
}
