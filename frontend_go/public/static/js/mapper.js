const palette = document.getElementById('segments');
const canvas = document.getElementById('canvas');
const emptyState = document.getElementById('canvasEmpty');

if (!palette || !canvas || !emptyState) {
  console.warn('HEDI mapping interface not initialised — elements missing');
} else {
  const GRID_COLUMNS = 80;
  const SEGMENT_DEFINITIONS = [
    { id: 'ISA', name: 'Interchange Control Header', color: '#2563eb' },
    { id: 'GS', name: 'Functional Group Header', color: '#7c3aed' },
    { id: 'ST', name: 'Transaction Set Header', color: '#f97316' },
    { id: 'BHT', name: 'Beginning of Hierarchical Transaction', color: '#d946ef' },
    { id: 'NM1', name: 'Individual or Organizational Name', color: '#14b8a6' },
    { id: 'HL', name: 'Hierarchical Level', color: '#0ea5e9' },
    { id: 'SBR', name: 'Subscriber Information', color: '#facc15' },
    { id: 'PAT', name: 'Patient Information', color: '#22c55e' },
    { id: 'CLM', name: 'Claim Information', color: '#fb7185' },
    { id: 'DTP', name: 'Date or Time Reference', color: '#38bdf8' },
    { id: 'REF', name: 'Reference Information', color: '#f59e0b' },
    { id: 'HI', name: 'Health Care Diagnosis Code', color: '#a855f7' },
    { id: 'LX', name: 'Service Line Number', color: '#f97316' },
    { id: 'SV1', name: 'Professional Service', color: '#8b5cf6' },
    { id: 'SE', name: 'Transaction Set Trailer', color: '#2563eb' },
    { id: 'GE', name: 'Functional Group Trailer', color: '#0891b2' },
    { id: 'IEA', name: 'Interchange Control Trailer', color: '#0f766e' }
  ];

  const SEGMENT_SAMPLE_CONTENT = {
    ISA: 'ISA*00*          *00*          *ZZ*SUBMITTER*ZZ*RECEIVER*230101*1234*^*00501*000000905*0*T*:~',
    GS: 'GS*HC*SUBMITTER*RECEIVER*20230101*1234*1*X*005010X222A1~',
    ST: 'ST*837*0001*005010X222A1~',
    BHT: 'BHT*0019*00*0123*20230101*1234*CH~',
    NM1: 'NM1*41*2*SUBMITTER*****46*123456789~',
    HL: 'HL*1**20*1~',
    SBR: 'SBR*P*18*******CI~',
    PAT: 'PAT*19~',
    CLM: 'CLM*123456789*100***11:B:1*Y*A*Y*I*P~',
    DTP: 'DTP*434*RD8*20230101-20230101~',
    REF: 'REF*G1*REF12345~',
    HI: 'HI*ABK:Z1234~',
    LX: 'LX*1~',
    SV1: 'SV1*HC:99213*100*UN*1***1~',
    SE: 'SE*23*0001~',
    GE: 'GE*1*1~',
    IEA: 'IEA*1*000000905~'
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
    "IEA"
  ];

  const activeSegments = DEFAULT_SEGMENTS.map((id) =>
    SEGMENT_DEFINITIONS.find((segment) => segment.id === id)
  ).filter(Boolean);

  function createSegmentChip(segment) {
    const chip = document.createElement('span');
    chip.className = 'segment-chip';
    chip.style.setProperty('--chip-color', segment.color);

    const code = document.createElement('span');
    code.className = 'chip-code';
    code.textContent = segment.id;

    const name = document.createElement('span');
    name.className = 'chip-name';
    name.textContent = segment.name;

    chip.append(code, name);
    chip.title = `${segment.id} — ${segment.name}`;
    return chip;
  }

  function renderPalette() {
    SEGMENT_DEFINITIONS.forEach(segment => {
      const item = document.createElement('li');
      item.appendChild(createSegmentChip(segment));
      item.draggable = true;
      item.style.listStyle = 'none';
      item.addEventListener('dragstart', e => {
        e.dataTransfer.setData('text/plain', segment.id);
        e.dataTransfer.effectAllowed = 'copy';
      });
      palette.appendChild(item);
    });
  }

  function appendSegmentToCanvas(segment) {
    if (emptyState) {
      emptyState.hidden = true;
    }
    activeSegments.push(segment);
    renderCanvas();
  }

  function createGridCell(value, span, className) {
    const cell = document.createElement('span');
    cell.className = 'grid-cell';
    if (className) {
      cell.classList.add(className);
    }

    const resolvedSpan = Math.max(1, Math.min(span || (typeof value === 'string' ? value.length : 1), GRID_COLUMNS));
    cell.style.gridColumn = `span ${resolvedSpan}`;

    if (value instanceof Node) {
      cell.appendChild(value);
    } else if (typeof value === 'string') {
      cell.textContent = value.replace(/ /g, '\u00a0');
    } else {
      cell.textContent = String(value);
    }

    return cell;
  }

  function createGridChip(segment, span) {
    const chip = createSegmentChip(segment);
    chip.classList.add('grid-chip');
    chip.style.gridColumn = `span ${Math.max(1, Math.min(span, GRID_COLUMNS))}`;
    chip.setAttribute('aria-label', `${segment.id} segment chip`);
    return chip;
  }

  function createSegmentRow(segment) {
    const row = document.createElement('div');
    row.className = 'grid-row';
    row.setAttribute('role', 'listitem');
    row.style.setProperty('--grid-columns', GRID_COLUMNS);

    let consumed = 0;

    function addCell(element, span) {
      consumed += span;
      row.appendChild(element);
    }

    const indentSpan = 2;
    addCell(createGridCell('  ', indentSpan, 'indent'), indentSpan);

    const openBracketSpan = 1;
    addCell(createGridCell('<', openBracketSpan, 'bracket'), openBracketSpan);

    const chipSpan = Math.max(segment.id.length + 2, 6);
    addCell(createGridChip(segment, chipSpan), chipSpan);

    const closeBracketSpan = 1;
    addCell(createGridCell('>', closeBracketSpan, 'bracket'), closeBracketSpan);

    const sample = SEGMENT_SAMPLE_CONTENT[segment.id] || `${segment.id}*...~`;
    const closingReserve = 2 + chipSpan + 1;
    const available = Math.max(GRID_COLUMNS - consumed - closingReserve, 4);
    const sampleSpan = Math.min(sample.length, available);
    const sampleCell = createGridCell(sample, sampleSpan, 'content');
    sampleCell.title = sample;
    addCell(sampleCell, sampleSpan);

    const closingStartSpan = 2;
    addCell(createGridCell('</', closingStartSpan, 'bracket'), closingStartSpan);

    addCell(createGridChip(segment, chipSpan), chipSpan);

    addCell(createGridCell('>', closeBracketSpan, 'bracket'), closeBracketSpan);

    return row;
  }

  function createBoundaryRow(open = true) {
    const row = document.createElement('div');
    row.className = 'grid-row structural-row';
    row.setAttribute('role', 'listitem');
    row.style.setProperty('--grid-columns', GRID_COLUMNS);

    const label = open ? '<transactionSet type="837P">' : '</transactionSet>';
    const span = Math.min(label.length, GRID_COLUMNS);
    row.appendChild(createGridCell(label, span, 'content'));

    return row;
  }

  function renderCanvas() {
    Array.from(canvas.querySelectorAll('.grid-row')).forEach(row => row.remove());

    if (!activeSegments.length) {
      emptyState.hidden = false;
      return;
    }

    emptyState.hidden = true;

    canvas.appendChild(createBoundaryRow(true));
    canvas.appendChild(createRulerRow());
    activeSegments.forEach(segment => {
      canvas.appendChild(createSegmentRow(segment));
    });
    canvas.appendChild(createBoundaryRow(false));
  }

  function createRulerRow() {
    const row = document.createElement('div');
    row.className = 'grid-row ruler';
    row.style.setProperty('--grid-columns', GRID_COLUMNS);
    for (let i = 0; i < GRID_COLUMNS; i += 5) {
      const label = String(i + 1).padStart(2, ' ');
      const span = Math.min(5, GRID_COLUMNS - i);
      row.appendChild(createGridCell(label, span));
    }
    return row;
  }

  function initDragAndDrop() {
    canvas.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      canvas.classList.add('dragover');
    });

    canvas.addEventListener('dragleave', e => {
      if (!canvas.contains(e.relatedTarget)) {
        canvas.classList.remove('dragover');
      }
    });

    canvas.addEventListener('drop', e => {
      e.preventDefault();
      canvas.classList.remove('dragover');
      const segmentId = (e.dataTransfer.getData('text/plain') || '').trim().toUpperCase();
      const segment = SEGMENT_DEFINITIONS.find(item => item.id === segmentId);
      if (!segment) {
        return;
      }
      appendSegmentToCanvas(segment);
    });
  }

  renderPalette();
  initDragAndDrop();
  renderCanvas();
}
