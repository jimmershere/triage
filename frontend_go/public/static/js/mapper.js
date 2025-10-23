(function () {
  const GRID_COLUMNS = 80;
  const SEGMENT_DEFINITIONS = [
    { id: "ISA", name: "Interchange Control Header", color: "#2563eb" },
    { id: "GS", name: "Functional Group Header", color: "#7c3aed" },
    { id: "ST", name: "Transaction Set Header", color: "#f97316" },
    { id: "BHT", name: "Beginning of Hierarchical Transaction", color: "#d946ef" },
    { id: "BPR", name: "Financial Information", color: "#fb923c" },
    { id: "NM1", name: "Individual or Organizational Name", color: "#14b8a6" },
    { id: "HL", name: "Hierarchical Level", color: "#0ea5e9" },
    { id: "SBR", name: "Subscriber Information", color: "#facc15" },
    { id: "PAT", name: "Patient Information", color: "#22c55e" },
    { id: "CLM", name: "Claim Information", color: "#fb7185" },
    { id: "DTP", name: "Date or Time Reference", color: "#38bdf8" },
    { id: "DTM", name: "Date/Time Reference", color: "#0ea5e9" },
    { id: "REF", name: "Reference Information", color: "#f59e0b" },
    { id: "HI", name: "Health Care Diagnosis Code", color: "#a855f7" },
    { id: "LX", name: "Service Line Number", color: "#f97316" },
    { id: "SV1", name: "Professional Service", color: "#8b5cf6" },
    { id: "TRN", name: "Trace Number", color: "#0ea5e9" },
    { id: "N1", name: "Party Identification", color: "#06b6d4" },
    { id: "SE", name: "Transaction Set Trailer", color: "#2563eb" },
    { id: "GE", name: "Functional Group Trailer", color: "#0891b2" },
    { id: "IEA", name: "Interchange Control Trailer", color: "#0f766e" },
  ];

  const SEGMENT_SAMPLE_CONTENT = {
    ISA: "ISA*00*          *00*          *ZZ*SUBMITTER*ZZ*RECEIVER*230101*1234*^*00501*000000905*0*T*:~",
    GS: "GS*HC*SUBMITTER*RECEIVER*20230101*1234*1*X*005010X222A1~",
    ST: "ST*837*0001*005010X222A1~",
    BHT: "BHT*0019*00*0123*20230101*1234*CH~",
    NM1: "NM1*41*2*SUBMITTER*****46*123456789~",
    HL: "HL*1**20*1~",
    SBR: "SBR*P*18*******CI~",
    PAT: "PAT*19~",
    CLM: "CLM*123456789*100***11:B:1*Y*A*Y*I*P~",
    DTP: "DTP*434*RD8*20230101-20230101~",
    DTM: "DTM*111*20230101~",
    REF: "REF*G1*REF12345~",
    HI: "HI*ABK:Z1234~",
    LX: "LX*1~",
    SV1: "SV1*HC:99213*100*UN*1***1~",
    BPR: "BPR*I*534.54*C*ACH*CCP*01*123456789*DA*987654321*1234567890**01*999999999*DA*888888888*20230101~",
    TRN: "TRN*1*1099999990*9876543210*1512345678~",
    N1: "N1*PR*HEDI HEALTH PLAN*FI*999999999~",
    SE: "SE*23*0001~",
    GE: "GE*1*1~",
    IEA: "IEA*1*000000905~",
  };

  const DEFAULT_SEGMENTS = [
    "ISA",
    "GS",
    "ST",
    "BHT",
    "NM1",
    "HL",
    "SBR",
    "PAT",
    "CLM",
    "DTP",
    "REF",
    "HI",
    "LX",
    "SV1",
    "SE",
    "GE",
    "IEA",
  ];

  function resolveElement(reference) {
    if (!reference) return null;
    if (reference instanceof Element) return reference;
    if (typeof reference === "string") {
      return document.querySelector(reference);
    }
    return null;
  }

  function createSegmentChip(segment) {
    const chip = document.createElement("span");
    chip.className = "segment-chip";
    chip.style.setProperty("--chip-color", segment.color);

    const code = document.createElement("span");
    code.className = "chip-code";
    code.textContent = segment.id;

    const name = document.createElement("span");
    name.className = "chip-name";
    name.textContent = segment.name;

    chip.append(code, name);
    chip.title = `${segment.id} — ${segment.name}`;
    return chip;
  }

  let segmentCounter = 0;

  function getDefaultFieldsForSegment(segmentId) {
    const sample = SEGMENT_SAMPLE_CONTENT[segmentId];
    if (!sample) {
      return [""];
    }
    const trimmed = sample.replace(/~\s*$/, "");
    const parts = trimmed.split("*");
    parts.shift();
    return parts.length ? parts : [""];
  }

  function parseLineToFields(line) {
    if (typeof line !== "string") return null;
    const cleaned = line.trim();
    if (!cleaned) return null;
    const withoutTerminator = cleaned.replace(/~\s*$/, "");
    const parts = withoutTerminator.split("*");
    if (!parts.length) return null;
    const id = (parts.shift() || "").trim().toUpperCase();
    if (!id) return null;
    return {
      id,
      fields: parts.map((part) => part.trim()),
    };
  }

  function createSegmentState(definition, fields) {
    const data = Array.isArray(fields) && fields.length ? fields.slice() : getDefaultFieldsForSegment(definition.id);
    return {
      key: `${definition.id}-${Date.now()}-${segmentCounter++}`,
      id: definition.id,
      definition,
      fields: data,
    };
  }

  function createBoundaryLabel(open = true) {
    const label = document.createElement("div");
    label.className = "segment-boundary";
    label.textContent = open ? "<transactionSet>" : "</transactionSet>";
    return label;
  }

  function createSegmentRow(segment, onFieldChange) {
    const row = document.createElement("div");
    row.className = "segment-row";
    row.dataset.segmentId = segment.id;
    row.dataset.segmentKey = segment.key;

    const header = document.createElement("div");
    header.className = "segment-row__header";
    const chip = createSegmentChip(segment.definition);
    chip.classList.add("grid-chip");
    chip.setAttribute("aria-label", `${segment.id} segment identifier`);
    header.appendChild(chip);
    row.appendChild(header);

    const fieldsWrapper = document.createElement("div");
    fieldsWrapper.className = "segment-row__fields";
    const fields = segment.fields.length ? segment.fields : [""];
    fields.forEach((value, index) => {
      const field = document.createElement("label");
      field.className = "segment-field";
      field.dataset.segmentId = segment.id;
      field.dataset.fieldIndex = String(index);

      const text = document.createElement("span");
      text.className = "segment-field__code";
      text.textContent = `${segment.id}${String(index + 1).padStart(2, "0")}`;

      const input = document.createElement("input");
      input.type = "text";
      input.className = "segment-field__input";
      input.value = value || "";
      input.placeholder = "…";
      input.addEventListener("input", (event) => {
        onFieldChange(segment, index, event.target.value);
      });

      field.append(text, input);
      fieldsWrapper.appendChild(field);
    });

    row.appendChild(fieldsWrapper);
    return row;
  }

  function parseSegmentsFromContent(raw) {
    if (typeof raw !== "string") return [];
    return raw
      .split("~")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => line.split("*")[0].trim().toUpperCase())
      .filter(Boolean)
      .filter((segment) => SEGMENT_DEFINITIONS.some((def) => def.id === segment));
  }

  function createMapper(options = {}) {
    const paletteEl = resolveElement(options.palette || "#segments");
    const canvasEl = resolveElement(options.canvas || "#canvas");
    const emptyStateEl = resolveElement(options.emptyState || "#canvasEmpty");

    if (!paletteEl || !canvasEl || !emptyStateEl) {
      console.warn("HEDI mapping interface not initialised — elements missing");
      return null;
    }

    const state = {
      activeSegments: [],
      paletteInitialised: false,
    };

    function findSegment(segmentId) {
      return SEGMENT_DEFINITIONS.find((segment) => segment.id === segmentId);
    }

    function emitChange(detail = {}) {
      const event = new CustomEvent("mapper:change", {
        detail: {
          segments: getSegmentData(),
          ...detail,
        },
      });
      canvasEl.dispatchEvent(event);
      if (typeof options.onChange === "function") {
        options.onChange(getSegmentData());
      }
    }

    function getSegmentData() {
      return state.activeSegments.map((segment) => ({
        id: segment.id,
        fields: segment.fields.slice(),
      }));
    }

    function handleFieldChange(segment, index, value) {
      if (!segment) return;
      segment.fields[index] = value;
      emitChange({ segmentId: segment.id, fieldIndex: index, value });
    }

    function renderPalette() {
      if (state.paletteInitialised) {
        paletteEl.querySelectorAll("li").forEach((item) => item.remove());
      }
      const frag = document.createDocumentFragment();
      SEGMENT_DEFINITIONS.forEach((segment) => {
        const item = document.createElement("li");
        item.appendChild(createSegmentChip(segment));
        item.draggable = true;
        item.style.listStyle = "none";
        item.addEventListener("dragstart", (event) => {
          event.dataTransfer.setData("text/plain", segment.id);
          event.dataTransfer.effectAllowed = "copy";
        });
        frag.appendChild(item);
      });
      paletteEl.appendChild(frag);
      state.paletteInitialised = true;
    }

    function renderCanvas() {
      canvasEl.replaceChildren();

      if (!state.activeSegments.length) {
        emptyStateEl.hidden = false;
        return;
      }

      emptyStateEl.hidden = true;
      canvasEl.appendChild(createBoundaryLabel(true));
      state.activeSegments.forEach((segment) => {
        canvasEl.appendChild(createSegmentRow(segment, handleFieldChange));
      });
      canvasEl.appendChild(createBoundaryLabel(false));
    }

    function setSegmentsByIds(ids) {
      state.activeSegments = ids
        .map((segmentId) => {
          const definition = findSegment(segmentId);
          if (!definition) return null;
          return createSegmentState(definition);
        })
        .filter(Boolean);
      renderCanvas();
      emitChange({ reason: "set-segments" });
    }

    function appendSegmentById(segmentId) {
      const definition = findSegment(segmentId);
      if (!definition) return;
      state.activeSegments.push(createSegmentState(definition));
      renderCanvas();
      emitChange({ reason: "append", segmentId });
    }

    function resetToDefault() {
      setSegmentsByIds(DEFAULT_SEGMENTS);
    }

    function getActiveSegmentIds() {
      return state.activeSegments.map((segment) => segment.id);
    }

    function initDragAndDrop() {
      canvasEl.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        canvasEl.classList.add("dragover");
      });

      canvasEl.addEventListener("dragleave", (event) => {
        if (!canvasEl.contains(event.relatedTarget)) {
          canvasEl.classList.remove("dragover");
        }
      });

      canvasEl.addEventListener("drop", (event) => {
        event.preventDefault();
        canvasEl.classList.remove("dragover");
        const segmentId = (event.dataTransfer.getData("text/plain") || "").trim().toUpperCase();
        appendSegmentById(segmentId);
      });
    }

    function updateFromContent(rawContent) {
      if (typeof rawContent !== "string") {
        state.activeSegments = [];
        renderCanvas();
        emitChange({ reason: "reset" });
        return;
      }

      const tokens = rawContent
        .split(/~/)
        .map((token) => token.trim())
        .filter(Boolean);

      const parsed = tokens
        .map((token) => parseLineToFields(`${token}~`))
        .filter(Boolean)
        .map(({ id, fields }) => {
          const definition = findSegment(id);
          if (!definition) return null;
          return createSegmentState(definition, fields);
        })
        .filter(Boolean);

      state.activeSegments = parsed;
      renderCanvas();
      emitChange({ reason: "content-update" });
    }

    renderPalette();
    initDragAndDrop();
    if (options.initializeDefaults !== false) {
      resetToDefault();
    } else {
      renderCanvas();
    }

    return {
      renderPalette,
      renderCanvas,
      appendSegmentById,
      setSegmentsByIds,
      resetToDefault,
      getActiveSegmentIds,
      updateFromContent,
      getSegmentData,
      exportContent() {
        return state.activeSegments
          .map((segment) => {
            const sanitized = segment.fields.map((field) => (field ?? "").trim());
            const joined = sanitized.join("*");
            return joined ? `${segment.id}*${joined}~` : `${segment.id}~`;
          })
          .join("\n");
      },
      setInteractivity(enabled) {
        const disable = enabled === false;
        canvasEl.classList.toggle("mapper-disabled", disable);
        paletteEl.classList.toggle("mapper-disabled", disable);
        canvasEl.querySelectorAll("input.segment-field__input").forEach((input) => {
          input.disabled = disable;
        });
      },
      elements: { palette: paletteEl, canvas: canvasEl, empty: emptyStateEl },
    };
  }

  function autoInit() {
    if (window.HediMapper && window.HediMapper.autoInitialised) {
      return;
    }
    const mapper = createMapper();
    if (mapper) {
      window.defaultHediMapper = mapper;
    }
    if (!window.HediMapper) {
      window.HediMapper = {};
    }
    window.HediMapper.autoInitialised = true;
  }

  window.HediMapper = Object.assign(window.HediMapper || {}, {
    create: createMapper,
    definitions: SEGMENT_DEFINITIONS.slice(),
    defaultSegmentIds: DEFAULT_SEGMENTS.slice(),
    sampleContent: { ...SEGMENT_SAMPLE_CONTENT },
    parseSegmentsFromContent,
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoInit);
  } else {
    autoInit();
  }
})();
