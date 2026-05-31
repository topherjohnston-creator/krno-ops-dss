import {
  CATEGORY_COLORS,
  DEFAULT_DSS_CONFIG,
  PARTNER_IMPACT_LABELS,
  PARTNER_TYPE_TEMPLATES,
  cloneDefaultConfig
} from "./dss-config-schema.js";
import { NBM_FIELD_CATALOG, fieldLabel, getNbmFieldById, recommendedNbmFieldsForHazard } from "./nbm-field-catalog.js";
import { DEFAULT_RISK_MATRIX, LIKELIHOOD_CATEGORIES, getLikelihoodCategory, getRiskCategory } from "./weather-risk-matrix.js";
import { calculateForecastConfidence } from "./confidence-engine.js";

const storageKey = "krno-partner-dss-admin-config";
const steps = [
  "Partner / Event Setup",
  "Forecast Area Setup",
  "Select Partner Type",
  "Select Hazards",
  "Define Impact Levels",
  "Map NBM Variable",
  "Define Partner Actions",
  "Review Risk Matrix",
  "Preview Partner Display",
  "Export / Save JSON"
];

let config = loadConfig();
let currentStep = 0;
let selectedHazardId = firstEnabledHazard()?.hazardId || config.hazards[0]?.hazardId;

function byId(id) {
  return document.getElementById(id);
}

function loadConfig() {
  try {
    const stored = localStorage.getItem(storageKey);
    return stored ? mergeWithDefaults(JSON.parse(stored)) : cloneDefaultConfig();
  } catch {
    return cloneDefaultConfig();
  }
}

function mergeWithDefaults(value) {
  const base = cloneDefaultConfig();
  return {
    ...base,
    ...value,
    partnerProfile: { ...base.partnerProfile, ...(value.partnerProfile || {}) },
    forecastGeometry: {
      ...base.forecastGeometry,
      ...(value.forecastGeometry || {}),
      point: { ...base.forecastGeometry.point, ...(value.forecastGeometry?.point || {}) },
      radius: { ...base.forecastGeometry.radius, ...(value.forecastGeometry?.radius || {}) },
      multiPoint: { ...base.forecastGeometry.multiPoint, ...(value.forecastGeometry?.multiPoint || {}) }
    },
    hazards: Array.isArray(value.hazards) ? value.hazards : base.hazards,
    riskMatrix: value.riskMatrix || base.riskMatrix
  };
}

function saveConfig() {
  localStorage.setItem(storageKey, JSON.stringify(config, null, 2));
  updateJson();
}

function firstEnabledHazard() {
  return (config.hazards || []).find(hazard => hazard.enabled);
}

function selectedHazard() {
  return config.hazards.find(hazard => hazard.hazardId === selectedHazardId) || firstEnabledHazard() || config.hazards[0];
}

function hazardOptions(select) {
  select.innerHTML = "";
  config.hazards.forEach(hazard => {
    const option = document.createElement("option");
    option.value = hazard.hazardId;
    option.textContent = hazard.operationalRowName || hazard.displayName;
    select.appendChild(option);
  });
  select.value = selectedHazard()?.hazardId;
  select.onchange = () => {
    selectedHazardId = select.value;
    renderAll();
  };
}

function bindProfile() {
  const fields = [
    "partnerName",
    "productTitle",
    "eventName",
    "locationName",
    "latitude",
    "longitude",
    "validPeriodHours",
    "timezone",
    "updateFrequencyMinutes",
    "contactNotes"
  ];
  fields.forEach(field => {
    const element = byId(field);
    if (!element) return;
    element.value = config.partnerProfile[field] ?? "";
    element.oninput = () => {
      const numeric = ["latitude", "longitude", "validPeriodHours", "updateFrequencyMinutes"].includes(field);
      config.partnerProfile[field] = numeric ? Number(element.value) : element.value;
      if (field === "latitude") {
        config.forecastGeometry.point.lat = Number(element.value);
        config.forecastGeometry.radius.centerLat = Number(element.value);
      }
      if (field === "longitude") {
        config.forecastGeometry.point.lon = Number(element.value);
        config.forecastGeometry.radius.centerLon = Number(element.value);
      }
      updateJson();
      renderPreview();
    };
  });
}

function renderStepList() {
  const list = byId("step-list");
  list.innerHTML = "";
  steps.forEach((title, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `step-button ${index === currentStep ? "active" : ""}`;
    button.innerHTML = `<span class="step-number">${index + 1}</span><span>${title}</span>`;
    button.addEventListener("click", () => {
      currentStep = index;
      renderAll();
    });
    list.appendChild(button);
  });
}

function renderStepState() {
  document.querySelectorAll(".wizard-step").forEach(step => {
    step.classList.toggle("active", Number(step.dataset.step) === currentStep);
  });
  byId("step-count").textContent = `Step ${currentStep + 1} of ${steps.length}`;
  byId("step-title").textContent = steps[currentStep];
  byId("progress-bar").style.width = `${((currentStep + 1) / steps.length) * 100}%`;
  byId("back-step").disabled = currentStep === 0;
  byId("next-step").textContent = currentStep === steps.length - 1 ? "Finish" : "Next";
}

function renderGeometry() {
  const choices = [
    ["point", "Point Forecast", "Best for a single venue, facility, station, airport reference point, or burn scar point."],
    ["radius", "Area / Radius Forecast", "Best when the partner cares about a decision area around one location."],
    ["multiPoint", "Multi-Point Route / Corridor Forecast — Coming Later", "Scaffolded for road corridors, evacuation routes, mountain passes, and event access routes.", true]
  ];
  const container = byId("geometry-choices");
  container.innerHTML = "";
  choices.forEach(([mode, title, body, disabled]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `choice-card ${config.forecastGeometry.mode === mode ? "active" : ""} ${disabled ? "disabled" : ""}`;
    button.innerHTML = `<div class="choice-title">${title}</div><p>${body}</p>`;
    button.disabled = Boolean(disabled);
    button.addEventListener("click", () => {
      config.forecastGeometry.mode = mode;
      renderGeometry();
      updateJson();
    });
    container.appendChild(button);
  });
  renderGeometryEditor();
}

function renderGeometryEditor() {
  const editor = byId("geometry-editor");
  const geometry = config.forecastGeometry;
  if (geometry.mode === "point") {
    editor.innerHTML = `
      <div class="form-grid three">
        <label><span>Location Name</span><input id="point-name"></label>
        <label><span>Latitude</span><input id="point-lat" type="number" step="0.0001"></label>
        <label><span>Longitude</span><input id="point-lon" type="number" step="0.0001"></label>
      </div>
      <div class="helper-card">Calculation: nearest NBM grid point or interpolated value at the partner location.</div>
    `;
    bindObjectInput("point-name", geometry.point, "name");
    bindObjectInput("point-lat", geometry.point, "lat", true);
    bindObjectInput("point-lon", geometry.point, "lon", true);
    return;
  }
  editor.innerHTML = `
    <div class="form-grid three">
      <label><span>Area Name</span><input id="radius-name"></label>
      <label><span>Center Latitude</span><input id="radius-lat" type="number" step="0.0001"></label>
      <label><span>Center Longitude</span><input id="radius-lon" type="number" step="0.0001"></label>
      <label><span>Radius</span><input id="radius-value" type="number" min="1"></label>
      <label><span>Units</span><select id="radius-units"><option>NM</option><option>miles</option><option>km</option></select></label>
      <label><span>Default Aggregation</span><select id="radius-default-aggregation"><option>max</option><option>min</option><option>mean</option><option>any</option><option>nearest</option><option>custom</option></select></label>
    </div>
    <div class="helper-card">Calculation: sample NBM grid points inside the configured radius. Hazard-specific aggregation is stored in the exported JSON.</div>
  `;
  bindObjectInput("radius-name", geometry.radius, "areaName");
  bindObjectInput("radius-lat", geometry.radius, "centerLat", true);
  bindObjectInput("radius-lon", geometry.radius, "centerLon", true);
  bindObjectInput("radius-value", geometry.radius, "radiusValue", true);
  bindObjectInput("radius-units", geometry.radius, "radiusUnits");
  const defaultAgg = byId("radius-default-aggregation");
  defaultAgg.value = "max";
  defaultAgg.onchange = () => updateJson();
}

function bindObjectInput(id, object, key, numeric = false) {
  const element = byId(id);
  element.value = object[key] ?? "";
  element.oninput = () => {
    object[key] = numeric ? Number(element.value) : element.value;
    updateJson();
    renderPreview();
  };
}

function renderPartnerTypes() {
  const container = byId("partner-type-choices");
  container.innerHTML = "";
  Object.entries(PARTNER_TYPE_TEMPLATES).forEach(([key, template]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `choice-card ${config.partnerProfile.displayMode === key ? "active" : ""}`;
    button.innerHTML = `<div class="choice-title">${template.label}</div><p>Preloads: ${template.hazards.length ? template.hazards.map(hazardName).join(", ") : "start from a blank hazard set"}</p>`;
    button.addEventListener("click", () => {
      config.partnerProfile.displayMode = key;
      if (key !== "custom") {
        const enabled = new Set(template.hazards);
        config.hazards.forEach(hazard => {
          hazard.enabled = enabled.has(hazard.hazardId);
          hazard.showOnPartnerDisplay = hazard.enabled;
        });
        selectedHazardId = firstEnabledHazard()?.hazardId || config.hazards[0]?.hazardId;
      }
      renderAll();
      updateJson();
    });
    container.appendChild(button);
  });
}

function hazardName(hazardId) {
  return config.hazards.find(hazard => hazard.hazardId === hazardId)?.displayName || hazardId;
}

function renderHazardSelection() {
  const grid = byId("hazard-select-grid");
  grid.innerHTML = "";
  config.hazards.forEach(hazard => {
    const fields = recommendedNbmFieldsForHazard(hazard.hazardId);
    const card = document.createElement("article");
    card.className = `hazard-card ${hazard.enabled ? "enabled" : ""}`;
    card.innerHTML = `
      <div class="hazard-title">${hazard.operationalRowName}</div>
      <p>${hazard.operationalMeaning}</p>
      <div class="hazard-meta">
        <span class="pill">${fields[0] ? fieldLabel(fields[0]) : "NBM support limited"}</span>
        <span class="pill">${hazard.defaultGeometry === "radius" ? "Area / Radius" : "Point"}</span>
        <span class="pill">${hazard.defaultAggregation}</span>
      </div>
      <button type="button">${hazard.enabled ? "Enabled" : "Enable"}</button>
    `;
    card.querySelector("button").addEventListener("click", () => {
      hazard.enabled = !hazard.enabled;
      hazard.showOnPartnerDisplay = hazard.enabled;
      renderHazardSelection();
      renderPreview();
      updateJson();
    });
    grid.appendChild(card);
  });
}

function renderImpactEditor() {
  const select = byId("threshold-hazard");
  hazardOptions(select);
  const hazard = selectedHazard();
  byId("threshold-helper").textContent = `${hazard.operationalMeaning} Threshold direction: ${hazard.thresholdDirection}.`;
  const table = byId("impact-table");
  table.innerHTML = "";
  Object.entries(PARTNER_IMPACT_LABELS).forEach(([level, label]) => {
    const item = hazard.impactThresholds[level] || { label, threshold: "", description: "", action: "" };
    const row = document.createElement("div");
    row.className = "impact-row";
    row.innerHTML = `
      <div class="impact-name">Impact Level ${level}<br><span class="small-label">${label}</span></div>
      <input aria-label="Threshold for Impact Level ${level}" value="${item.threshold ?? ""}">
      <input aria-label="Operational description for Impact Level ${level}" value="${item.description ?? ""}">
      <input aria-label="Example action for Impact Level ${level}" value="${item.action ?? ""}">
    `;
    const [thresholdInput, descriptionInput, actionInput] = row.querySelectorAll("input");
    thresholdInput.oninput = () => updateImpact(level, "threshold", thresholdInput.value);
    descriptionInput.oninput = () => updateImpact(level, "description", descriptionInput.value);
    actionInput.oninput = () => updateImpact(level, "action", actionInput.value);
    table.appendChild(row);
  });
}

function updateImpact(level, key, value) {
  const hazard = selectedHazard();
  hazard.impactThresholds[level] ||= { label: PARTNER_IMPACT_LABELS[level], threshold: "", action: "", description: "" };
  hazard.impactThresholds[level][key] = value;
  refreshCompatibility(hazard);
  renderPreview();
  updateJson();
}

function refreshCompatibility(hazard) {
  hazard.actionTextByImpact = { None: hazard.noSignalText };
  Object.values(hazard.impactThresholds || {}).forEach(item => {
    hazard.actionTextByImpact[item.label] = item.action;
  });
  hazard.nbmCatalogKey = hazard.modelVariable;
}

function renderNbmMapping() {
  const select = byId("mapping-hazard");
  hazardOptions(select);
  const hazard = selectedHazard();
  const fieldSelect = byId("nbm-field");
  const recommended = recommendedNbmFieldsForHazard(hazard.hazardId);
  const all = [...recommended, ...NBM_FIELD_CATALOG.filter(field => !recommended.includes(field))];
  fieldSelect.innerHTML = "";
  all.forEach(field => {
    const option = document.createElement("option");
    option.value = field.id;
    option.textContent = `${recommended.includes(field) ? "Recommended: " : ""}${fieldLabel(field)}`;
    fieldSelect.appendChild(option);
  });
  fieldSelect.value = hazard.modelVariable;
  fieldSelect.onchange = () => {
    const field = getNbmFieldById(fieldSelect.value);
    hazard.modelVariable = fieldSelect.value;
    hazard.nbmCatalogKey = fieldSelect.value;
    hazard.units = field?.units || hazard.units;
    hazard.defaultAggregation = field?.defaultAggregation || hazard.defaultAggregation;
    renderNbmMapping();
    updateJson();
  };

  const field = getNbmFieldById(hazard.modelVariable);
  byId("field-detail").textContent = field
    ? `${field.fieldName} • ${field.fieldOptions.join(", ")} • ${field.supportedWindows.join(", ")} • Forecast hours ${field.forecastHours}. ${field.notes}`
    : "Choose an NBM field.";
  setSelect("aggregation-method", ["max", "min", "mean", "percentExceeding", "any", "nearest", "custom"], hazard.defaultAggregation, value => {
    hazard.defaultAggregation = value;
    config.forecastGeometry.radius.aggregationMethodByHazard[hazard.hazardId] = value;
    updateJson();
  });
  setSelect("threshold-direction", ["greaterThanOrEqual", "lessThanOrEqual", "outsideRange"], hazard.thresholdDirection, value => {
    hazard.thresholdDirection = value;
    updateJson();
  });
  const units = byId("units");
  units.value = hazard.units || "";
  units.oninput = () => {
    hazard.units = units.value;
    updateJson();
  };
}

function setSelect(id, options, value, onChange) {
  const select = byId(id);
  select.innerHTML = "";
  options.forEach(option => {
    const element = document.createElement("option");
    element.value = option;
    element.textContent = option;
    select.appendChild(element);
  });
  select.value = value;
  select.onchange = () => onChange(select.value);
}

function renderActionEditor() {
  const select = byId("action-hazard");
  hazardOptions(select);
  const hazard = selectedHazard();
  byId("action-helper").textContent = hazard.decisionTriggerText;
  const table = byId("action-table");
  table.innerHTML = "";
  Object.entries(PARTNER_IMPACT_LABELS).forEach(([level, label]) => {
    const item = hazard.impactThresholds[level] || { label, action: "" };
    const row = document.createElement("div");
    row.className = "action-row";
    row.innerHTML = `
      <div class="impact-name">Impact Level ${level}<br><span class="small-label">${label}</span></div>
      <textarea>${item.action ?? ""}</textarea>
    `;
    const textarea = row.querySelector("textarea");
    textarea.oninput = () => updateImpact(level, "action", textarea.value);
    table.appendChild(row);
  });
}

function renderRiskMatrix() {
  const matrix = byId("risk-matrix");
  matrix.innerHTML = `<div></div>${[1, 2, 3, 4, 5].map(level => `<div class="matrix-head">Impact ${level}<br>${PARTNER_IMPACT_LABELS[level]}</div>`).join("")}`;
  LIKELIHOOD_CATEGORIES.slice().reverse().forEach(likelihood => {
    const label = document.createElement("div");
    label.className = "matrix-row-label";
    label.textContent = likelihood;
    matrix.appendChild(label);
    [1, 2, 3, 4, 5].forEach(level => {
      const category = DEFAULT_RISK_MATRIX[level][likelihood];
      const cell = document.createElement("div");
      cell.className = "matrix-cell";
      cell.style.background = CATEGORY_COLORS[category];
      cell.textContent = category;
      matrix.appendChild(cell);
    });
  });
  const impact = byId("matrix-impact");
  impact.innerHTML = [1, 2, 3, 4, 5].map(level => `<option value="${level}">Impact Level ${level} - ${PARTNER_IMPACT_LABELS[level]}</option>`).join("");
  impact.onchange = renderMatrixResult;
  byId("matrix-probability").oninput = renderMatrixResult;
  renderMatrixResult();
}

function renderMatrixResult() {
  const impactLevel = Number(byId("matrix-impact").value || 2);
  const probability = Number(byId("matrix-probability").value || 0);
  const likelihood = getLikelihoodCategory(probability);
  const risk = getRiskCategory(impactLevel, probability);
  byId("matrix-result").innerHTML = `
    <div class="small-label">Calculated Result</div>
    <h3>${risk}</h3>
    <p>NBM probability ${probability}% is ${likelihood}. Combined with Impact Level ${impactLevel}, the partner-facing category is ${risk}.</p>
  `;
}

function renderPreview() {
  const active = config.hazards.filter(hazard => hazard.enabled && hazard.showOnPartnerDisplay);
  const primary = active[0] || config.hazards[0];
  const confidence = calculateForecastConfidence({
    probability: 18,
    riskCategory: "Minor",
    hazardConfig: primary,
    blocks: [{ riskCategory: "Minor" }, { riskCategory: "Minor" }, { riskCategory: "Little to None" }],
    dataHealth: { status: "fresh" }
  });
  byId("partner-preview").innerHTML = `
    <div class="preview-banner">
      <h3>${primary.impactThresholds?.["2"]?.action || "Monitor partner weather potential."}</h3>
      <p>Minor • ${primary.displayName} • Peak Sun 2-8 PM • ${decisionAreaText()} • Confidence ${confidence.label}</p>
    </div>
    <div class="preview-grid">
      <div class="preview-card"><div class="small-label">Hazard / Impact</div><p>${primary.operationalMeaning}</p></div>
      <div class="preview-card"><div class="small-label">Timing</div><p>Highest concern Sun 2-8 PM.</p></div>
      <div class="preview-card"><div class="small-label">Action / Confidence</div><p>${primary.impactThresholds?.["2"]?.action || "Monitor trend."} Confidence: ${confidence.label}.</p></div>
    </div>
  `;
}

function decisionAreaText() {
  if (config.forecastGeometry.mode === "point") return "Point Forecast";
  const radius = config.forecastGeometry.radius;
  return `${radius.radiusValue} ${radius.radiusUnits} Decision Area`;
}

function updateJson() {
  const output = byId("json-output");
  if (output) output.value = JSON.stringify(config, null, 2);
}

function downloadJson() {
  const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${config.partnerProfile.productTitle.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}-config.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function importJson(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      config = mergeWithDefaults(JSON.parse(reader.result));
      selectedHazardId = firstEnabledHazard()?.hazardId || config.hazards[0]?.hazardId;
      saveConfig();
      renderAll();
    } catch {
      alert("Unable to import this JSON file.");
    }
  };
  reader.readAsText(file);
}

function bindChrome() {
  byId("back-step").onclick = () => {
    currentStep = Math.max(0, currentStep - 1);
    renderAll();
  };
  byId("next-step").onclick = () => {
    currentStep = Math.min(steps.length - 1, currentStep + 1);
    renderAll();
  };
  ["save-draft", "save-draft-bottom"].forEach(id => {
    const button = byId(id);
    if (button) button.onclick = saveConfig;
  });
  ["export-json", "export-json-bottom"].forEach(id => {
    const button = byId(id);
    if (button) button.onclick = downloadJson;
  });
  ["preview-display", "preview-display-bottom"].forEach(id => {
    const button = byId(id);
    if (button) button.onclick = () => {
      saveConfig();
      window.open("./krno-nws-idss-partner-tv.html", "_blank");
    };
  });
  byId("reset-defaults").onclick = () => {
    config = cloneDefaultConfig();
    selectedHazardId = firstEnabledHazard()?.hazardId || config.hazards[0]?.hazardId;
    saveConfig();
    renderAll();
  };
  byId("import-json").onchange = event => {
    const file = event.target.files?.[0];
    if (file) importJson(file);
  };
}

function renderAll() {
  renderStepState();
  renderStepList();
  bindProfile();
  renderGeometry();
  renderPartnerTypes();
  renderHazardSelection();
  renderImpactEditor();
  renderNbmMapping();
  renderActionEditor();
  renderRiskMatrix();
  renderPreview();
  updateJson();
}

bindChrome();
renderAll();
