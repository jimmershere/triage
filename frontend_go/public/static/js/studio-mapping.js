/**
 * TurboHEDI Studio — Mapping Enhancement Layer
 * Adds: grouped palette, improved interactions, delete confirmation
 */
(function () {
  "use strict";

  const SEGMENT_GROUPS = {
    envelope: ["ISA", "GS", "ST", "SE", "GE", "IEA"],
    claim: ["BHT", "NM1", "HL", "SBR", "PAT", "CLM", "DTP", "DTM", "REF", "HI", "N1"],
    service: ["LX", "SV1", "BPR", "TRN"],
  };

  // ── Grouped Palette Population ────────────────────────────────────────
  function populateGroupedPalette() {
    const definitions = window.TriageMapper?.definitions || [];
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

        chip.addEventListener("click", () => {
          const mapper = window.defaultTriageMapper;
          if (mapper && typeof mapper.appendSegmentById === "function") {
            mapper.appendSegmentById(def.id);
          }
        });

        fragment.appendChild(li);
      });

      container.appendChild(fragment);
    });
  }

  // ── Delete Confirmation ───────────────────────────────────────────────
  function initDeleteConfirmation() {
    const deleteBtn = document.getElementById("deleteMapping");
    if (!deleteBtn) return;

    let confirmState = false;
    let resetTimer = null;

    deleteBtn.addEventListener("click", (e) => {
      if (!confirmState) {
        e.preventDefault();
        e.stopImmediatePropagation();
        confirmState = true;
        deleteBtn.textContent = "Confirm delete?";
        deleteBtn.classList.add("pill-button--danger-confirm");
        resetTimer = setTimeout(() => {
          confirmState = false;
          deleteBtn.textContent = "Delete";
          deleteBtn.classList.remove("pill-button--danger-confirm");
        }, 3000);
      } else {
        confirmState = false;
        clearTimeout(resetTimer);
        deleteBtn.textContent = "Delete";
        deleteBtn.classList.remove("pill-button--danger-confirm");
        // Let the original handler proceed
      }
    }, true); // capture phase so we can intercept before mapping-directory.js
  }

  // ── Initialize ────────────────────────────────────────────────────────
  function init() {
    populateGroupedPalette();
    initDeleteConfirmation();

    // Watch for mapper initialization
    if (!window.defaultTriageMapper && !window.TriageMapper?.definitions?.length) {
      const check = setInterval(() => {
        if (window.TriageMapper?.definitions?.length) {
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
