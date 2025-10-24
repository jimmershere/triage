(function () {
  const sidebar = document.querySelector(".edits-sidebar");
  const editorSection = document.querySelector(".edits-editor");
  if (!sidebar || !editorSection) {
    return;
  }

  const searchInput = document.getElementById("fileSearch");
  const resultsList = document.getElementById("fileResults");
  const newFileButton = document.getElementById("newFileButton");
  const newFileForm = document.getElementById("newFileForm");
  const newFileNameInput = document.getElementById("newFileName");
  const newFileTemplate = document.getElementById("newFileTemplate");
  const newFileNotes = document.getElementById("newFileNotes");
  const cancelCreateButton = document.getElementById("cancelCreate");

  const fileNameInput = document.getElementById("fileNameInput");
  const statusElement = document.getElementById("fileStatus");
  const updatedElement = document.getElementById("fileUpdated");
  const editor = document.getElementById("fileEditor");
  const saveButton = document.getElementById("saveFile");
  const submitButton = document.getElementById("submitFile");
  const resubmitButton = document.getElementById("resubmitFile");
  const canvasSaveButton = document.getElementById("canvasSaveFile");
  const canvasSubmitButton = document.getElementById("canvasSubmitFile");
  const nextIssueButton = document.getElementById("nextCanvasIssue");
  const toast = document.getElementById("fileToast");
  const browseFileButton = document.getElementById("browseLocalFile");
  const localFileInput = document.getElementById("localFileInput");
  const fileSelectionLabel = document.getElementById("fileSelectionLabel");

  const mapper = window.defaultHediMapper || (window.HediMapper && window.HediMapper.create({ initializeDefaults: true }));

  const STATUS_METADATA = {
    error: { label: "Needs correction", tone: "error" },
    draft: { label: "Draft", tone: "info" },
    submitted: { label: "Submitted", tone: "success" },
    resubmitted: { label: "Resubmitted", tone: "success" },
  };

  const sampleFiles = [
    {
      id: "837p-claim-001",
      name: "837P Claim — Cardiology",
      identifier: "ISA0001901",
      tradingPartner: "Medicare Part B",
      status: "error",
      version: 3,
      updatedAt: "2024-05-13T14:32:00Z",
      content: [
        "ISA*00*          *00*          *ZZ*CARDIOLOGY*ZZ*HEDI*240513*1432*^*00501*000000905*0*T*:~",
        "GS*HC*CARDIOLOGY*HEDI*20240513*1432*1*X*005010X222A1~",
        "ST*837*0001*005010X222A1~",
        "BHT*0019*00*0123*20240513*1432*CH~",
        "NM1*41*2*CARDIOLOGY PRACTICE*****46*123456789~",
        "HL*1**20*1~",
        "SBR*P*18*******CI~",
        "PAT*19~",
        "CLM*123456789*250***11:B:1*Y*A*Y*I*P~",
        "DTP*434*RD8*20240513-20240513~",
        "REF*G1*REF12345~",
        "HI*ABK:I10~",
        "LX*1~",
        "SV1*HC:99214*250*UN*1***1~",
        "SE*23*0001~",
        "GE*1*1~",
        "IEA*1*000000905~",
      ].join("\n"),
    },
    {
      id: "837i-edits-neo",
      name: "837I Neonatal Follow-up",
      identifier: "ISA0002235",
      tradingPartner: "Blue Cross",
      status: "draft",
      version: 1,
      updatedAt: "2024-04-28T09:12:00Z",
      content: [
        "ISA*00*          *00*          *ZZ*NEOHOSP*ZZ*HEDI*240428*0912*^*00501*000000777*0*T*:~",
        "GS*HC*NEOHOSP*HEDI*20240428*0912*1*X*005010X223A3~",
        "ST*837*1001*005010X223A3~",
        "BHT*0019*00*5555*20240428*0912*CH~",
        "NM1*41*2*NEONATAL HOSPITAL*****46*987654321~",
        "HL*1**20*1~",
        "SBR*T*18*******CI~",
        "PAT*19~",
        "CLM*222222222*999***11:B:1*Y*A*Y*I*P~",
        "DTP*434*RD8*20240427-20240427~",
        "REF*EA*REF5566~",
        "HI*BK:765.9~",
        "LX*1~",
        "SV1*HC:99460*999*UN*1***1~",
        "SE*23*1001~",
        "GE*1*1~",
        "IEA*1*000000777~",
      ].join("\n"),
    },
    {
      id: "835-remit-appeal",
      name: "835 Appeal Correction",
      identifier: "ISA0008902",
      tradingPartner: "United Payer",
      status: "submitted",
      version: 5,
      updatedAt: "2024-03-18T16:45:00Z",
      content: [
        "ISA*00*          *00*          *ZZ*UNITEDPAYER*ZZ*HEDI*240318*1645*^*00501*000001234*0*T*:~",
        "GS*HP*UNITEDPAYER*HEDI*20240318*1645*1*X*005010X221A1~",
        "ST*835*0001~",
        "BPR*I*534.54*C*ACH*CCP*01*123456789*DA*987654321*1234567890**01*999999999*DA*888888888*20240318~",
        "TRN*1*1099999990*9876543210*1512345678~",
        "DTM*111*20240315~",
        "N1*PR*UNITED PAYER*FI*999999999~",
        "N1*PE*CARDIOLOGY PRACTICE*XX*1234567890~",
        "SE*08*0001~",
        "GE*1*1~",
        "IEA*1*000001234~",
      ].join("\n"),
    },
  ];

  const files = new Map(sampleFiles.map((file) => [file.id, { ...file }]));
  let currentFileId = null;
  let editorDirty = false;
  let debounceTimer = null;

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

  function slugify(value) {
    return value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "file";
  }

  function ensureUniqueId(baseId) {
    let candidate = baseId;
    let counter = 1;
    while (files.has(candidate)) {
      candidate = `${baseId}-${counter++}`;
    }
    return candidate;
  }

  function updateFileSelectionLabel(label) {
    if (!fileSelectionLabel) return;
    fileSelectionLabel.textContent = label || "No file selected";
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

  function buildLocalIdentifier(baseName) {
    const cleaned = (baseName || "")
      .toUpperCase()
      .replace(/[^A-Z0-9]+/g, "")
      .slice(0, 12);
    return `LOCAL-${cleaned || "UPLOAD"}`;
  }

  function setEditorEnabled(enabled) {
    [fileNameInput, editor, saveButton, submitButton, resubmitButton, canvasSaveButton, canvasSubmitButton].forEach((el) => {
      if (!el) return;
      el.disabled = !enabled;
    });
  }

  function resetStatusClasses() {
    statusElement.classList.remove("status-pill--error", "status-pill--info", "status-pill--success");
  }

  function applyStatus(statusKey) {
    resetStatusClasses();
    const meta = STATUS_METADATA[statusKey] || { label: statusKey || "Unknown", tone: "info" };
    statusElement.textContent = meta.label;
    statusElement.dataset.status = statusKey || "unknown";
    const tone = meta.tone || "info";
    statusElement.classList.add(`status-pill--${tone}`);
  }

  function showToast(message, tone = "info") {
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast toast--${tone}`;
    toast.hidden = false;
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => {
      toast.hidden = true;
    }, 5000);
  }

  function renderResults() {
    if (!resultsList) return;
    const query = (searchInput?.value || "").trim().toLowerCase();
    resultsList.innerHTML = "";
    const fragment = document.createDocumentFragment();

    const entries = Array.from(files.values()).filter((file) => {
      if (!query) return true;
      return [file.name, file.identifier, file.tradingPartner]
        .filter(Boolean)
        .some((value) => value.toLowerCase().includes(query));
    });

    if (!entries.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = query ? "No files match your search." : "No files available yet.";
      resultsList.appendChild(empty);
      return;
    }

    entries
      .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
      .forEach((file) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "file-result";
        button.dataset.fileId = file.id;
        button.setAttribute("role", "listitem");
        button.innerHTML = `
          <span class="file-result__name">${file.name}</span>
          <span class="file-result__meta">${file.identifier} · ${file.tradingPartner}</span>
          <span class="file-result__status file-result__status--${file.status}">${
            STATUS_METADATA[file.status]?.label || file.status
          }</span>
        `;
        if (file.id === currentFileId) {
          button.classList.add("is-selected");
        }
        button.addEventListener("click", () => selectFile(file.id));
        fragment.appendChild(button);
      });

    resultsList.appendChild(fragment);
  }

  function updateMapper(content) {
    if (!mapper || typeof mapper.updateFromContent !== "function") {
      refreshIssueNavigation();
      return;
    }
    mapper.updateFromContent(content);
    refreshIssueNavigation();
  }

  function loadFileIntoEditor(file) {
    currentFileId = file?.id || null;
    editorDirty = false;

    if (!file) {
      statusElement.textContent = "No file selected";
      updatedElement.textContent = "";
      if (editor) {
        editor.value = "";
      }
      fileNameInput.value = "";
      updateFileSelectionLabel("No file selected");
      setEditorEnabled(false);
      updateMapper("");
      renderResults();
      return;
    }

    setEditorEnabled(true);
    fileNameInput.value = file.name;
    updateFileSelectionLabel(file.name || "No file selected");
    if (editor) {
      editor.value = file.content;
    }
    applyStatus(file.status);
    updatedElement.textContent = `Updated ${formatDate(file.updatedAt)}`;
    updateMapper(file.content);
    renderResults();
  }

  function selectFile(fileId) {
    const file = files.get(fileId);
    if (!file) {
      showToast("Unable to load the requested file.", "error");
      return;
    }
    loadFileIntoEditor(file);
  }

  function createFileFromForm(event) {
    event.preventDefault();
    const name = (newFileNameInput?.value || "").trim();
    if (!name) {
      showToast("Enter a file name to create a correction.", "error");
      newFileNameInput?.focus();
      return;
    }
    const template = newFileTemplate?.value || "837p";
    const baseId = slugify(name);
    const identifier = `ISA${String(Date.now()).slice(-9)}`;
    const now = new Date().toISOString();

    const templates = {
      "837p": window.HediMapper?.sampleContent?.ISA
        ? Object.values(window.HediMapper.sampleContent).join("\n")
        : sampleFiles[0].content,
      "837i": sampleFiles[1].content,
      "837d": sampleFiles[0].content.replace("99214", "D0120"),
      "835": sampleFiles[2].content,
    };

    const content = templates[template] || sampleFiles[0].content;
    const id = ensureUniqueId(baseId);
    const file = {
      id,
      name,
      identifier,
      tradingPartner: "Pending assignment",
      status: "draft",
      version: 1,
      updatedAt: now,
      notes: newFileNotes?.value || "",
      content,
    };

    files.set(file.id, file);
    newFileForm.hidden = true;
    newFileForm.reset();
    renderResults();
    selectFile(file.id);
    showToast(`Created ${file.name}`, "success");
  }

  function toggleCreateForm(show) {
    if (!newFileForm) return;
    newFileForm.hidden = !show;
    if (show) {
      newFileNameInput?.focus();
    }
  }

  function buildUniqueVersionName(baseName, version) {
    const trimmed = baseName.trim();
    if (!trimmed) return `corrected-file-${version}`;
    const baseId = slugify(trimmed);
    return ensureUniqueId(`${baseId}-v${version}`);
  }

  function handleSave() {
    if (!currentFileId) {
      showToast("Select a file before saving changes.", "error");
      return;
    }
    const current = files.get(currentFileId);
    if (!current) {
      showToast("File record not found.", "error");
      return;
    }
    const nextVersion = (current.version || 1) + 1;
    const desiredName = (fileNameInput?.value || current.name || "").trim() || current.name;
    const uniqueId = buildUniqueVersionName(desiredName, nextVersion);
    const now = new Date().toISOString();

    const displayName = desiredName.includes("v") ? desiredName : `${desiredName} v${nextVersion}`;

    const saved = {
      ...current,
      id: uniqueId,
      name: displayName,
      version: nextVersion,
      status: "draft",
      updatedAt: now,
      content: editor?.value || current.content,
      originId: current.id,
    };

    files.set(saved.id, saved);
    renderResults();
    selectFile(saved.id);
    showToast(`Saved ${saved.name} as version ${saved.version}`, "success");
  }

  function updateStatus(file, statusKey) {
    const now = new Date().toISOString();
    const updated = { ...file, status: statusKey, updatedAt: now };
    files.set(updated.id, updated);
    loadFileIntoEditor(updated);
    showToast(
      statusKey === "submitted"
        ? `${updated.name} queued for the worker API.`
        : `${updated.name} re-queued for processing.`,
      "success",
    );
  }

  function handleSubmit() {
    if (!currentFileId) {
      showToast("Select a file to submit.", "error");
      return;
    }
    const file = files.get(currentFileId);
    if (!file) {
      showToast("File record not found.", "error");
      return;
    }
    updateStatus(file, "submitted");
  }

  function handleResubmit() {
    if (!currentFileId) {
      showToast("Select a file to resubmit.", "error");
      return;
    }
    const file = files.get(currentFileId);
    if (!file) {
      showToast("File record not found.", "error");
      return;
    }
    updateStatus(file, "resubmitted");
  }

  function importLocalFile(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const content = typeof reader.result === "string" ? reader.result : "";
      const baseName = file.name.replace(/\.[^.]+$/, "");
      const slugBase = slugify(baseName);
      const id = ensureUniqueId(`upload-${slugBase}`);
      const now = new Date().toISOString();
      const record = {
        id,
        name: file.name,
        identifier: buildLocalIdentifier(slugBase || baseName),
        tradingPartner: "Local upload",
        status: "draft",
        version: 1,
        updatedAt: now,
        notes: "",
        content,
        source: "local",
      };
      files.set(record.id, record);
      renderResults();
      selectFile(record.id);
      showToast(`${file.name} loaded for editing.`, "success");
    };
    reader.onerror = () => {
      showToast("Unable to read the selected file.", "error");
    };
    reader.readAsText(file);
  }

  function handleLocalFileInputChange(event) {
    const file = event.target?.files?.[0];
    if (!file) return;
    importLocalFile(file);
    if (localFileInput) {
      localFileInput.value = "";
    }
  }

  function handleBrowseButtonClick() {
    if (!localFileInput) return;
    localFileInput.click();
  }

  function handleEditorInput() {
    if (!editor || !mapper) return;
    editorDirty = true;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      updateMapper(editor.value);
    }, 250);
  }

  function initializePaletteClicks() {
    if (!mapper) return;
    const palette = mapper?.elements?.palette;
    if (!palette) return;
    palette.addEventListener("click", (event) => {
      const chip = event.target.closest("li span.segment-chip");
      if (!chip) return;
      const code = chip.querySelector(".chip-code");
      if (!code) return;
      mapper.appendSegmentById(code.textContent.trim().toUpperCase());
    });
  }

  function initialize() {
    updateFileSelectionLabel("No file selected");
    renderResults();
    setEditorEnabled(false);
    initializePaletteClicks();

    searchInput?.addEventListener("input", renderResults);
    newFileButton?.addEventListener("click", () => toggleCreateForm(true));
    cancelCreateButton?.addEventListener("click", () => toggleCreateForm(false));
    newFileForm?.addEventListener("submit", createFileFromForm);
    saveButton?.addEventListener("click", handleSave);
    submitButton?.addEventListener("click", handleSubmit);
    resubmitButton?.addEventListener("click", handleResubmit);
    canvasSaveButton?.addEventListener("click", handleSave);
    canvasSubmitButton?.addEventListener("click", handleSubmit);
    nextIssueButton?.addEventListener("click", () => {
      if (!focusNextCanvasIssue()) {
        showToast("No highlighted issues to review.", "info");
      }
    });
    editor?.addEventListener("input", handleEditorInput);
    browseFileButton?.addEventListener("click", handleBrowseButtonClick);
    localFileInput?.addEventListener("change", handleLocalFileInputChange);

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
