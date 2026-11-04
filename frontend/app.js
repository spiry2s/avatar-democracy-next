/* Bill Analyzer — Avatar Democracy frontend */

const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');
const fileInfo = document.getElementById('file-info');
const fileName = document.getElementById('file-name');
const fileClear = document.getElementById('file-clear');
const textInput = document.getElementById('text-input');
const titleInput = document.getElementById('title-input');
const analyzeBtn = document.getElementById('analyze-btn');
const statusEl = document.getElementById('status');
const statusPrimary = document.getElementById('status-primary');
const statusSecondary = document.getElementById('status-secondary');
const errorEl = document.getElementById('error');
const errorText = document.getElementById('error-text');
const resultsEl = document.getElementById('results');
const resultsTitle = document.getElementById('results-title');
const downloadJson = document.getElementById('download-json');

let activeTab = 'upload';
let selectedFile = null;
let lastAnalysis = null;

// ── Tab switching ────────────────────────────────────────────────────────────

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    activeTab = tab.dataset.tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === tab));
    document.querySelectorAll('.tab-content').forEach(c =>
      c.classList.toggle('active', c.id === `tab-${activeTab}`)
    );
    updateAnalyzeBtn();
  });
});

// ── Upload zone ──────────────────────────────────────────────────────────────

uploadZone.addEventListener('click', () => fileInput.click());

uploadZone.addEventListener('dragover', e => {
  e.preventDefault();
  uploadZone.classList.add('drag-over');
});

uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));

uploadZone.addEventListener('drop', e => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) setFile(file);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});

fileClear.addEventListener('click', () => {
  selectedFile = null;
  fileInput.value = '';
  fileInfo.classList.add('hidden');
  uploadZone.classList.remove('hidden');
  updateAnalyzeBtn();
});

function setFile(file) {
  if (!file.name.endsWith('.pdf')) {
    showError('Only PDF files are supported for upload. Use "Paste text" for other formats.');
    return;
  }
  selectedFile = file;
  const sizeMB = (file.size / 1024 / 1024).toFixed(1);
  fileName.textContent = `${file.name} (${sizeMB} MB)`;
  fileInfo.classList.remove('hidden');
  uploadZone.classList.add('hidden');
  updateAnalyzeBtn();
}

// ── Enable/disable analyze button ────────────────────────────────────────────

textInput.addEventListener('input', updateAnalyzeBtn);

function updateAnalyzeBtn() {
  const hasContent = activeTab === 'upload'
    ? selectedFile !== null
    : textInput.value.trim().length >= 100;
  analyzeBtn.disabled = !hasContent;
}

// ── Analysis ─────────────────────────────────────────────────────────────────

analyzeBtn.addEventListener('click', async () => {
  hideError();
  resultsEl.classList.add('hidden');

  const formData = new FormData();

  if (activeTab === 'upload') {
    if (!selectedFile) { showError('Please select a PDF file.'); return; }
    formData.append('file', selectedFile);
  } else {
    const text = textInput.value.trim();
    if (!text) { showError('Please paste some bill text.'); return; }
    if (text.length < 100) { showError('Text too short — paste more of the bill.'); return; }
    formData.append('text', text);
  }

  setLoading(true);

  try {
    const res = await fetch('/api/analyze', { method: 'POST', body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    lastAnalysis = data.analysis;
    renderResults(data.analysis, data.source_chars);
  } catch (err) {
    showError(err.message);
  } finally {
    setLoading(false);
  }
});

function setLoading(on) {
  analyzeBtn.disabled = on;
  statusEl.classList.toggle('hidden', !on);
  if (on) {
    statusPrimary.textContent = 'Analyzing…';
    statusSecondary.textContent = 'This may take 30–60 seconds for long bills.';
  }
}

// ── Download JSON ─────────────────────────────────────────────────────────────

downloadJson.addEventListener('click', () => {
  if (!lastAnalysis) return;
  const blob = new Blob([JSON.stringify(lastAnalysis, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'bill-analysis.json';
  a.click();
  URL.revokeObjectURL(url);
});

// ── Render results ────────────────────────────────────────────────────────────

function renderResults(a, sourceChars) {
  // Title
  resultsTitle.textContent = a.title || 'Analysis';

  // Summary
  const m = a.metadata || {};
  const truncatedWarning = m.truncated
    ? `<p class="truncation-warning">⚠ This bill was too long to analyze in full. Only the first `
      + `${Number(m.analyzed_chars).toLocaleString()} of ${Number(m.total_chars).toLocaleString()} characters were read — `
      + `provisions after that point were not checked for red flags.</p>`
    : '';
  document.getElementById('summary-body').innerHTML =
    truncatedWarning + `<p>${escHtml(a.plain_summary)}</p>` + renderMetadata(a.metadata, sourceChars);

  // Sections
  const sectionsCard = document.getElementById('sections-card');
  const sectionsBody = document.getElementById('sections-body');
  const sectionsCount = document.getElementById('sections-count');
  if (a.sections && a.sections.length) {
    sectionsCount.textContent = a.sections.length;
    sectionsBody.innerHTML = a.sections.map(s => {
      const sig = (s.significance || 'medium').toLowerCase();
      return `<div class="section-item">
        <div class="section-header">
          <span class="section-id">${escHtml(s.section_id || '')}</span>
          <span class="section-heading">${escHtml(s.heading || '')}</span>
          <span class="sig-badge sig-${sig}">${escHtml(s.significance || 'medium')}</span>
        </div>
        <p>${escHtml(s.summary || '')}</p>
        ${s.significance_reason ? `<p class="muted">${escHtml(s.significance_reason)}</p>` : ''}
      </div>`;
    }).join('');
    sectionsCard.classList.remove('hidden');
  } else {
    sectionsCard.classList.add('hidden');
  }

  // Red flags
  const redFlagsCard = document.getElementById('redflags-card');
  const redFlagsBody = document.getElementById('redflags-body');
  const redFlagsCount = document.getElementById('redflags-count');
  if (a.red_flags && a.red_flags.length) {
    const sorted = [...a.red_flags].sort((x, y) =>
      ({ high: 0, medium: 1, low: 2 }[x.severity] ?? 3) -
      ({ high: 0, medium: 1, low: 2 }[y.severity] ?? 3)
    );
    redFlagsCount.textContent = sorted.length;
    redFlagsBody.innerHTML = sorted.map(f => {
      const sev = (f.severity || 'medium').toLowerCase();
      return `<div class="flag-item flag-${sev}">
        <div class="flag-header">
          <span class="sig-badge sig-${sev}">${escHtml(f.severity || 'medium')}</span>
          <span class="flag-type">${escHtml(f.type || '')}</span>
          <span class="muted">in ${escHtml(f.section || '?')}</span>
        </div>
        <p>${escHtml(f.description || '')}</p>
      </div>`;
    }).join('');
    redFlagsCard.classList.remove('hidden');
  } else {
    redFlagsCard.classList.add('hidden');
  }

  // Beneficiaries
  const benCard = document.getElementById('beneficiaries-card');
  const benBody = document.getElementById('beneficiaries-body');
  const b = a.beneficiaries || {};
  const hasBen = (b.benefits?.length || b.costs?.length || b.regulated?.length);
  if (hasBen) {
    let html = '<div class="ben-grid">';
    if (b.benefits?.length) {
      html += `<div><h4>Benefits</h4><ul>${b.benefits.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul></div>`;
    }
    if (b.costs?.length) {
      html += `<div><h4>Costs / pays</h4><ul>${b.costs.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul></div>`;
    }
    if (b.regulated?.length) {
      html += `<div><h4>Regulated</h4><ul>${b.regulated.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul></div>`;
    }
    html += '</div>';
    benBody.innerHTML = html;
    benCard.classList.remove('hidden');
  } else {
    benCard.classList.add('hidden');
  }

  resultsEl.classList.remove('hidden');
  resultsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderMetadata(m, sourceChars) {
  if (!m) return '';
  const items = [];
  if (m.primary_subject) items.push(['Subject', m.primary_subject]);
  if (m.complexity) items.push(['Complexity', m.complexity]);
  if (m.estimated_pages) items.push(['Est. pages', m.estimated_pages]);
  if (m.your_confidence) items.push(['Confidence', m.your_confidence]);
  if (sourceChars) items.push(['Source length', `${sourceChars.toLocaleString()} chars`]);
  if (!items.length) return '';
  return `<div class="meta-grid">${items.map(([k, v]) =>
    `<div class="meta-item"><span class="meta-key">${escHtml(k)}</span><span class="meta-val">${escHtml(String(v))}</span></div>`
  ).join('')}</div>`;
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function showError(msg) {
  errorText.textContent = msg;
  errorEl.classList.remove('hidden');
  errorEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function hideError() {
  errorEl.classList.add('hidden');
}

function escHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
