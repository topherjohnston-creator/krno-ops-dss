import {
  DATA_SOURCE_OPTIONS,
  DEFAULT_CONFIDENCE_WEIGHTS,
  IMPACT_CATEGORIES,
  cloneDefaultConfig
} from "./dss-config-schema.js";
import { calculateForecastConfidence } from "./confidence-engine.js";

const storageKey = "krno-partner-dss-admin-config";
let config = loadConfig();
let selectedHazardId = config.hazards[0]?.hazardId;

const profileFields = [
  "partnerName",
  "productTitle",
  "eventName",
  "locationName",
  "latitude",
  "longitude",
  "decisionRadiusNm",
  "validPeriodHours",
  "timezone",
  "displayMode"
];

const hazardFields = [
  "hazardId",
  "displayName",
  "operationalRowName",
  "enabled",
  "showOnPartnerDisplay",
  "priority",
  "dataSource",
  "modelVariable",
  "units",
  "accumulationWindow",
  "forecastWindow",
  "thresholdDirection",
  "backupDataSources",
  "decisionTriggerText",
  "noSignalText"
];

function byId(id) {
  return document.getElementById(id);
}

function loadConfig() {
  try {
    const stored = localStorage.getItem(storageKey);
    return stored ? JSON.parse(stored) : cloneDefaultConfig();
  } catch {
    return cloneDefaultConfig();
  }
}

function saveConfig() {
  localStorage.setItem(storageKey, JSON.stringify(config, null, 2));
  updateJsonOutput();
}

function selectedHazard() {
  return config.hazards.find(hazard => hazard.hazardId === selectedHazardId) || config.hazards[0];
}

function setSelectOptions(select, options) {
  select.innerHTML = "";
  options.forEach(option => {
    const element = document.createElement("option");
    element.value = option;
    element.textContent = option;
    select.appendChild(element);
  });
}

function renderProfile() {
  setSelectOptions(byId("displayMode"), ["airport", "event", "burnScar", "fireWeather", "custom"]);
  profileFields.forEach(field => {
    const element = byId(field);
    if (!element) return;
    element.value = config.partnerProfile[field] ?? "";
    element.oninput = () => {
      const numeric = ["latitude", "longitude", "decisionRadiusNm", "validPeriodHours"].includes(field);
      config.partnerProfile[field] = numeric ? Number(element.value) : element.value;
      renderPreview();
      updateJsonOutput();
    };
  });
}

function renderHazardList() {
  const list = byId("hazard-list");
  list.innerHTML = "";
  config.hazards
    .slice()
    .sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99))
    .forEach(hazard => {
      const button = document.createElement("button");
      button.className = `hazard-button ${hazard.hazardId === selectedHazardId ? "active" : ""}`;
      button.innerHTML = `
        <span>${hazard.operationalRowName || hazard.displayName}</span>
        <span class="pill">${hazard.dataSource}</span>
      `;
      button.addEventListener("click", () => {
        selectedHazardId = hazard.hazardId;
        renderAll();
      });
      list.appendChild(button);
    });
}

function renderDataSourceMapping() {
  const sourceBox = byId("source-options");
  sourceBox.innerHTML = DATA_SOURCE_OPTIONS.map(source => `<span class="pill">${source}</span>`).join("");

  const layerFields = {
    radarLayer: ["mapLayers", "radar"],
    lightningLayer: ["mapLayers", "lightning"]
  };
  Object.entries(layerFields).forEach(([id, [group, key]]) => {
    const element = byId(id);
    element.value = config[group]?.[key] ?? "";
    element.oninput = () => {
      config[group] ||= {};
      config[group][key] = element.value;
      updateJsonOutput();
    };
  });

  const rings = byId("ringsNm");
  rings.value = (config.mapLayers?.ringsNm || []).join(", ");
  rings.oninput = () => {
    config.mapLayers ||= {};
    config.mapLayers.ringsNm = rings.value.split(",").map(value => Number(value.trim())).filter(Number.isFinite);
    updateJsonOutput();
  };

  const alerts = byId("alertSources");
  alerts.value = (config.alertSources || []).join(", ");
  alerts.oninput = () => {
    config.alertSources = alerts.value.split(",").map(value => value.trim()).filter(Boolean);
    updateJsonOutput();
  };

  const cities = byId("cityMarkers");
  cities.value = JSON.stringify(config.mapLayers?.cities || [], null, 2);
  cities.oninput = () => {
    try {
      config.mapLayers ||= {};
      config.mapLayers.cities = JSON.parse(cities.value);
      updateJsonOutput();
    } catch {
      // Keep editing until the JSON is valid.
    }
  };
}

function renderHazardEditor() {
  const hazard = selectedHazard();
  if (!hazard) return;
  setSelectOptions(byId("dataSource"), DATA_SOURCE_OPTIONS);
  hazardFields.forEach(field => {
    const element = byId(field);
    if (!element) return;
    if (field === "backupDataSources") {
      element.value = (hazard.backupDataSources || []).join(", ");
    } else {
      element.value = String(hazard[field] ?? "");
    }
    element.oninput = () => {
      if (field === "backupDataSources") {
        hazard.backupDataSources = element.value.split(",").map(item => item.trim()).filter(Boolean);
      } else if (field === "enabled" || field === "showOnPartnerDisplay") {
        hazard[field] = element.value === "true";
      } else if (field === "priority") {
        hazard[field] = Number(element.value);
      } else {
        hazard[field] = element.value;
        if (field === "hazardId") selectedHazardId = element.value;
      }
      renderHazardList();
      renderPreview();
      updateJsonOutput();
    };
  });
}

function renderThresholds() {
  const hazard = selectedHazard();
  const grid = byId("threshold-grid");
  grid.innerHTML = "";
  IMPACT_CATEGORIES.forEach(category => {
    const row = document.createElement("div");
    row.className = "threshold-row";
    row.innerHTML = `
      <strong>${category}</strong>
      <input value="${hazard.impactThresholds?.[category] ?? ""}">
    `;
    const input = row.querySelector("input");
    input.addEventListener("input", () => {
      hazard.impactThresholds ||= {};
      hazard.impactThresholds[category] = input.value;
      updateJsonOutput();
    });
    grid.appendChild(row);
  });
}

function renderActions() {
  const hazard = selectedHazard();
  const grid = byId("action-grid");
  grid.innerHTML = "";
  IMPACT_CATEGORIES.forEach(category => {
    const row = document.createElement("div");
    row.className = "action-row";
    row.innerHTML = `
      <strong>${category}</strong>
      <textarea>${hazard.actionTextByImpact?.[category] ?? ""}</textarea>
    `;
    const input = row.querySelector("textarea");
    input.addEventListener("input", () => {
      hazard.actionTextByImpact ||= {};
      hazard.actionTextByImpact[category] = input.value;
      renderPreview();
      updateJsonOutput();
    });
    grid.appendChild(row);
  });
}

function renderWeights() {
  const hazard = selectedHazard();
  const grid = byId("weights-grid");
  grid.innerHTML = "";
  const weights = { ...DEFAULT_CONFIDENCE_WEIGHTS, ...(hazard.confidenceWeights || {}) };
  Object.entries(weights).forEach(([key, value]) => {
    const row = document.createElement("div");
    row.className = "weight-row";
    row.innerHTML = `
      <strong>${labelize(key)}</strong>
      <input type="number" min="0" max="1" step="0.01" value="${value}">
    `;
    const input = row.querySelector("input");
    input.addEventListener("input", () => {
      hazard.confidenceWeights ||= {};
      hazard.confidenceWeights[key] = Number(input.value);
      renderConfidencePreview();
      updateJsonOutput();
    });
    grid.appendChild(row);
  });
}

function labelize(value) {
  return String(value)
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, letter => letter.toUpperCase());
}

function renderPreview() {
  const hazard = selectedHazard();
  byId("preview-product").textContent = config.partnerProfile.productTitle || "--";
  byId("preview-hazard").textContent = hazard ? `${hazard.operationalRowName} • ${hazard.dataSource} • ${hazard.modelVariable}` : "--";
  byId("preview-action").textContent = hazard?.actionTextByImpact?.Minor || hazard?.noSignalText || "--";
  renderConfidencePreview();
}

function renderConfidencePreview() {
  const hazard = selectedHazard();
  const box = byId("confidence-components");
  const confidence = calculateForecastConfidence({
    hazardConfig: hazard,
    probability: 18,
    riskCategory: "Minor",
    blocks: [
      { riskCategory: "Little to None" },
      { riskCategory: "Minor" },
      { riskCategory: "Minor" },
      { riskCategory: "Little to None" }
    ],
    dataHealth: { status: "fresh" }
  });
  const components = confidence.components || {};
  box.innerHTML = `
    <div class="preview-card">
      <div class="preview-title">Sample Forecast Confidence</div>
      <div class="preview-value">${confidence.label} (${Math.round(confidence.score)})</div>
    </div>
    ${Object.entries(components).map(([key, component]) => `
      <div>
        <div class="preview-title">${labelize(key)} ${Math.round(component.score)}</div>
        <div class="bar"><span style="width:${Math.max(0, Math.min(100, component.score))}%"></span></div>
      </div>
    `).join("")}
  `;
}

function updateJsonOutput() {
  byId("json-output").value = JSON.stringify(config, null, 2);
}

function exportConfig() {
  const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "partner-dss-config.json";
  link.click();
  URL.revokeObjectURL(url);
}

function importConfig(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      config = JSON.parse(reader.result);
      selectedHazardId = config.hazards?.[0]?.hazardId;
      saveConfig();
      renderAll();
    } catch {
      alert("The selected file is not valid JSON.");
    }
  };
  reader.readAsText(file);
}

function wireButtons() {
  byId("save-config").addEventListener("click", saveConfig);
  byId("export-config").addEventListener("click", exportConfig);
  byId("import-file").addEventListener("change", event => importConfig(event.target.files?.[0]));
  byId("open-preview").addEventListener("click", () => {
    window.open("./krno-nws-idss-partner-tv.html", "_blank", "noopener");
  });
  byId("reset-config").addEventListener("click", () => {
    config = cloneDefaultConfig();
    selectedHazardId = config.hazards[0]?.hazardId;
    saveConfig();
    renderAll();
  });
  byId("json-output").addEventListener("input", event => {
    try {
      const parsed = JSON.parse(event.target.value);
      config = parsed;
      selectedHazardId = config.hazards?.[0]?.hazardId;
      renderAll();
    } catch {
      // Keep editing until JSON becomes valid.
    }
  });
}

function renderAll() {
  renderProfile();
  renderHazardList();
  renderDataSourceMapping();
  renderHazardEditor();
  renderThresholds();
  renderActions();
  renderWeights();
  renderPreview();
  updateJsonOutput();
}

wireButtons();
renderAll();
