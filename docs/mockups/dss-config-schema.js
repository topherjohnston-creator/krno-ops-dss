export const IMPACT_CATEGORIES = [
  "None",
  "Little to None",
  "Minor",
  "Moderate",
  "Major",
  "Extreme"
];

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

export const DATA_SOURCE_OPTIONS = [
  "NBM",
  "HREF",
  "REFS",
  "RRFS",
  "HRRR",
  "OBS",
  "RADAR",
  "MRMS",
  "LIGHTNING",
  "NWS_ALERTS",
  "CUSTOM_JSON"
];

export const DEFAULT_CONFIDENCE_WEIGHTS = {
  probabilitySupport: 0.25,
  runConsistency: 0.25,
  spreadAgreement: 0.20,
  spatialConsistency: 0.15,
  timingPersistence: 0.10,
  dataHealth: 0.05
};

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
    displayMode: "airport"
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
  hazards: [
    {
      hazardId: "rampLightning",
      sourceKey: "LIGHTNING",
      displayName: "Lightning",
      operationalRowName: "Ramp Lightning",
      enabled: true,
      showOnPartnerDisplay: true,
      alwaysShowOnTimeline: true,
      priority: 1,
      dataSource: "NBM",
      backupDataSources: ["REFS", "HREF", "HRRR", "LIGHTNING", "RADAR"],
      modelVariable: "thunder_probability",
      accumulationWindow: "3-hour",
      forecastWindow: "72-hour",
      units: "%",
      thresholdDirection: "greaterThanOrEqual",
      impactThresholds: {
        None: 0,
        "Little to None": 1,
        Minor: 10,
        Moderate: 25,
        Major: 50,
        Extreme: 75
      },
      probabilityThresholds: {
        Minor: 10,
        Moderate: 25,
        Major: 50,
        Extreme: 75
      },
      confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS },
      decisionTriggerText: "Lightning within the 20 NM decision ring",
      noSignalText: "No lightning restrictions expected.",
      actionTextByImpact: {
        None: "No lightning restrictions expected.",
        "Little to None": "Routine awareness.",
        Minor: "Monitor ramp lightning potential.",
        Moderate: "Prepare for ramp lightning procedures.",
        Major: "Consider ramp safety procedures.",
        Extreme: "Suspend exposed ramp operations if lightning is detected nearby."
      }
    },
    {
      hazardId: "airfieldWind",
      sourceKey: "WIND",
      displayName: "Wind",
      operationalRowName: "Airfield Wind",
      enabled: true,
      showOnPartnerDisplay: true,
      alwaysShowOnTimeline: true,
      priority: 2,
      dataSource: "NBM",
      backupDataSources: ["REFS", "HREF", "HRRR", "OBS"],
      modelVariable: "wind_gust",
      accumulationWindow: "instant",
      forecastWindow: "72-hour",
      units: "mph",
      thresholdDirection: "greaterThanOrEqual",
      impactThresholds: {
        None: 0,
        "Little to None": 1,
        Minor: 30,
        Moderate: 45,
        Major: 58,
        Extreme: 65
      },
      probabilityThresholds: {},
      confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS },
      decisionTriggerText: "Gusts affecting ramp or aircraft handling",
      noSignalText: "No ground wind restrictions expected.",
      actionTextByImpact: {
        None: "No ground wind restrictions expected.",
        "Little to None": "Routine awareness.",
        Minor: "Monitor gusts around exposed ramp operations.",
        Moderate: "Prepare for wind-sensitive ramp impacts.",
        Major: "Limit wind-sensitive exposed operations.",
        Extreme: "Suspend exposed operations where wind thresholds are exceeded."
      }
    },
    {
      hazardId: "visibilityCeiling",
      sourceKey: "VISIBILITY",
      displayName: "Visibility",
      operationalRowName: "Visibility / Ceiling",
      enabled: true,
      showOnPartnerDisplay: true,
      alwaysShowOnTimeline: true,
      priority: 3,
      dataSource: "NBM",
      backupDataSources: ["REFS", "HREF", "OBS"],
      modelVariable: "visibility_probability",
      accumulationWindow: "3-hour",
      forecastWindow: "72-hour",
      units: "SM",
      thresholdDirection: "lessThanOrEqual",
      impactThresholds: {
        None: 10,
        "Little to None": 5,
        Minor: 3,
        Moderate: 1,
        Major: 0.5,
        Extreme: 0.25
      },
      probabilityThresholds: {
        Minor: 10,
        Moderate: 25,
        Major: 50,
        Extreme: 75
      },
      confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS },
      decisionTriggerText: "Reduced visibility affecting airfield movement",
      noSignalText: "Low visibility procedures are unlikely.",
      actionTextByImpact: {
        None: "Low visibility procedures are unlikely.",
        "Little to None": "Routine awareness.",
        Minor: "Monitor visibility trends.",
        Moderate: "Prepare for low visibility coordination.",
        Major: "Coordinate low visibility procedures.",
        Extreme: "Use low visibility procedures as required."
      }
    },
    {
      hazardId: "rainDrainage",
      sourceKey: "RAIN",
      displayName: "Rain",
      operationalRowName: "Rain / Drainage",
      enabled: true,
      showOnPartnerDisplay: true,
      alwaysShowOnTimeline: true,
      priority: 4,
      dataSource: "NBM",
      backupDataSources: ["REFS", "HREF", "RADAR", "MRMS"],
      modelVariable: "precipitation_amount",
      accumulationWindow: "6-hour",
      forecastWindow: "72-hour",
      units: "in",
      thresholdDirection: "greaterThanOrEqual",
      impactThresholds: {
        None: 0,
        "Little to None": 0.01,
        Minor: 0.10,
        Moderate: 0.25,
        Major: 0.50,
        Extreme: 1.00
      },
      probabilityThresholds: {
        Minor: 10,
        Moderate: 33,
        Major: 66,
        Extreme: 90
      },
      confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS },
      decisionTriggerText: "Rain rates causing drainage or ponding concerns",
      noSignalText: "Drainage impacts are unlikely.",
      actionTextByImpact: {
        None: "Drainage impacts are unlikely.",
        "Little to None": "Routine awareness.",
        Minor: "Monitor ponding-prone areas.",
        Moderate: "Check drainage trouble spots.",
        Major: "Prepare field drainage response.",
        Extreme: "Respond to field flooding or ponding."
      }
    },
    {
      hazardId: "winterOps",
      sourceKey: "WINTER",
      sourceKeys: ["SNOW", "FZRA"],
      displayName: "Winter Operations",
      operationalRowName: "Winter Ops",
      enabled: true,
      showOnPartnerDisplay: true,
      alwaysShowOnTimeline: false,
      priority: 5,
      dataSource: "NBM",
      backupDataSources: ["REFS", "HREF", "OBS"],
      modelVariable: "snow_fzra_amount",
      accumulationWindow: "1-hour or 6-hour",
      forecastWindow: "72-hour",
      units: "in",
      thresholdDirection: "greaterThanOrEqual",
      impactThresholds: {
        None: 0,
        "Little to None": 0.01,
        Minor: 0.10,
        Moderate: 0.50,
        Major: 1.00,
        Extreme: 2.00
      },
      probabilityThresholds: {
        Minor: 10,
        Moderate: 33,
        Major: 66,
        Extreme: 90
      },
      confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS },
      decisionTriggerText: "Snow or freezing rain affecting treatment needs",
      noSignalText: "Winter operations signal is low.",
      actionTextByImpact: {
        None: "Winter operations signal is low.",
        "Little to None": "Routine awareness.",
        Minor: "Monitor treatment needs.",
        Moderate: "Prepare winter operations staffing.",
        Major: "Stage winter operations resources.",
        Extreme: "Activate full winter operations response."
      }
    },
    {
      hazardId: "flashFreeze",
      sourceKey: "FLASH_FREEZE",
      displayName: "Flash Freeze",
      operationalRowName: "Flash Freeze",
      enabled: true,
      showOnPartnerDisplay: true,
      alwaysShowOnTimeline: false,
      priority: 6,
      dataSource: "NBM",
      backupDataSources: ["OBS", "RADAR", "MRMS"],
      modelVariable: "temperature_wet_surface_proxy",
      accumulationWindow: "3-hour",
      forecastWindow: "72-hour",
      units: "°F",
      thresholdDirection: "lessThanOrEqual",
      impactThresholds: {
        None: 40,
        "Little to None": 36,
        Minor: 32,
        Moderate: 28,
        Major: 25,
        Extreme: 20
      },
      probabilityThresholds: {},
      confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS },
      decisionTriggerText: "Wet pavement plus falling temperatures",
      noSignalText: "Pavement freeze signal is low.",
      actionTextByImpact: {
        None: "Pavement freeze signal is low.",
        "Little to None": "Routine awareness.",
        Minor: "Monitor wet pavement and temperatures.",
        Moderate: "Prepare pavement treatment checks.",
        Major: "Treat pavement trouble spots.",
        Extreme: "Activate pavement freeze response."
      }
    },
    {
      hazardId: "temperature",
      sourceKey: "TEMPERATURE",
      displayName: "Temperature",
      operationalRowName: "Temperature",
      enabled: true,
      showOnPartnerDisplay: true,
      alwaysShowOnTimeline: false,
      priority: 7,
      dataSource: "NBM",
      backupDataSources: ["OBS"],
      modelVariable: "max_min_temperature",
      accumulationWindow: "daily",
      forecastWindow: "72-hour",
      units: "°F",
      thresholdDirection: "outsideRange",
      impactThresholds: {
        None: "40-90",
        "Little to None": "32-95",
        Minor: "20-100",
        Moderate: "10-105",
        Major: "0-110",
        Extreme: "outside major threshold"
      },
      probabilityThresholds: {},
      confidenceWeights: { ...DEFAULT_CONFIDENCE_WEIGHTS },
      decisionTriggerText: "Heat or cold affecting crews and equipment",
      noSignalText: "No temperature action expected.",
      actionTextByImpact: {
        None: "No temperature action expected.",
        "Little to None": "Routine awareness.",
        Minor: "Monitor crew exposure.",
        Moderate: "Prepare crew weather precautions.",
        Major: "Use exposure mitigation procedures.",
        Extreme: "Activate temperature safety procedures."
      }
    }
  ]
};

export function cloneDefaultConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_DSS_CONFIG));
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
