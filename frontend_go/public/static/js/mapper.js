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

  function createGridCell(value, span, className) {
    const cell = document.createElement("span");
    cell.className = "grid-cell";
    if (className) {
      cell.classList.add(className);
    }

    const resolvedSpan = Math.max(
      1,
      Math.min(span || (typeof value === "string" ? value.length : 1), GRID_COLUMNS),
    );
    cell.style.gridColumn = `span ${resolvedSpan}`;

    if (value instanceof Node) {
      cell.appendChild(value);
    } else if (typeof value === "string") {
      cell.textContent = value.replace(/ /g, "\u00a0");
    } else {
      cell.textContent = String(value);
    }

    return cell;
  }

  function createGridChip(segment, span) {
    const chip = createSegmentChip(segment);
    chip.classList.add("grid-chip");
    chip.style.gridColumn = `span ${Math.max(1, Math.min(span, GRID_COLUMNS))}`;
    chip.setAttribute("aria-label", `${segment.id} segment chip`);
    return chip;
  }

  function createBoundaryRow(open = true) {
    const row = document.createElement("div");
    row.className = "grid-row structural-row";
    row.setAttribute("role", "listitem");
    row.style.setProperty("--grid-columns", GRID_COLUMNS);

    const label = open ? "<transactionSet type=\"837P\">" : "</transactionSet>";
    const span = Math.min(label.length, GRID_COLUMNS);
    row.appendChild(createGridCell(label, span, "content"));

    return row;
  }

  function createRulerRow() {
    const row = document.createElement("div");
    row.className = "grid-row ruler";
    row.style.setProperty("--grid-columns", GRID_COLUMNS);
    for (let i = 0; i < GRID_COLUMNS; i += 5) {
      const label = String(i + 1).padStart(2, " ");
      const span = Math.min(5, GRID_COLUMNS - i);
      row.appendChild(createGridCell(label, span));
    }
    return row;
  }

  function resolveEntry(entry) {
    if (!entry) return null;
    if (entry.definition) {
      return entry;
    }
    if (entry.id) {
      return { definition: entry, content: entry.content || null };
    }
    return null;
  }

  function createSegmentRow(entry) {
    const resolved = resolveEntry(entry);
    if (!resolved || !resolved.definition) return null;
    const segment = resolved.definition;
    const row = document.createElement("div");
    row.className = "grid-row";
    row.setAttribute("role", "listitem");
    row.style.setProperty("--grid-columns", GRID_COLUMNS);

    let consumed = 0;

    function addCell(element, span) {
      consumed += span;
      row.appendChild(element);
    }

    const indentSpan = 2;
    addCell(createGridCell("  ", indentSpan, "indent"), indentSpan);

    const openBracketSpan = 1;
    addCell(createGridCell("<", openBracketSpan, "bracket"), openBracketSpan);

    const chipSpan = Math.max(segment.id.length + 2, 6);
    addCell(createGridChip(segment, chipSpan), chipSpan);

    const closeBracketSpan = 1;
    addCell(createGridCell(">", closeBracketSpan, "bracket"), closeBracketSpan);

    const sample =
      (typeof resolved.content === "string" && resolved.content.trim().length
        ? resolved.content.trim()
        : SEGMENT_SAMPLE_CONTENT[segment.id]) || `${segment.id}*...~`;
    const closingReserve = 2 + chipSpan + 1;
    const available = Math.max(GRID_COLUMNS - consumed - closingReserve, 4);
    const sampleSpan = Math.min(sample.length, available);
    const sampleCell = createGridCell(sample, sampleSpan, "content");
    sampleCell.title = sample;
    addCell(sampleCell, sampleSpan);

    const closingStartSpan = 2;
    addCell(createGridCell("</", closingStartSpan, "bracket"), closingStartSpan);

    addCell(createGridChip(segment, chipSpan), chipSpan);

    addCell(createGridCell(">", closeBracketSpan, "bracket"), closeBracketSpan);

    return row;
  }

  function parseSegmentsFromContent(raw) {
    if (typeof raw !== "string") return [];
    return raw
      .split("~")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [segmentId = ""] = line.split("*");
        const id = segmentId.trim().toUpperCase();
        if (!id) return null;
        return { id, content: `${line}~` };
      })
      .filter((entry) => entry && SEGMENT_DEFINITIONS.some((def) => def.id === entry.id));
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
      Array.from(canvasEl.querySelectorAll(".grid-row")).forEach((row) => row.remove());

      if (!state.activeSegments.length) {
        emptyStateEl.hidden = false;
        return;
      }

      emptyStateEl.hidden = true;
      canvasEl.appendChild(createBoundaryRow(true));
      canvasEl.appendChild(createRulerRow());
      state.activeSegments.forEach((segment) => {
        const row = createSegmentRow(segment);
        if (row) {
          canvasEl.appendChild(row);
        }
      });
      canvasEl.appendChild(createBoundaryRow(false));
    }

    function setSegmentsByIds(ids) {
      state.activeSegments = ids
        .map((segmentId) => {
          const definition = findSegment(segmentId);
          if (!definition) return null;
          return { definition, content: null };
        })
        .filter(Boolean);
      renderCanvas();
    }

    function appendSegmentById(segmentId) {
      const definition = findSegment(segmentId);
      if (!definition) return;
      state.activeSegments.push({ definition, content: null });
      renderCanvas();
    }

    function resetToDefault() {
      setSegmentsByIds(DEFAULT_SEGMENTS);
    }

    function getActiveSegmentIds() {
      const seen = new Set();
      return state.activeSegments
        .map((entry) => entry?.definition?.id)
        .filter((id) => {
          if (!id || seen.has(id)) return false;
          seen.add(id);
          return true;
        });
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
      const segments = parseSegmentsFromContent(rawContent);
      if (!segments.length) {
        state.activeSegments = [];
        renderCanvas();
        return;
      }
      state.activeSegments = segments
        .map((entry) => {
          const definition = findSegment(entry.id);
          if (!definition) return null;
          return { definition, content: entry.content };
        })
        .filter(Boolean);
      renderCanvas();
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
