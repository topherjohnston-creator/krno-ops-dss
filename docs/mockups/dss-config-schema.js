import { DEFAULT_RISK_MATRIX } from "./weather-risk-matrix.js";

export const IMPACT_CATEGORIES = [
  "None",
  "Little to None",
  "Minor",
  "Moderate",
  "Major",
  "Extreme"
];

export const PARTNER_IMPACT_LABELS = {
  1: "Little to None",
  2: "Minor",
  3: "Moderate",
  4: "Major",
  5: "Extreme"
};

export const CATEGORY_RANK = {
  None: 0,
  "Little to None": 1,
  Minor: 2,
  Moderate: 3,
  Major: 4,
  Extreme: 5
};

export const CATEGORY_COLORS = {
  None: "#2d3745",
  "Little to None": "#4f677d",
  Minor: "#d7bc4a",
  Moderate: "#d98c33",
  Major: "#d25555",
  Extreme: "#b44fd4"
};

export const DATA_SOURCE_OPTIONS = ["NBM"];

export const DEFAULT_CONFIDENCE_WEIGHTS = {
  probabilityClarity: 0.30,
  runConsistency: 0.25,
  spreadAgreement: 0.20,
  timingPersistence: 0.15,
  dataHealth: 0.10
};

export const PARTNER_TYPE_TEMPLATES = {
  airport: {
    label: "Airport Operations",
    hazards: ["rampLightning", "airfieldWind", "visibility", "rainDrainage", "snowWinterOps", "freezingRain", "flashFreeze", "temperature"]
  },
  outdoorEvent: {
    label: "Outdoor Event",
    hazards: ["rampLightning", "airfieldWind", "rainDrainage", "temperature"]
  },
  burnScar: {
    label: "Burn Scar / Flooding",
    hazards: ["rainDrainage", "rampLightning"]
  },
  fireWeather: {
    label: "Fire Weather",
    hazards: ["airfieldWind", "temperature", "rampLightning"]
  },
  transportation: {
    label: "Road / Transportation",
    hazards: ["snowWinterOps", "freezingRain", "flashFreeze", "airfieldWind", "visibility", "rainDrainage", "temperature"]
  },
  publicSafety: {
    label: "Public Safety / Emergency Management",
    hazards: ["rampLightning", "airfieldWind", "rainDrainage", "snowWinterOps", "temperature"]
  },
  custom: {
    label: "Custom",
    hazards: []
  }
};

function thresholds(entries) {
  return Object.fromEntries(entries.map(([level, label, threshold, action, description = ""]) => [
    String(level),
    { label, threshold, action, description }
  ]));
}

const airportHazards = [
  {
    hazardId: "rampLightning",
    sourceKey: "LIGHTNING",
    displayName: "Lightning",
    operationalRowName: "Ramp Lightning",
    operationalMeaning: "Ramp monitoring and possible lightning procedures.",
    enabled: true,
    showOnPartnerDisplay: true,
    alwaysShowOnTimeline: true,
    priority: 1,
    dataSource: "NBM",
    modelVariable: "nbm_prob_thunder_3hr",
    units: "%",
    thresholdDirection: "greaterThanOrEqual",
    defaultGeometry: "radius",
    defaultAggregation: "max",
    decisionTriggerText: "Lightning within the 20 NM decision ring",
    noSignalText: "No lightning restrictions expected.",
    impactThresholds: thresholds([
      [1, "Little to None", 0, "Routine awareness.", "Thunder probability remains below partner concern."],
      [2, "Minor", 10, "Monitor ramp lightning potential.", "Isolated thunder could require closer monitoring."],
      [3, "Moderate", 25, "Prepare for ramp lightning procedures.", "Thunder potential is high enough to plan for ramp response."],
      [4, "Major", 50, "Coordinate likely ramp lightning procedures if storms approach.", "Lightning procedures may be needed."],
      [5, "Extreme", 75, "Suspend exposed ramp operations if lightning is detected nearby.", "High concern if storms approach the field."]
    ]),
    confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS }
  },
  {
    hazardId: "airfieldWind",
    sourceKey: "WIND",
    displayName: "Wind",
    operationalRowName: "Airfield Wind",
    operationalMeaning: "Ramp equipment, crosswind, and ground handling impacts.",
    enabled: true,
    showOnPartnerDisplay: true,
    alwaysShowOnTimeline: true,
    priority: 2,
    dataSource: "NBM",
    modelVariable: "nbm_wind_gust_10m",
    summaryVariable: "nbm_wind_gust_24hr_qmd",
    units: "mph",
    thresholdDirection: "greaterThanOrEqual",
    defaultGeometry: "radius",
    defaultAggregation: "max",
    decisionTriggerText: "Gusts affecting ramp or aircraft handling",
    noSignalText: "No ground wind restrictions expected.",
    impactThresholds: thresholds([
      [1, "Little to None", 0, "Routine awareness."],
      [2, "Minor", 30, "Monitor gusts around exposed ramp operations."],
      [3, "Moderate", 45, "Prepare for wind-sensitive ramp impacts."],
      [4, "Major", 58, "Limit wind-sensitive exposed operations."],
      [5, "Extreme", 65, "Suspend exposed operations where wind thresholds are exceeded."]
    ]),
    confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS }
  },
  {
    hazardId: "visibility",
    sourceKey: "VISIBILITY",
    displayName: "Visibility",
    operationalRowName: "Visibility",
    operationalMeaning: "Low visibility procedures and airfield movement concerns.",
    enabled: true,
    showOnPartnerDisplay: true,
    alwaysShowOnTimeline: true,
    priority: 3,
    dataSource: "NBM",
    modelVariable: "nbm_visibility",
    units: "SM",
    thresholdDirection: "lessThanOrEqual",
    defaultGeometry: "radius",
    defaultAggregation: "min",
    decisionTriggerText: "Reduced visibility affecting airfield movement",
    noSignalText: "Low visibility procedures are unlikely.",
    impactThresholds: thresholds([
      [1, "Little to None", 6, "No visibility impacts expected."],
      [2, "Minor", 6, "Monitor visibility trends."],
      [3, "Moderate", 3, "Prepare for reduced-visibility impacts."],
      [4, "Major", 1, "Low visibility may affect operations. Coordinate response actions."],
      [5, "Extreme", 0.5, "Significant visibility restrictions possible. Take protective action."]
    ]),
    confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS }
  },
  {
    hazardId: "rainDrainage",
    sourceKey: "RAIN",
    displayName: "Rain",
    operationalRowName: "Rain / Drainage",
    operationalMeaning: "Ramp drainage, ponding, and access road impacts.",
    enabled: true,
    showOnPartnerDisplay: true,
    alwaysShowOnTimeline: true,
    priority: 4,
    dataSource: "NBM",
    modelVariable: "nbm_precip_1hr_mean",
    summaryVariable: "nbm_precip_6hr_qmd",
    units: "in/hr",
    thresholdDirection: "greaterThanOrEqual",
    defaultGeometry: "radius",
    defaultAggregation: "max",
    decisionTriggerText: "Rain rates causing drainage or ponding concerns",
    noSignalText: "Drainage impacts are unlikely.",
    impactThresholds: thresholds([
      [1, "Little to None", 0, "No drainage impacts expected."],
      [2, "Minor", 0.10, "Monitor ponding-prone ramp and access areas."],
      [3, "Moderate", 0.25, "Prepare for ponding/drainage impacts."],
      [4, "Major", 0.50, "Flooding of poor-drainage areas possible. Coordinate response actions."],
      [5, "Extreme", 1.00, "Significant flooding or rapid water issues possible. Take protective action."]
    ]),
    confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS }
  },
  {
    hazardId: "snowWinterOps",
    sourceKey: "SNOW",
    displayName: "Snow",
    operationalRowName: "Snow / Winter Ops",
    operationalMeaning: "Treatment, plowing, and winter staffing impacts.",
    enabled: true,
    showOnPartnerDisplay: true,
    alwaysShowOnTimeline: false,
    priority: 5,
    dataSource: "NBM",
    modelVariable: "nbm_snow_1hr",
    summaryVariable: "nbm_snow_6hr",
    units: "in/hr",
    thresholdDirection: "greaterThanOrEqual",
    defaultGeometry: "radius",
    defaultAggregation: "max",
    decisionTriggerText: "Snow affecting treatment or plowing needs",
    noSignalText: "Winter operations signal is low.",
    impactThresholds: thresholds([
      [1, "Little to None", 0, "Routine awareness."],
      [2, "Minor", 0.01, "Monitor treatment needs."],
      [3, "Moderate", 0.50, "Prepare winter operations staffing."],
      [4, "Major", 1.00, "Stage winter operations resources."],
      [5, "Extreme", 2.00, "Activate full winter operations response."]
    ]),
    confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS }
  },
  {
    hazardId: "freezingRain",
    sourceKey: "FZRA",
    displayName: "Freezing Rain",
    operationalRowName: "Freezing Rain",
    operationalMeaning: "Ice accretion on exposed surfaces and ground operations.",
    enabled: true,
    showOnPartnerDisplay: true,
    alwaysShowOnTimeline: false,
    priority: 6,
    dataSource: "NBM",
    modelVariable: "nbm_fzra_1hr_mean",
    summaryVariable: "nbm_fzra_6hr",
    units: "in",
    thresholdDirection: "greaterThanOrEqual",
    defaultGeometry: "radius",
    defaultAggregation: "max",
    decisionTriggerText: "Freezing rain affecting exposed surfaces",
    noSignalText: "Freezing rain signal is low.",
    impactThresholds: thresholds([
      [1, "Little to None", 0, "Routine awareness."],
      [2, "Minor", 0.001, "Monitor for icing on exposed surfaces."],
      [3, "Moderate", 0.01, "Prepare surface icing response."],
      [4, "Major", 0.10, "Coordinate significant icing response."],
      [5, "Extreme", 0.20, "Activate ice response procedures."]
    ]),
    confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS }
  },
  {
    hazardId: "flashFreeze",
    sourceKey: "FLASH_FREEZE",
    displayName: "Flash Freeze",
    operationalRowName: "Flash Freeze",
    operationalMeaning: "Wet surfaces freezing quickly as temperatures fall.",
    enabled: true,
    showOnPartnerDisplay: true,
    alwaysShowOnTimeline: false,
    priority: 7,
    dataSource: "NBM",
    modelVariable: "nbm_temperature_2m",
    companionVariable: "nbm_precip_1hr_mean",
    units: "degF",
    thresholdDirection: "lessThanOrEqual",
    defaultGeometry: "radius",
    defaultAggregation: "custom",
    decisionTriggerText: "Wet pavement plus falling temperatures",
    noSignalText: "Pavement freeze signal is low.",
    impactThresholds: thresholds([
      [1, "Little to None", 37, "Routine awareness."],
      [2, "Minor", 36, "Monitor wet pavement and temperatures."],
      [3, "Moderate", 32, "Prepare pavement treatment checks."],
      [4, "Major", 28, "Treat pavement trouble spots."],
      [5, "Extreme", 25, "Activate pavement freeze response."]
    ]),
    confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS }
  },
  {
    hazardId: "temperature",
    sourceKey: "TEMPERATURE",
    displayName: "Temperature",
    operationalRowName: "Temperature",
    operationalMeaning: "Heat or cold stress for crews, equipment, and operations.",
    enabled: true,
    showOnPartnerDisplay: true,
    alwaysShowOnTimeline: false,
    priority: 8,
    dataSource: "NBM",
    modelVariable: "nbm_temperature_2m",
    summaryVariable: "nbm_max_temperature_qmd",
    units: "degF",
    thresholdDirection: "outsideRange",
    defaultGeometry: "radius",
    defaultAggregation: "maxOrMinByThreshold",
    decisionTriggerText: "Heat or cold affecting crews and equipment",
    noSignalText: "No temperature action expected.",
    impactThresholds: thresholds([
      [1, "Little to None", "40-90", "Routine awareness."],
      [2, "Minor", "32-95", "Monitor crew exposure."],
      [3, "Moderate", "20-100", "Prepare crew weather precautions."],
      [4, "Major", "10-105", "Use exposure mitigation procedures."],
      [5, "Extreme", "<10 or >105", "Activate temperature safety procedures."]
    ]),
    confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS }
  }
];

function addCompatibilityFields(hazard) {
  const actionTextByImpact = { None: hazard.noSignalText };
  Object.values(hazard.impactThresholds || {}).forEach(item => {
    actionTextByImpact[item.label] = item.action;
  });
  const legacyThresholds = { None: 0 };
  Object.values(hazard.impactThresholds || {}).forEach(item => {
    legacyThresholds[item.label] = item.threshold;
  });
  return {
    ...hazard,
    nbmCatalogKey: hazard.modelVariable,
    actionTextByImpact,
    legacyImpactThresholds: legacyThresholds
  };
}

export const DEFAULT_DSS_CONFIG = {
  partnerProfile: {
    partnerName: "KRNO Airport Operations",
    productTitle: "KRNO Airport Ground Operations DSS",
    eventName: "",
    locationName: "Reno-Tahoe International Airport",
    latitude: 39.4991,
    longitude: -119.7681,
    decisionRadiusNm: 20,
    validPeriodHours: 72,
    timezone: "America/Los_Angeles",
    displayMode: "airport",
    updateFrequencyMinutes: 5,
    contactNotes: ""
  },
  forecastGeometry: {
    mode: "radius",
    point: {
      name: "KRNO",
      lat: 39.4991,
      lon: -119.7681,
      identifier: "KRNO"
    },
    radius: {
      centerName: "KRNO",
      centerLat: 39.4991,
      centerLon: -119.7681,
      radiusValue: 20,
      radiusUnits: "NM",
      areaName: "KRNO 20 NM Decision Area",
      aggregationMethodByHazard: {
        rampLightning: "max",
        airfieldWind: "max",
        visibility: "min",
        rainDrainage: "max",
        snowWinterOps: "max",
        freezingRain: "max",
        flashFreeze: "custom",
        temperature: "maxOrMinByThreshold"
      }
    },
    multiPoint: {
      enabled: false,
      routeName: "",
      corridorBuffer: "",
      points: []
    }
  },
  mapLayers: {
    radar: "IEM_N0Q",
    lightning: "GOES_GLM",
    ringsNm: [10, 20],
    cities: [
      { name: "Truckee", latitude: 39.32796, longitude: -120.18325 },
      { name: "Carson City", latitude: 39.16380, longitude: -119.76740 },
      { name: "Fernley", latitude: 39.60797, longitude: -119.25183 },
      { name: "Incline Village", latitude: 39.24970, longitude: -119.95270 },
      { name: "Fallon", latitude: 39.47353, longitude: -118.77737 }
    ]
  },
  alertSources: ["NWS_ALERTS"],
  riskMatrix: {
    likelihoodBins: {
      "Extremely Unlikely": [0, 10],
      Unlikely: [10, 33],
      "About as Likely as Not": [33, 66],
      Likely: [67, 90],
      "Very Likely": [90, 100]
    },
    matrix: DEFAULT_RISK_MATRIX
  },
  hazards: airportHazards.map(addCompatibilityFields)
};

export function cloneDefaultConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_DSS_CONFIG));
}

export function getDecisionAreaLabel(config = DEFAULT_DSS_CONFIG) {
  const geometry = config.forecastGeometry || DEFAULT_DSS_CONFIG.forecastGeometry;
  if (geometry.mode === "point") return `${geometry.point?.name || config.partnerProfile.locationName} | Point Forecast`;
  if (geometry.mode === "radius") {
    const radius = geometry.radius || {};
    return `${config.partnerProfile.locationName} | ${radius.radiusValue || 20} ${radius.radiusUnits || "NM"} Decision Area`;
  }
  return `${geometry.multiPoint?.routeName || "Route / Corridor"} | Corridor Forecast`;
}

export function getDecisionRadiusNm(config = DEFAULT_DSS_CONFIG) {
  const radius = config.forecastGeometry?.radius;
  if (config.forecastGeometry?.mode === "radius" && radius) {
    if (String(radius.radiusUnits).toLowerCase() === "miles") return Number(radius.radiusValue) * 0.868976;
    if (String(radius.radiusUnits).toLowerCase() === "km") return Number(radius.radiusValue) * 0.539957;
    return Number(radius.radiusValue) || 20;
  }
  return 0;
}

export function getHazardConfig(config, hazardId) {
  return (config?.hazards || []).find(hazard => hazard.hazardId === hazardId);
}

export function getHazardBySource(config, sourceKey) {
  return (config?.hazards || []).find(hazard => {
    if (hazard.sourceKey === sourceKey) return true;
    return Array.isArray(hazard.sourceKeys) && hazard.sourceKeys.includes(sourceKey);
  });
}
