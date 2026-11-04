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
  renderGroundingBanner(a);

  const titleBadge = a.title_grounded === false
    ? '<p><span class="sig-badge sig-high">title ungrounded</span> <span class="muted">Shown here. Withheld from the Avatar, the chamber, and the conflict check.</span></p>'
    : (a.title_grounded === true
      ? '<p><span class="sig-badge sig-low">title grounded</span></p>'
      : '');
  const summaryBadge = a.plain_summary_grounded === false
    ? '<p><span class="sig-badge sig-high">summary ungrounded</span> <span class="muted">Shown here, withheld from the Avatar.</span></p>'
    : (a.plain_summary_grounded === true
      ? '<p><span class="sig-badge sig-low">summary grounded</span></p>'
      : '');

  // Summary
  document.getElementById('summary-body').innerHTML =
    titleBadge + renderQuote({
      quote: a.title_quote,
      grounded: a.title_grounded,
      grounding_reason: a.title_grounding_reason,
    }) + summaryBadge + `<p>${escHtml(a.plain_summary)}</p>` + renderQuote({
      quote: a.plain_summary_quote,
      grounded: a.plain_summary_grounded,
      grounding_reason: a.plain_summary_grounding_reason,
    }) + renderMetadata(a.metadata, sourceChars);

  // Sections
  const sectionsCard = document.getElementById('sections-card');
  const sectionsBody = document.getElementById('sections-body');
  const sectionsCount = document.getElementById('sections-count');
  if (a.sections && a.sections.length) {
    sectionsCount.textContent = a.sections.length;
    sectionsBody.innerHTML = a.sections.map(s => {
      const sig = (s.significance || 'medium').toLowerCase();
      const ungrounded = s.grounded === false ? ' claim-ungrounded' : '';
      return `<div class="section-item${ungrounded}">
        <div class="section-header">
          <span class="section-id">${escHtml(s.section_id || '')}</span>
          <span class="section-heading">${escHtml(s.heading || '')}</span>
          <span class="sig-badge sig-${sig}">${escHtml(s.significance || 'medium')}</span>
          ${groundBadge(s)}
        </div>
        <p>${escHtml(s.summary || '')}</p>
        ${s.significance_reason ? `<p class="muted">${escHtml(s.significance_reason)}</p>` : ''}
        ${renderQuote(s)}
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
      const ungrounded = f.grounded === false ? ' claim-ungrounded' : '';
      return `<div class="flag-item flag-${sev}${ungrounded}">
        <div class="flag-header">
          <span class="sig-badge sig-${sev}">${escHtml(f.severity || 'medium')}</span>
          <span class="flag-type">${escHtml(f.type || '')}</span>
          <span class="muted">in ${escHtml(f.section || '?')}</span>
          ${groundBadge(f)}
        </div>
        <p>${escHtml(f.description || '')}</p>
        ${renderQuote(f)}
      </div>`;
    }).join('');
    redFlagsCard.classList.remove('hidden');
  } else {
    redFlagsCard.classList.add('hidden');
  }

  // Beneficiaries
  const benCard = document.getElementById('beneficiaries-card');
  const benBody = document.getElementById('beneficiaries-body');
  const claims = Array.isArray(a.beneficiary_claims) ? a.beneficiary_claims : null;
  const b = a.beneficiaries || {};
  const hasBen = claims
    ? claims.length > 0
    : (b.benefits?.length || b.costs?.length || b.regulated?.length);
  if (hasBen) {
    let html = '<div class="ben-grid">';
    const groups = [
      ['benefits', 'Benefits'],
      ['costs', 'Costs / pays'],
      ['regulated', 'Regulated'],
    ];
    for (const [key, label] of groups) {
      if (claims) {
        const items = claims.filter(c => c.group === key);
        if (!items.length) continue;
        html += `<div><h4>${label}</h4><ul>${items.map(c => {
          const ungrounded = c.grounded === false ? ' class="claim-ungrounded"' : '';
          return `<li${ungrounded}>${groundBadge(c)} ${escHtml(c.claim || '')}${renderQuote(c)}</li>`;
        }).join('')}</ul></div>`;
      } else if (b[key]?.length) {
        html += `<div><h4>${label}</h4><ul>${b[key].map(x => `<li>${escHtml(x)}</li>`).join('')}</ul></div>`;
      }
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

function renderGroundingBanner(a) {
  const el = document.getElementById('grounding-banner');
  if (!el) return;
  const blocks = [];
  const t = a.truncation || {};
  if (t.blocking) {
    blocks.push(`<div class="grounding-banner blocking"><strong>Incomplete analysis.</strong> ${escHtml(t.message || 'The bill was truncated.')}</div>`);
  } else if (t.chunked) {
    blocks.push(`<div class="grounding-banner info">${escHtml(t.message || 'The bill was analyzed in sections.')}</div>`);
  }
  const g = a.grounding || {};
  const un = (g.sections_ungrounded || 0) + (g.red_flags_ungrounded || 0) + (g.beneficiary_claims_ungrounded || 0);
  if (un > 0) {
    const high = g.high_severity_ungrounded_flags
      ? ' An ungrounded high-severity flag blocks a yes or no vote on the sections it names.'
      : '';
    blocks.push(
      `<div class="grounding-banner warning"><strong>${un} ungrounded claim${un === 1 ? '' : 's'}.</strong> `
      + `A quote was missing or was not found verbatim in the bill. Those claims stay visible below and are withheld from the Avatar.${escHtml(high)}</div>`
    );
  }
  el.innerHTML = blocks.join('');
}

function groundBadge(item) {
  if (!item || item.grounded == null) return '';
  if (item.grounded === true) return '<span class="sig-badge sig-low">grounded</span>';
  return '<span class="sig-badge sig-high">ungrounded</span>';
}

function reasonLabel(reason) {
  if (reason === 'missing_quote') return 'No quote was provided.';
  if (reason === 'quote_not_in_source') return 'Quoted text was not found in the bill.';
  if (reason === 'quote_too_short') return 'Fewer than 40 letters, digits, or punctuation. Invisible padding does not count.';
  if (reason === 'title_not_in_quote') return 'The title wording does not appear inside its quote.';
  if (reason === 'quote_not_in_section') return 'Quote was not found in the cited section.';
  if (reason === 'section_unmatched') return 'Section id does not match a heading in the bill.';
  if (reason === 'section_ambiguous') return 'Section id matches more than one heading, or names the whole bill.';
  if (reason === 'quote_outside_analyzed_text') return 'Quote appears only in text that was not sent for analysis.';
  if (reason === 'exact' || reason === 'exact_offset') return 'Matches the cited text.';
  return reason || '';
}

function renderQuote(item) {
  if (!item || (item.grounded == null && !item.quote)) return '';
  const offsets = item.grounded === true && item.quote_start != null && item.quote_end != null
    ? `<span class="quote-meta">characters ${escHtml(item.quote_start)}–${escHtml(item.quote_end)}</span>`
    : '';
  const reason = item.grounding_reason
    ? `<span class="quote-meta">${escHtml(reasonLabel(item.grounding_reason))}</span>`
    : '';
  const quote = item.quote
    ? `<blockquote class="bill-quote">${escHtml(item.quote)}</blockquote>`
    : '<p class="muted">No verbatim quote was provided.</p>';
  return `<div class="quote-block">${reason}${offsets}${quote}</div>`;
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
