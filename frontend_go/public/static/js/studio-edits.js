/**
 * TurboHEDI Studio — Edits Enhancement Layer
 * Adds: line numbers, grouped palette, editor↔canvas sync, split-pane UX
 */
(function () {
  "use strict";

  const SEGMENT_GROUPS = {
    envelope: ["ISA", "GS", "ST", "SE", "GE", "IEA"],
    claim: ["BHT", "NM1", "HL", "SBR", "PAT", "CLM", "DTP", "DTM", "REF", "HI", "N1"],
    service: ["LX", "SV1", "BPR", "TRN"],
  };

  const editorTextarea = document.getElementById("fileEditor");
  const editorGutter = document.getElementById("editorGutter");

  // ── Line Numbers ──────────────────────────────────────────────────────
  function updateLineNumbers() {
    if (!editorTextarea || !editorGutter) return;
    const lines = editorTextarea.value.split("\n").length;
    const fragment = document.createDocumentFragment();
    // Clear existing
    editorGutter.innerHTML = "";
    for (let i = 1; i <= Math.max(lines, 1); i++) {
      const span = document.createElement("span");
      span.className = "line-num";
      span.textContent = String(i);
      fragment.appendChild(span);
    }
    editorGutter.appendChild(fragment);
  }

  // Sync scroll between textarea and gutter
  function syncGutterScroll() {
    if (!editorTextarea || !editorGutter) return;
    editorGutter.scrollTop = editorTextarea.scrollTop;
  }

  // ── Grouped Palette Population ────────────────────────────────────────
  function populateGroupedPalette() {
    const definitions = window.HediMapper?.definitions || [];
    if (!definitions.length) return;

    const groups = {
      envelope: document.getElementById("segmentGroupEnvelope"),
      claim: document.getElementById("segmentGroupClaim"),
      service: document.getElementById("segmentGroupService"),
    };

    Object.entries(SEGMENT_GROUPS).forEach(([groupKey, segmentIds]) => {
      const container = groups[groupKey];
      if (!container) return;
      container.innerHTML = "";
      const fragment = document.createDocumentFragment();

      segmentIds.forEach((segId) => {
        const def = definitions.find((d) => d.id === segId);
        if (!def) return;

        const li = document.createElement("li");
        const chip = document.createElement("span");
        chip.className = "segment-chip";
        chip.style.setProperty("--chip-color", def.color);
        chip.draggable = true;
        chip.title = `${def.id} — ${def.name}`;

        const code = document.createElement("span");
        code.className = "chip-code";
        code.textContent = def.id;

        const name = document.createElement("span");
        name.className = "chip-name";
        name.textContent = def.name;

        chip.append(code, name);
        li.appendChild(chip);

        li.draggable = true;
        li.addEventListener("dragstart", (e) => {
          e.dataTransfer.setData("text/plain", def.id);
          e.dataTransfer.effectAllowed = "copy";
        });

        // Click to append
        chip.addEventListener("click", () => {
          const mapper = window.defaultHediMapper;
          if (mapper && typeof mapper.appendSegmentById === "function") {
            mapper.appendSegmentById(def.id);
          }
        });

        fragment.appendChild(li);
      });

      container.appendChild(fragment);
    });
  }

  // ── Editor ↔ Canvas Sync ──────────────────────────────────────────────
  function syncEditorToCanvas() {
    if (!editorTextarea) return;
    const mapper = window.defaultHediMapper;
    if (!mapper || typeof mapper.updateFromContent !== "function") return;
    mapper.updateFromContent(editorTextarea.value);
  }

  let syncTimer = null;
  function handleEditorInput() {
    updateLineNumbers();
    clearTimeout(syncTimer);
    syncTimer = setTimeout(syncEditorToCanvas, 300);
  }

  // ── Initialize ────────────────────────────────────────────────────────
  function init() {
    // Line numbers
    if (editorTextarea && editorGutter) {
      updateLineNumbers();
      editorTextarea.addEventListener("input", handleEditorInput);
      editorTextarea.addEventListener("scroll", syncGutterScroll);
      // Initial sync when file loads (via MutationObserver on value changes)
      const observer = new MutationObserver(() => updateLineNumbers());
      observer.observe(editorTextarea, { attributes: true, attributeFilter: ["value"] });
    }

    // Grouped palette
    populateGroupedPalette();

    // Watch for mapper initialization
    if (!window.defaultHediMapper) {
      const check = setInterval(() => {
        if (window.defaultHediMapper) {
          clearInterval(check);
          populateGroupedPalette();
        }
      }, 200);
      setTimeout(() => clearInterval(check), 5000);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
