(function () {
  const layout = document.querySelector(".mapping-layout");
  if (!layout) return;

  const searchInput = document.getElementById("mappingSearch");
  const listElement = document.getElementById("mappingList");
  const identifiersElement = document.getElementById("mappingIdentifiers");
  const titleElement = document.getElementById("mappingTitle");
  const summaryElement = document.getElementById("mappingSummary");
  const identifierElement = document.getElementById("mappingIdentifier");
  const transactionElement = document.getElementById("mappingTransaction");
  const updatedElement = document.getElementById("mappingUpdated");
  const toast = document.getElementById("mappingToast");
  const createButton = document.getElementById("createMapping");
  const updateButton = document.getElementById("updateMapping");
  const deleteButton = document.getElementById("deleteMapping");
  const browseFileButton = document.getElementById("browseMappingFile");
  const mappingFileInput = document.getElementById("mappingFileInput");
  const mappingFileLabel = document.getElementById("mappingFileSelection");
  const nextIssueButton = document.getElementById("nextCanvasIssue");
  const canvasSaveButton = document.getElementById("canvasSaveMapping");
  const canvasSubmitButton = document.getElementById("canvasSubmitMapping");

  const mapper = window.defaultTriageMapper || (window.TriageMapper && window.TriageMapper.create({ initializeDefaults: true }));
  const definitionMap = new Map((window.TriageMapper?.definitions || []).map((def) => [def.id, def]));

  const sampleMaps = [
    {
      id: "837p-professional-core",
      name: "837P Professional Core",
      transaction: "837P",
      summary: "Baseline submission map for professional claims with Triage standard loops.",
      identifier: "MAP-837P-CORE",
      updatedAt: "2024-05-10T10:45:00Z",
      segments: ["ISA", "GS", "ST", "BHT", "NM1", "HL", "SBR", "CLM", "DTP", "REF", "HI", "LX", "SV1", "SE", "GE", "IEA"],
    },
    {
      id: "837i-institutional",
      name: "837I Institutional Routing",
      transaction: "837I",
      summary: "Institutional configuration with revenue code validation and UB-04 loops.",
      identifier: "MAP-837I-UB04",
      updatedAt: "2024-04-18T09:02:00Z",
      segments: ["ISA", "GS", "ST", "BHT", "NM1", "HL", "PAT", "CLM", "DTP", "REF", "HI", "LX", "SV1", "SE", "GE", "IEA"],
    },
    {
      id: "835-remittance-routing",
      name: "835 Remittance Routing",
      transaction: "835",
      summary: "Remittance advice mapping that feeds downstream payment posting.",
      identifier: "MAP-835-REM",
      updatedAt: "2024-03-25T15:20:00Z",
      segments: ["ISA", "GS", "ST", "BPR", "TRN", "DTM", "N1", "REF", "SE", "GE", "IEA"],
    },
  ];

  const mapRecords = new Map(sampleMaps.map((entry) => [entry.id, { ...entry }]));
  let currentMapId = null;

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function showToast(message, tone = "info") {
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast toast--${tone}`;
    toast.hidden = false;
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => {
      toast.hidden = true;
    }, 4000);
  }

  function ensureUniqueId(base) {
    let candidate = base;
    let counter = 1;
    while (mapRecords.has(candidate)) {
      candidate = `${base}-${counter++}`;
    }
    return candidate;
  }

  function slugify(value) {
    return (value || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "map";
  }

  function buildLocalIdentifier(baseName) {
    const cleaned = (baseName || "")
      .toUpperCase()
      .replace(/[^A-Z0-9]+/g, "")
      .slice(0, 12);
    return `LOCAL-${cleaned || "UPLOAD"}`;
  }

  function inferTransaction(content) {
    if (typeof content !== "string" || !content.trim()) return "Custom";
    const match = content.match(/ST\*([A-Z0-9]+)[*~]/i);
    if (match && match[1]) {
      return match[1].toUpperCase();
    }
    return "Custom";
  }

  function updateMappingFileLabel(label) {
    if (!mappingFileLabel) return;
    mappingFileLabel.textContent = label || "No file selected";
  }

  function refreshIssueNavigation() {
    if (!nextIssueButton) return;
    const hasIssues = Boolean(mapper && typeof mapper.hasIssues === "function" && mapper.hasIssues());
    nextIssueButton.disabled = !hasIssues;
  }

  function focusNextCanvasIssue() {
    if (!mapper || typeof mapper.focusNextIssue !== "function") {
      return false;
    }
    return Boolean(mapper.focusNextIssue());
  }

  function initializeIssueObserver() {
    const canvas = mapper?.elements?.canvas;
    if (!canvas || typeof MutationObserver === "undefined") {
      return;
    }
    const observer = new MutationObserver(() => {
      refreshIssueNavigation();
    });
    observer.observe(canvas, { attributes: true, attributeFilter: ["data-has-issues"] });
  }

  function createChip(segmentId) {
    const definition = definitionMap.get(segmentId);
    const span = document.createElement("span");
    span.className = "segment-chip";
    if (definition) {
      span.style.setProperty("--chip-color", definition.color);
      const code = document.createElement("span");
      code.className = "chip-code";
      code.textContent = definition.id;
      const name = document.createElement("span");
      name.className = "chip-name";
      name.textContent = definition.name;
      span.append(code, name);
    } else {
      span.textContent = segmentId;
    }
    return span;
  }

  function renderIdentifierChips(record) {
    identifiersElement.innerHTML = "";
    if (!record) {
      return;
    }
    const fragment = document.createDocumentFragment();
    record.segments.forEach((segment) => {
      const item = document.createElement("li");
      item.appendChild(createChip(segment));
      fragment.appendChild(item);
    });
    identifiersElement.appendChild(fragment);
  }

  function updateMapper(record) {
    if (!mapper) {
      refreshIssueNavigation();
      return;
    }
    if (!record) {
      if (typeof mapper.resetToDefault === "function") {
        mapper.resetToDefault();
      }
      refreshIssueNavigation();
      return;
    }
    if (typeof mapper.setSegmentsByIds === "function") {
      mapper.setSegmentsByIds(record.segments);
    }
    refreshIssueNavigation();
  }

  function renderDetail(record) {
    updateMappingFileLabel(record?.name || "No file selected");
    if (!record) {
      titleElement.textContent = "Select a map file";
      summaryElement.textContent = "Choose a file identifier to view details and load its XML representation.";
      identifierElement.textContent = "—";
      transactionElement.textContent = "—";
      updatedElement.textContent = "—";
      renderIdentifierChips(null);
      updateMapper(null);
      return;
    }

    titleElement.textContent = record.name;
    summaryElement.textContent = record.summary;
    identifierElement.textContent = record.identifier;
    transactionElement.textContent = record.transaction;
    updatedElement.textContent = formatDate(record.updatedAt);
    renderIdentifierChips(record);
    updateMapper(record);
  }

  function renderList() {
    if (!listElement) return;
    const filter = (searchInput?.value || "").trim().toLowerCase();
    listElement.innerHTML = "";

    const entries = Array.from(mapRecords.values()).filter((record) => {
      if (!filter) return true;
      return [record.name, record.identifier, record.transaction]
        .filter(Boolean)
        .some((value) => value.toLowerCase().includes(filter));
    });

    if (!entries.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = filter ? "No maps found for that search." : "No mapping files available.";
      listElement.appendChild(empty);
      return;
    }

    entries
      .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
      .forEach((record) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "mapping-item";
        button.dataset.mapId = record.id;
        button.setAttribute("role", "listitem");
        button.innerHTML = `
          <span class="mapping-item__name">${record.name}</span>
          <span class="mapping-item__meta">${record.identifier} · ${record.transaction}</span>
        `;
        if (record.id === currentMapId) {
          button.classList.add("is-selected");
        }
        button.addEventListener("click", () => selectMap(record.id));
        listElement.appendChild(button);
      });
  }

  function selectMap(mapId) {
    const record = mapRecords.get(mapId);
    currentMapId = record ? record.id : null;
    renderDetail(record);
    renderList();
  }

  function handleCreate() {
    const now = new Date();
    const baseId = ensureUniqueId("custom-map");
    const name = `Custom map ${now.getMonth() + 1}/${now.getDate()}`;
    const segments = currentMapId ? [...(mapRecords.get(currentMapId)?.segments || [])] : (window.TriageMapper?.defaultSegmentIds || []);
    const record = {
      id: baseId,
      name,
      transaction: currentMapId ? mapRecords.get(currentMapId).transaction : "837P",
      summary: currentMapId
        ? `Cloned from ${mapRecords.get(currentMapId).name}`
        : "Draft mapping created from default template.",
      identifier: baseId.toUpperCase().replace(/-/g, "_"),
      updatedAt: now.toISOString(),
      segments: segments.length ? segments : (window.TriageMapper?.defaultSegmentIds || []),
    };
    mapRecords.set(record.id, record);
    selectMap(record.id);
    showToast(`${record.name} created.`, "success");
  }

  function handleUpdate() {
    if (!currentMapId || !mapRecords.has(currentMapId)) {
      showToast("Select a map before updating.", "error");
      return;
    }
    const record = mapRecords.get(currentMapId);
    const now = new Date().toISOString();
    const updatedRecord = {
      ...record,
      updatedAt: now,
      summary: `${record.summary} (revised ${formatDate(now)})`,
      segments: mapper ? mapper.getActiveSegmentIds() : record.segments,
    };
    mapRecords.set(updatedRecord.id, updatedRecord);
    selectMap(updatedRecord.id);
    showToast(`${updatedRecord.name} queued for publication.`, "success");
  }

  function handleSubmitToApi() {
    if (!currentMapId || !mapRecords.has(currentMapId)) {
      showToast("Select a map before submitting.", "error");
      return;
    }
    const record = mapRecords.get(currentMapId);
    const now = new Date().toISOString();
    const updatedRecord = {
      ...record,
      updatedAt: now,
      status: "submitted",
      summary: `${record.summary} (submitted ${formatDate(now)})`,
      segments: mapper ? mapper.getActiveSegmentIds() : record.segments,
    };
    mapRecords.set(updatedRecord.id, updatedRecord);
    selectMap(updatedRecord.id);
    showToast(`${updatedRecord.name} submitted to the worker API.`, "success");
  }

  function handleDelete() {
    if (!currentMapId || !mapRecords.has(currentMapId)) {
      showToast("Select a map to delete.", "error");
      return;
    }
    const record = mapRecords.get(currentMapId);
    mapRecords.delete(currentMapId);
    currentMapId = null;
    renderDetail(null);
    renderList();
    showToast(`${record.name} marked for retirement.`, "info");
  }

  function importMappingFromFile(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const content = typeof reader.result === "string" ? reader.result : "";
      if (mapper && typeof mapper.updateFromContent === "function") {
        mapper.updateFromContent(content);
        refreshIssueNavigation();
      }
      const segments = mapper && typeof mapper.getActiveSegmentIds === "function" ? mapper.getActiveSegmentIds() : [];
      const baseName = file.name.replace(/\.[^.]+$/, "");
      const slugBase = slugify(baseName);
      const id = ensureUniqueId(`upload-${slugBase}`);
      const record = {
        id,
        name: file.name,
        transaction: inferTransaction(content),
        summary: "Loaded from local upload.",
        identifier: buildLocalIdentifier(slugBase || baseName),
        updatedAt: new Date().toISOString(),
        segments,
        source: "local",
      };
      mapRecords.set(record.id, record);
      selectMap(record.id);
      showToast(`${file.name} loaded into the canvas.`, "success");
    };
    reader.onerror = () => {
      showToast("Unable to read the selected file.", "error");
    };
    reader.readAsText(file);
  }

  function handleMappingFileInput(event) {
    const file = event.target?.files?.[0];
    if (!file) return;
    importMappingFromFile(file);
    if (mappingFileInput) {
      mappingFileInput.value = "";
    }
  }

  function handleBrowseFileButton() {
    if (!mappingFileInput) return;
    mappingFileInput.click();
  }

  function initialize() {
    renderList();
    renderDetail(null);
    searchInput?.addEventListener("input", renderList);
    createButton?.addEventListener("click", handleCreate);
    updateButton?.addEventListener("click", handleUpdate);
    canvasSaveButton?.addEventListener("click", handleUpdate);
    canvasSubmitButton?.addEventListener("click", handleSubmitToApi);
    nextIssueButton?.addEventListener("click", () => {
      if (!focusNextCanvasIssue()) {
        showToast("No highlighted issues to review.", "info");
      }
    });
    deleteButton?.addEventListener("click", handleDelete);
    browseFileButton?.addEventListener("click", handleBrowseFileButton);
    mappingFileInput?.addEventListener("change", handleMappingFileInput);

    document.addEventListener("keydown", (event) => {
      if (!event.altKey || !event.shiftKey || event.ctrlKey || event.metaKey) {
        return;
      }
      if ((event.key || "").toLowerCase() !== "e") {
        return;
      }
      const navigated = focusNextCanvasIssue();
      if (navigated) {
        event.preventDefault();
      } else if (!nextIssueButton?.disabled) {
        showToast("No highlighted issues to review.", "info");
      }
    });

    initializeIssueObserver();
    refreshIssueNavigation();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize);
  } else {
    initialize();
  }
})();
