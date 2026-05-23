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

  function sanitiseTransactionType(value) {
    const cleaned = String(value || "")
      .trim()
      .replace(/[^0-9A-Za-z.+-]/g, "")
      .slice(0, 16);
    return cleaned || "X12";
  }

  function extractSegmentElements(content) {
    if (typeof content !== "string") {
      return [];
    }
    return content.replace(/~\s*$/, "").split("*");
  }

  function determineTransactionSetLabel(entries, rawContent) {
    const list = Array.isArray(entries) ? entries : [];
    const findEntry = (segmentId) =>
      list.find((entry) => (entry?.id || entry?.definition?.id) === segmentId) || null;

    const stParts = extractSegmentElements(findEntry("ST")?.content);
    const gsParts = extractSegmentElements(findEntry("GS")?.content);

    const transactionId = (stParts?.[1] || "").toUpperCase();
    const implementation = (stParts?.[3] || gsParts?.[8] || "").toUpperCase();

    if (transactionId === "837") {
      if (implementation.includes("X222")) return "837P";
      if (implementation.includes("X223")) return "837I";
      if (implementation.includes("X224")) return "837D";
      return "837";
    }

    if (transactionId) {
      return transactionId;
    }

    const functionalId = (gsParts?.[1] || "").toUpperCase();
    if (functionalId) {
      return functionalId;
    }

    if (typeof rawContent === "string" && rawContent.includes("<")) {
      const match = rawContent.match(/<transactionSet[^>]*type=\"([^\"]+)\"/i);
      if (match && match[1]) {
        return match[1];
      }
    }

    return "X12";
  }

  function buildIssueMessages(entry) {
    if (!entry || !entry.issues) {
      return [];
    }
    const messages = [];
    const identifier = entry.originalIdentifier || entry.definition?.id || entry.id || "segment";

    if (entry.issues.invalidIdentifier) {
      messages.push(
        `The segment ID "${identifier}" must contain 2–4 uppercase letters or digits as required by the X12 standard.`,
      );
    }
    if (entry.issues.unknownSegment) {
      messages.push(
        `"${identifier}" is not a recognised segment for this transaction set in HEDI's palette, so the mapper cannot validate it.`,
      );
    }
    if (entry.issues.invalidCharacters) {
      messages.push(
        "This row includes characters outside the permitted X12 basic character set (uppercase letters, digits, spaces, and standard punctuation).",
      );
    }
    if (entry.issues.missingTerminator) {
      messages.push('The required "~" segment terminator is missing at the end of the line.');
    }

    return messages;
  }

  function createIssueDialog(entry, messages) {
    const dialog = document.createElement("div");
    dialog.className = "issue-thought-dialog";
    dialog.hidden = true;

    const illustration = document.createElement("img");
    illustration.src = "/img/trish-laptop.svg";
    illustration.width = 56;
    illustration.height = 56;
    illustration.alt = "X12 guidance illustration";
    illustration.className = "issue-thought-illustration";

    const copy = document.createElement("div");
    copy.className = "issue-thought-messages";

    const heading = document.createElement("h4");
    heading.className = "issue-thought-heading";
    heading.textContent = `${entry.definition?.id || entry.id || "Segment"} segment issue`;

    const list = document.createElement("ul");
    list.className = "issue-thought-list";

    if (messages.length) {
      messages.forEach((message) => {
        const item = document.createElement("li");
        item.textContent = message;
        list.appendChild(item);
      });
    } else {
      const item = document.createElement("li");
      item.textContent = "This segment needs review because it falls outside expected X12 patterns.";
      list.appendChild(item);
    }

    copy.appendChild(heading);
    copy.appendChild(list);

    dialog.appendChild(illustration);
    dialog.appendChild(copy);

    return dialog;
  }

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

  function createTextNodePreservingSpaces(text) {
    return document.createTextNode((text || "").replace(/ /g, "\u00a0"));
  }

  function buildHighlightedContent(content, highlights) {
    if (typeof content !== "string" || !Array.isArray(highlights) || !highlights.length) {
      return content;
    }
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    const sorted = highlights
      .map((range) => ({
        start: Math.max(0, Number(range.start) || 0),
        end: Math.max(0, Number(range.end) || 0),
      }))
      .filter((range) => range.end > range.start)
      .sort((a, b) => a.start - b.start);

    sorted.forEach(({ start, end }) => {
      const safeStart = Math.max(cursor, Math.min(start, content.length));
      const safeEnd = Math.max(safeStart, Math.min(end, content.length));
      if (safeStart > cursor) {
        fragment.appendChild(createTextNodePreservingSpaces(content.slice(cursor, safeStart)));
      }
      const mark = document.createElement("mark");
      mark.className = "canvas-highlight";
      mark.appendChild(createTextNodePreservingSpaces(content.slice(safeStart, safeEnd)));
      fragment.appendChild(mark);
      cursor = safeEnd;
    });

    if (cursor < content.length) {
      fragment.appendChild(createTextNodePreservingSpaces(content.slice(cursor)));
    }
    return fragment;
  }

  function mergeRanges(ranges, upperBound) {
    if (!Array.isArray(ranges) || !ranges.length) {
      return [];
    }
    const sorted = ranges
      .map(({ start, end }) => ({
        start: Math.max(0, Number(start) || 0),
        end: Math.max(0, Number(end) || 0),
      }))
      .filter((range) => range.end > range.start)
      .sort((a, b) => a.start - b.start);

    const merged = [];
    sorted.forEach((range) => {
      const capped = {
        start: range.start,
        end: typeof upperBound === "number" ? Math.min(range.end, upperBound) : range.end,
      };
      if (!merged.length) {
        merged.push(capped);
        return;
      }
      const previous = merged[merged.length - 1];
      if (capped.start <= previous.end) {
        previous.end = Math.max(previous.end, capped.end);
      } else {
        merged.push(capped);
      }
    });
    return merged.filter((range) => range.end > range.start);
  }

  function isAllowedX12Character(char) {
    if (!char) return true;
    const code = char.charCodeAt(0);
    if (code === 10 || code === 13) return true;
    if (code < 32 || code > 126) return false;
    if (code >= 97 && code <= 122) return false;
    return true;
  }

  function findInvalidCharacterRanges(value) {
    if (typeof value !== "string" || !value) {
      return [];
    }
    const ranges = [];
    let rangeStart = null;
    for (let index = 0; index < value.length; index += 1) {
      const char = value[index];
      if (!isAllowedX12Character(char)) {
        if (rangeStart === null) {
          rangeStart = index;
        }
      } else if (rangeStart !== null) {
        ranges.push({ start: rangeStart, end: index });
        rangeStart = null;
      }
    }
    if (rangeStart !== null) {
      ranges.push({ start: rangeStart, end: value.length });
    }
    return ranges;
  }

  function createBoundaryRow(open = true, transactionType = "X12") {
    const row = document.createElement("div");
    row.className = "grid-row structural-row";
    row.setAttribute("role", "listitem");
    row.style.setProperty("--grid-columns", GRID_COLUMNS);

    const safeType = sanitiseTransactionType(transactionType);
    if (open) {
      row.dataset.transactionType = safeType;
    }
    const label = open ? `<transactionSet type="${safeType}">` : "</transactionSet>";
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
    row.dataset.segmentId = segment.id;
    if (resolved?.issues?.hasIssue) {
      row.classList.add("has-issues");
      row.dataset.hasIssue = "true";

      const thoughtBubble = document.createElement("button");
      thoughtBubble.type = "button";
      thoughtBubble.className = "issue-thought-bubble";
      thoughtBubble.setAttribute("aria-label", `Explain ${segment.id} segment issues`);
      thoughtBubble.setAttribute("aria-expanded", "false");
      thoughtBubble.title = `Show guidance for ${segment.id}`;

      const icon = document.createElement("span");
      icon.className = "issue-thought-bubble__icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "💭";
      thoughtBubble.appendChild(icon);

      const messages = buildIssueMessages(resolved);
      const dialog = createIssueDialog(resolved, messages);
      thoughtBubble.issueDialogElement = dialog;

      thoughtBubble.addEventListener("click", () => {
        const container = row.parentElement || document;
        container.querySelectorAll(".issue-thought-bubble.is-open").forEach((openBubble) => {
          if (openBubble === thoughtBubble) return;
          openBubble.classList.remove("is-open");
          openBubble.setAttribute("aria-expanded", "false");
          if (openBubble.issueDialogElement) {
            openBubble.issueDialogElement.hidden = true;
          }
        });

        const isOpen = thoughtBubble.classList.toggle("is-open");
        thoughtBubble.setAttribute("aria-expanded", String(isOpen));
        dialog.hidden = !isOpen;
        if (!isOpen && typeof thoughtBubble.blur === "function") {
          thoughtBubble.blur();
        }
      });

      row.appendChild(thoughtBubble);
      row.appendChild(dialog);
    } else {
      row.dataset.hasIssue = "false";
    }

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

    const hasCustomContent = typeof resolved.content === "string" && resolved.content.trim().length;
    const sample =
      (hasCustomContent ? resolved.content.trim() : SEGMENT_SAMPLE_CONTENT[segment.id]) || `${segment.id}*...~`;
    const closingReserve = 2 + chipSpan + 1;
    const available = Math.max(GRID_COLUMNS - consumed - closingReserve, 4);
    const sampleSpan = Math.min(sample.length, available);
    const highlighted = hasCustomContent ? buildHighlightedContent(sample, resolved.highlights) : sample;
    const sampleCell = createGridCell(highlighted, sampleSpan, "content");
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

    const normalised = raw.replace(/\r\n/g, "\n");
    const segments = normalised.split("~");
    const endsWithTerminator = /~\s*$/.test(normalised);

    return segments
      .map((segment, index) => {
        const trimmed = segment.trim();
        if (!trimmed) return null;

        const hasTerminator = index < segments.length - 1 || endsWithTerminator;
        const fullContent = `${trimmed}${hasTerminator ? "~" : ""}`;
        const [rawIdentifier = ""] = trimmed.split("*");
        const identifier = rawIdentifier.trim();
        const canonicalId = identifier.toUpperCase();
        const definition = SEGMENT_DEFINITIONS.find((seg) => seg.id === canonicalId) || null;

        const highlightRanges = [];
        const issues = {
          hasIssue: false,
          unknownSegment: false,
          invalidIdentifier: false,
          invalidCharacters: false,
          missingTerminator: false,
        };

        const identifierValid = /^[A-Z0-9]{2,4}$/.test(canonicalId);
        if (!identifierValid || canonicalId !== identifier) {
          highlightRanges.push({ start: 0, end: identifier.length || fullContent.length });
          issues.invalidIdentifier = true;
        }
        if (!definition) {
          highlightRanges.push({ start: 0, end: identifier.length || fullContent.length });
          issues.unknownSegment = true;
        }

        const invalidCharacters = findInvalidCharacterRanges(fullContent);
        if (invalidCharacters.length) {
          highlightRanges.push(...invalidCharacters);
          issues.invalidCharacters = true;
        }

        if (!hasTerminator) {
          highlightRanges.push({ start: 0, end: fullContent.length });
          issues.missingTerminator = true;
        }

        const highlights = mergeRanges(highlightRanges, fullContent.length);
        issues.hasIssue = highlights.length > 0;

        const baseDefinition = definition || {
          id: identifier || canonicalId || "???",
          name: "Unrecognized segment",
          color: "#dc2626",
        };

        return {
          id: canonicalId || identifier || "",
          originalIdentifier: identifier,
          definition: baseDefinition,
          content: fullContent,
          highlights,
          issues,
        };
      })
      .filter(Boolean);
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
      issueRows: [],
      issueIndex: -1,
      lastFocusedIssue: null,
      transactionSetLabel: "X12",
    };

    canvasEl.dataset.hasIssues = "false";

    function clearIssueFocus() {
      if (!state.lastFocusedIssue) return;
      state.lastFocusedIssue.classList.remove("issue-focus");
      if (state.lastFocusedIssue.dataset && state.lastFocusedIssue.dataset.autoTabIndex === "true") {
        state.lastFocusedIssue.removeAttribute("tabindex");
        delete state.lastFocusedIssue.dataset.autoTabIndex;
      }
      state.lastFocusedIssue = null;
    }

    function applyIssueFocus(row) {
      if (!row) return null;
      clearIssueFocus();
      row.classList.add("issue-focus");
      if (!row.hasAttribute("tabindex")) {
        row.setAttribute("tabindex", "-1");
        row.dataset.autoTabIndex = "true";
      }
      if (typeof row.focus === "function") {
        row.focus({ preventScroll: true });
      }
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      state.lastFocusedIssue = row;
      return row;
    }

    function updateIssueTracking() {
      state.issueRows = Array.from(canvasEl.querySelectorAll(".grid-row.has-issues"));
      if (!state.issueRows.length) {
        state.issueIndex = -1;
        canvasEl.dataset.hasIssues = "false";
        clearIssueFocus();
        return;
      }
      canvasEl.dataset.hasIssues = "true";
      if (state.issueIndex >= state.issueRows.length) {
        state.issueIndex = -1;
      }
      if (state.lastFocusedIssue && !state.issueRows.includes(state.lastFocusedIssue)) {
        clearIssueFocus();
      }
    }

    function focusIssue(direction = 1) {
      if (!state.issueRows.length) {
        clearIssueFocus();
        canvasEl.dataset.hasIssues = "false";
        return null;
      }
      const step = direction === -1 ? -1 : 1;
      const nextIndex =
        state.issueIndex === -1
          ? step === -1
            ? state.issueRows.length - 1
            : 0
          : (state.issueIndex + step + state.issueRows.length) % state.issueRows.length;
      state.issueIndex = nextIndex;
      const row = state.issueRows[state.issueIndex];
      return applyIssueFocus(row);
    }

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
        state.issueRows = [];
        state.issueIndex = -1;
        canvasEl.dataset.hasIssues = "false";
        clearIssueFocus();
        return;
      }

      emptyStateEl.hidden = true;
      canvasEl.appendChild(createBoundaryRow(true, state.transactionSetLabel));
      canvasEl.appendChild(createRulerRow());
      state.activeSegments.forEach((segment) => {
        const row = createSegmentRow(segment);
        if (row) {
          canvasEl.appendChild(row);
        }
      });
      canvasEl.appendChild(createBoundaryRow(false, state.transactionSetLabel));
      updateIssueTracking();
    }

    function setSegmentsByIds(ids) {
      state.activeSegments = ids
        .map((segmentId) => {
          const definition = findSegment(segmentId);
          if (!definition) return null;
          return { id: definition.id, originalIdentifier: definition.id, definition, content: null };
        })
        .filter(Boolean);
      state.transactionSetLabel = determineTransactionSetLabel(state.activeSegments);
      renderCanvas();
    }

    function appendSegmentById(segmentId) {
      const definition = findSegment(segmentId);
      if (!definition) return;
      state.activeSegments.push({ id: definition.id, originalIdentifier: definition.id, definition, content: null });
      state.transactionSetLabel = determineTransactionSetLabel(state.activeSegments);
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
        state.transactionSetLabel = "X12";
        renderCanvas();
        return;
      }
      state.activeSegments = segments.map((entry) => {
        const definition = findSegment(entry.id) || entry.definition;
        return {
          id: entry.id,
          originalIdentifier: entry.originalIdentifier,
          definition,
          content: entry.content,
          highlights: entry.highlights,
          issues: entry.issues,
        };
      });
      state.transactionSetLabel = determineTransactionSetLabel(state.activeSegments, rawContent);
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
      focusNextIssue: () => !!focusIssue(1),
      focusPreviousIssue: () => !!focusIssue(-1),
      hasIssues: () => state.issueRows.length > 0,
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
