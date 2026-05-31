export const NBM_VARIABLE_CATALOG = [
  { weatherType: "Aviation", variable: "Ceiling", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-36 hrs", "3hr": "36-83 hrs" } },
  { weatherType: "Aviation", variable: "Ceiling <3 kft", product: "Core", fieldOptions: ["Probability"], temporal: { "1hr": "0-36 hrs", "3hr": "36-77 hrs" } },
  { weatherType: "Aviation", variable: "Ceiling <1 kft", product: "Core", fieldOptions: ["Probability"], temporal: { "1hr": "0-36 hrs", "3hr": "36-77 hrs" } },
  { weatherType: "Aviation", variable: "Ceiling <0.5 kft", product: "Core", fieldOptions: ["Probability"], temporal: { "1hr": "0-36 hrs", "3hr": "36-77 hrs" } },
  { weatherType: "Aviation", variable: "Total Cloud Cover", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Aviation", variable: "Mixing Height", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Aviation", variable: "Low Level Turbulence", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-36 hrs", "3hr": "36-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Aviation", variable: "Ellrod Index", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-36 hrs", "3hr": "36-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Fire Weather", variable: "Transport Winds", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Fire Weather", variable: "Ventilation Rate", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-36 hrs", "3hr": "36-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Precipitation", variable: "1 hr Precipitation", product: "Core", fieldOptions: ["Mean", "Probability >0.01 in"], temporal: { "1hr": "0-191 hrs" } },
  { weatherType: "Precipitation", variable: "6 hr Precipitation", product: "Core", fieldOptions: ["Mean", "Probability >0.01 in"], temporal: { "6hr": "11-263 hrs" } },
  { weatherType: "Precipitation", variable: "6 hr Precipitation", product: "QMD", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "6hr": "0-269 hrs" } },
  { weatherType: "Precipitation", variable: "6 hr Precipitation ARI", product: "QMD", fieldOptions: ["Max", "Statistics", "Probability"], temporal: { "6hr": "0-269 hrs" } },
  { weatherType: "Precipitation", variable: "12 hr Precipitation", product: "QMD", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "12hr": "5-269 hrs" } },
  { weatherType: "Precipitation", variable: "24 hr Precipitation", product: "QMD", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "24hr": "24-263 hrs" } },
  { weatherType: "Precipitation", variable: "24 hr Precipitation ARI", product: "Core", fieldOptions: ["Max", "Statistics", "Probability"], temporal: { "24hr": "24-263 hrs" } },
  { weatherType: "Precipitation", variable: "48 hr Precipitation", product: "QMD", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "12hr": "47-95 hrs", "24hr": "95-263 hrs" } },
  { weatherType: "Precipitation", variable: "48 hr Precipitation ARI", product: "Core", fieldOptions: ["Max", "Statistics", "Probability"], temporal: { "12hr": "47-95 hrs", "24hr": "95-263 hrs" } },
  { weatherType: "Precipitation", variable: "72 hr Precipitation", product: "QMD", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "12hr": "71-95 hrs", "24hr": "95-263 hrs" } },
  { weatherType: "Precipitation", variable: "72 hr Precipitation ARI", product: "Core", fieldOptions: ["Max", "Statistics", "Probability"], temporal: { "12hr": "71-95 hrs", "24hr": "95-263 hrs" } },
  { weatherType: "Snow", variable: "1 hr Snowfall", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "1hr": "0-48 hrs" } },
  { weatherType: "Snow", variable: "6 hr Snowfall", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "6hr": "0-263 hrs" } },
  { weatherType: "Snow", variable: "24 hr Snowfall", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "6hr": "0-263 hrs" } },
  { weatherType: "Snow", variable: "48 hr Snowfall", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "6hr": "47-263 hrs" } },
  { weatherType: "Snow", variable: "72 hr Snowfall", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "6hr": "71-263 hrs" } },
  { weatherType: "Snow", variable: "Snow Level", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Snow", variable: "Snow Liquid Ratio", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Freezing Rain", variable: "1 hr Freezing Rain 1:1 Ratio", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-48 hrs" } },
  { weatherType: "Freezing Rain", variable: "6 hr Freezing Rain 1:1 Ratio", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "6hr": "0-263 hrs" } },
  { weatherType: "Convection", variable: "Surface CAPE", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "1hr": "0-36 hrs", "3hr": "36-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Convection", variable: "1 hr Probability of Thunder", product: "Core", fieldOptions: ["Probability"], temporal: { "1hr": "0-36 hrs" } },
  { weatherType: "Convection", variable: "3 hr Probability of Thunder", product: "Core", fieldOptions: ["Probability"], temporal: { "3hr": "0-83 hrs" } },
  { weatherType: "Convection", variable: "3 hr Probability of Dry Thunder", product: "Core", fieldOptions: ["Probability"], temporal: { "3hr": "0-83 hrs" } },
  { weatherType: "Convection", variable: "6 hr Probability of Thunder", product: "Core", fieldOptions: ["Probability"], temporal: { "6hr": "11-191 hrs" } },
  { weatherType: "Convection", variable: "12 hr Probability of Thunder", product: "Core", fieldOptions: ["Probability"], temporal: { "12hr": "17-191 hrs" } },
  { weatherType: "Convection", variable: "Vertically Integrated Liquid", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-48 hrs" } },
  { weatherType: "Convection", variable: "CWASP", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "1hr": "0-36 hrs", "3hr": "36-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Marine", variable: "Freezing Spray", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-36 hrs", "3hr": "36-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Marine", variable: "Significant Wave Height", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: {} },
  { weatherType: "Surface", variable: "Max Temperature 12 hr", product: "Core", fieldOptions: ["Mean", "24 hour Delta"], temporal: { "24hr": "35-251 hrs" } },
  { weatherType: "Surface", variable: "Min Temperature 12 hr", product: "Core", fieldOptions: ["Mean", "24 hour Delta"], temporal: { "24hr": "35-251 hrs" } },
  { weatherType: "Surface", variable: "Max Temperature", product: "QMD", fieldOptions: ["Statistics", "Spread", "Probability", "24 hour Delta"], temporal: { "24hr": "11-251 hrs" } },
  { weatherType: "Surface", variable: "Min Temperature", product: "QMD", fieldOptions: ["Statistics", "Spread", "Probability", "24 hour Delta"], temporal: { "24hr": "11-251 hrs" } },
  { weatherType: "Surface", variable: "2m Temperature", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability", "24 hour Delta"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-251 hrs" } },
  { weatherType: "Surface", variable: "2m Wet Bulb Globe Temperature", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-251 hrs" } },
  { weatherType: "Surface", variable: "2m Apparent Temperature", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-251 hrs" } },
  { weatherType: "Surface", variable: "Solar Radiation", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-251 hrs" } },
  { weatherType: "Dynamics", variable: "10m Wind", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Dynamics", variable: "24 hr Max 10m Wind", product: "QMD", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "24hr": "17-257 hrs" } },
  { weatherType: "Dynamics", variable: "10m Wind Gusts", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Dynamics", variable: "24 hr Max 10m Wind Gust", product: "QMD", fieldOptions: ["Statistics", "Spread", "Probability"], temporal: { "24hr": "17-257 hrs" } },
  { weatherType: "Dynamics", variable: "10m Wind Direction", product: "Core", fieldOptions: ["Mean"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Moisture", variable: "2m Relative Humidity", product: "Core", fieldOptions: ["Mean", "24 hour Delta"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } },
  { weatherType: "Moisture", variable: "2m Max Relative Humidity", product: "Core", fieldOptions: ["Mean", "24 hour Delta"], temporal: { "24hr": "23-239 hrs" } },
  { weatherType: "Moisture", variable: "2m Min Relative Humidity", product: "Core", fieldOptions: ["Mean", "24 hour Delta"], temporal: { "24hr": "11-251 hrs" } },
  { weatherType: "Moisture", variable: "2m Dew Point", product: "Core", fieldOptions: ["Statistics", "Spread", "Probability", "24 hour Delta"], temporal: { "1hr": "0-48 hrs", "3hr": "48-191 hrs", "6hr": "191-263 hrs" } }
];

export function catalogKey(item) {
  return `${item.product}:${item.weatherType}:${item.variable}`;
}

export function catalogLabel(item) {
  const windows = Object.entries(item.temporal || {}).map(([key, value]) => `${key} ${value}`).join(", ");
  return `${item.weatherType} - ${item.variable} (${item.product}${windows ? `; ${windows}` : ""})`;
}

export function getCatalogItem(key) {
  return NBM_VARIABLE_CATALOG.find(item => catalogKey(item) === key) || null;
}
