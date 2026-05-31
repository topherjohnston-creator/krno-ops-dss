import { DEFAULT_DSS_CONFIG } from "./dss-config-schema.js";

export const KRNO_OPERATIONAL_CONFIG = {
  ...DEFAULT_DSS_CONFIG,
  partnerProfile: {
    ...DEFAULT_DSS_CONFIG.partnerProfile,
    partnerName: "KRNO Airport Operations",
    productTitle: "KRNO Airport Ground Operations DSS",
    locationName: "Reno-Tahoe International Airport",
    airportId: "KRNO",
    latitude: 39.4991,
    longitude: -119.7681,
    decisionRadiusNm: 20,
    validPeriodHours: 72,
    timezone: "America/Los_Angeles",
    displayMode: "airport"
  },
  forecastGeometry: {
    ...DEFAULT_DSS_CONFIG.forecastGeometry,
    mode: "radius",
    radius: {
      ...DEFAULT_DSS_CONFIG.forecastGeometry.radius,
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
    }
  }
};
