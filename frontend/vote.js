/* Vote — analyze a bill, or vote on an analysis handed off from Analyze. */

const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');
const fileInfo = document.getElementById('file-info');
const fileName = document.getElementById('file-name');
const billUrl = document.getElementById('bill-url');
const textInput = document.getElementById('text-input');
const voteBtn = document.getElementById('vote-btn');
const statusEl = document.getElementById('status');
const statusPrimary = document.getElementById('status-primary');
const statusSecondary = document.getElementById('status-secondary');
const errorEl = document.getElementById('error');
const errorText = document.getElementById('error-text');
const resultsEl = document.getElementById('results');
const HANDOFF_KEY = window.VOTE_HANDOFF_KEY;

let activeTab = 'upload';
let selectedFile = null;
let currentVote = null;
let excerptNote = '';
let handoff = null;
let busy = false;

// ── Check profile exists ──────────────────────────────────────────────────────

apiFetch('/api/profile').then(r => r.json()).then(d => {
  if (!d.profile) document.getElementById('no-profile-banner').classList.remove('hidden');
});

// ── Tabs ──────────────────────────────────────────────────────────────────────

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    activeTab = tab.dataset.tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === tab));
    document.querySelectorAll('.tab-content').forEach(c =>
      c.classList.toggle('active', c.id === `tab-${activeTab}`));
    updateBtn();
  });
});

// ── Upload zone ───────────────────────────────────────────────────────────────

uploadZone.addEventListener('click', () => fileInput.click());
uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', e => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files[0]) setFile(fileInput.files[0]); });
document.getElementById('file-clear').addEventListener('click', () => {
  selectedFile = null;
  fileInput.value = '';
  fileInfo.classList.add('hidden');
  uploadZone.classList.remove('hidden');
  updateBtn();
});

function setFile(file) {
  if (!file.name.endsWith('.pdf')) { showError('Only PDF upload is supported. Use Paste text otherwise.'); return; }
  selectedFile = file;
  fileName.textContent = `${file.name} (${(file.size / 1048576).toFixed(1)} MB)`;
  fileInfo.classList.remove('hidden');
  uploadZone.classList.add('hidden');
  updateBtn();
}

textInput.addEventListener('input', updateBtn);
billUrl.addEventListener('input', updateBtn);
function updateBtn() {
  if (handoff) {
    voteBtn.textContent = 'Vote on this analysis';
    voteBtn.disabled = busy;
    return;
  }
  voteBtn.textContent = 'Analyze & vote';
  if (busy) {
    voteBtn.disabled = true;
    return;
  }
  if (activeTab === 'paste') {
    voteBtn.disabled = textInput.value.trim().length < 100;
    return;
  }
  voteBtn.disabled = !selectedFile && billUrl.value.trim().length === 0;
}

// ── Vote on a stored analysis, or analyze then vote ──────────────────────────

async function voteOnAnalysis(analysis) {
  const truncation = analysis && typeof analysis.truncation === 'object' && !Array.isArray(analysis.truncation)
    ? analysis.truncation
    : {};
  excerptNote = truncation.truncated && !truncation.blocking ? oversizeNote(truncation) : '';
  setStatus(true, 'Your Avatar is voting…', 'Applying your delegates, values, and compass to each section.');
  try {
    const vRes = await apiFetch('/api/avatar/vote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ analysis: analysis }),
    });
    if (!vRes.ok) {
      const err = await vRes.json().catch(() => ({ detail: `HTTP ${vRes.status}` }));
      throw new Error(err.detail || `Vote failed (HTTP ${vRes.status})`);
    }
    currentVote = (await vRes.json()).vote;
    renderVote(currentVote);
  } catch (err) {
    showError(err.message);
  } finally {
    setStatus(false);
  }
}

voteBtn.addEventListener('click', async () => {
  hideError();
  resultsEl.classList.add('hidden');
  if (handoff) {
    await voteOnAnalysis(handoff);
    return;
  }

  const formData = new FormData();
  let loadingMode = 'analyze';
  if (activeTab === 'paste') {
    const text = textInput.value.trim();
    if (text.length < 100) {
      showError('Text too short — paste more of the bill.');
      return;
    }
    formData.append('text', text);
  } else {
    const url = billUrl.value.trim();
    if (selectedFile && url) {
      showError('Use either a PDF or a link, not both.');
      return;
    }
    if (url) {
      formData.append('url', url);
      loadingMode = 'url';
    } else if (selectedFile) {
      formData.append('file', selectedFile);
    } else {
      showError('Select a PDF or paste a bill link.');
      return;
    }
  }

  if (loadingMode === 'url') {
    setStatus(true, 'Fetching the bill…', 'Reading the official source, then analyzing.');
  } else {
    setStatus(true, 'Analyzing the bill…', 'Reading and structuring the legislation.');
  }

  try {
    const aRes = await apiFetch('/api/analyze', { method: 'POST', body: formData });
    if (!aRes.ok) {
      const err = await aRes.json().catch(() => ({ detail: `HTTP ${aRes.status}` }));
      throw new Error(err.detail || `Analyze failed (HTTP ${aRes.status})`);
    }
    const analysis = (await aRes.json()).analysis;
    setStatus(false);
    await voteOnAnalysis(analysis);
  } catch (err) {
    showError(err.message);
    setStatus(false);
  }
});

function setStatus(on, primary, secondary) {
  busy = on;
  statusEl.classList.toggle('hidden', !on);
  if (on) {
    voteBtn.disabled = true;
    statusPrimary.textContent = primary;
    statusSecondary.textContent = secondary || '';
  } else {
    updateBtn();
  }
}

function parseHandoff(raw) {
  let data;
  try {
    data = JSON.parse(raw);
  } catch (err) {
    return null;
  }
  if (!data || typeof data !== 'object' || Array.isArray(data) || data.v !== 1) return null;
  if (!data.analysis || typeof data.analysis !== 'object' || Array.isArray(data.analysis)) return null;
  return data.analysis;
}

function clearHandoff() {
  handoff = null;
  try {
    sessionStorage.removeItem(HANDOFF_KEY);
  } catch (err) {
    /* storage may be blocked; the in-memory handoff is already gone */
  }
  const banner = document.getElementById('handoff-banner');
  if (banner) banner.classList.add('hidden');
  const note = document.getElementById('handoff-note');
  if (note) {
    note.textContent = '';
    note.classList.add('hidden');
  }
  updateBtn();
}

function showHandoff(analysis) {
  handoff = analysis;
  const label = document.getElementById('handoff-bill');
  const note = document.getElementById('handoff-note');
  const title = typeof analysis.title === 'string' ? analysis.title.trim() : '';
  label.textContent = title || 'Untitled bill';
  const truncation = analysis.truncation && typeof analysis.truncation === 'object' && !Array.isArray(analysis.truncation)
    ? analysis.truncation
    : null;
  if (note) {
    if (truncation && (truncation.truncated || truncation.blocking)) {
      note.textContent = truncation.blocking
        ? (typeof truncation.message === 'string' && truncation.message ? truncation.message : 'The bill was truncated.')
        : oversizeNote(truncation);
      note.classList.remove('hidden');
    } else {
      note.textContent = '';
      note.classList.add('hidden');
    }
  }
  document.getElementById('handoff-banner').classList.remove('hidden');
  updateBtn();
}

function loadHandoff() {
  let raw = null;
  try {
    raw = sessionStorage.getItem(HANDOFF_KEY);
  } catch (err) {
    handoff = null;
    return;
  }
  if (!raw) {
    handoff = null;
    return;
  }
  const analysis = parseHandoff(raw);
  if (!analysis) {
    clearHandoff();
    return;
  }
  showHandoff(analysis);
}

document.getElementById('handoff-clear').addEventListener('click', () => {
  clearHandoff();
  hideError();
});

// ── Render ────────────────────────────────────────────────────────────────────

function renderVote(vote) {
  document.getElementById('results-title').textContent = `Your Avatar's vote: ${vote.bill_title}`;

  renderTally(vote);
  renderVoteBanner(vote);
  document.getElementById('recommendation').textContent = vote.recommendation;

  const tensionEl = document.getElementById('tension');
  if (vote.key_tension && vote.key_tension.trim()) {
    tensionEl.innerHTML = `<strong>⚖ Tension in your own configuration:</strong> ${escHtml(vote.key_tension)}`;
    tensionEl.classList.remove('hidden');
  } else {
    tensionEl.classList.add('hidden');
  }

  document.getElementById('sections-count').textContent = vote.section_votes.length;
  const container = document.getElementById('section-votes');
  container.innerHTML = vote.section_votes.map((v, i) => renderSection(v, i)).join('');

  // wire override buttons
  container.querySelectorAll('.override-opt').forEach(btn => {
    btn.addEventListener('click', () => override(parseInt(btn.dataset.idx, 10), btn.dataset.pos));
  });

  resultsEl.classList.remove('hidden');
  resultsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderTally(vote) {
  const counts = { yes: 0, no: 0, abstain: 0, needs_human_review: 0 };
  vote.section_votes.forEach(v => {
    if (v.needs_human_review && (v.position === 'yes' || v.position === 'no')) {
      counts.needs_human_review += 1;
      return;
    }
    counts[v.position] = (counts[v.position] || 0) + 1;
  });
  const review = counts.needs_human_review
    ? `<span class="tally-pill badge-review">${counts.needs_human_review} needs human review (excluded from yes/no)</span>`
    : '';
  document.getElementById('tally').innerHTML = `
    <span class="tally-pill tally-yes">${counts.yes} yes</span>
    <span class="tally-pill tally-no">${counts.no} no</span>
    <span class="tally-pill tally-abstain">${counts.abstain} abstain</span>
    ${review}`;
}

function renderSection(v, idx) {
  const pos = (v.position || 'abstain').toLowerCase();
  const basisLabel = formatBasis(v.basis);
  const overridden = v._overridden ? '<span class="sig-badge sig-medium">overridden</span>' : '';
  const diverge = v.divergent ? '<span class="badge-diverge" title="The checker is a second Claude pass (another Anthropic model by default), not an independent vendor. It voted differently — treat as low-confidence.">⚠ second Claude pass</span>' : '';
  const held = v.comparison_abstained ? `<div class="diverge-line"><span class="badge-review">⚠ models disagree — abstained</span> ${escHtml(v.comparison_note || '')}</div>` : '';
  const review = v.needs_human_review ? `<div class="diverge-line"><span class="badge-review">⚠ human review</span> ${escHtml(v.comparison_note || '')}</div>` : '';
  const drift = v.delegate_drift ? '<span class="badge-drift" title="This vote contradicts the delegate\'s recorded positions — review before trusting it">⚠ delegate drift</span>' : '';
  const blocked = v.blocked_reason
    ? `<span class="sig-badge sig-high" title="${escHtml(v.blocked_reason)}">withheld from the vote</span>`
    : '';
  const modelNote = v.model_position && v.model_position !== v.position
    ? `<p class="muted">Model output was ${escHtml(v.model_position)} and was not used.</p>`
    : '';
  return `<div class="vote-item ${allowClass(VOTE_CLASSES, pos, 'vote-abstain')}" id="${voteDomId(idx)}">
    <div class="vote-header">
      <span class="vote-position ${allowClass(POS_CLASSES, pos, 'pos-abstain')}">${escHtml(v.position)}</span>
      <span class="section-id">${escHtml(v.section_id)}</span>
      <span class="section-heading">${escHtml(v.heading)}</span>
      ${overridden}
      ${diverge}
      ${drift}
      ${blocked}
    </div>
    <p class="vote-reasoning">${escHtml(v.reasoning)}</p>
    ${modelNote}
    ${v.blocked_reason ? `<p class="muted">${escHtml(v.blocked_reason)}</p>` : ''}
    ${held}
    ${review}
    <div class="vote-footer">
      <span class="vote-basis">${basisLabel}</span>
      <span class="sig-badge ${allowClass(CONFIDENCE_CLASSES, v.confidence, 'sig-medium')}">confidence: ${escHtml(v.confidence)}</span>
      <div class="override-group">
        <span class="muted">Override:</span>
        ${['yes', 'no', 'abstain'].map(p =>
          `<button class="override-opt ${currentClass(pos, p)}" data-idx="${domIndex(idx)}" data-pos="${allowClass(VOTE_VALUES, p, 'abstain')}">${allowClass(VOTE_VALUES, p, 'abstain')}</button>`
        ).join('')}
      </div>
    </div>
  </div>`;
}

function oversizeNote(t) {
  if (t && t.message) return t.message;
  const original = Number(t && t.original_chars) || 0;
  const analyzed = Number(t && t.analyzed_chars) || 0;
  if (original > 0 && analyzed > 0 && analyzed < original) {
    let pct = Math.round((100 * analyzed) / original);
    if (pct <= 0) pct = 1;
    if (pct >= 100) pct = 99;
    return `This bill is very long; only the first ${pct}% was analyzed.`;
  }
  return 'This bill is very long; only part of it was analyzed.';
}

function renderVoteBanner(vote) {
  const el = document.getElementById('vote-banner');
  if (!el) return;
  const parts = [];
  if (excerptNote && !vote.truncation_blocking) {
    parts.push(`<div class="grounding-banner warning">${escHtml(excerptNote)}</div>`);
  }
  if (vote.truncation_blocking) {
    parts.push(`<div class="grounding-banner blocking"><strong>No yes or no vote.</strong> ${escHtml(vote.blocking_reason || vote.recommendation || 'The bill was truncated.')}</div>`);
  }
  const flags = (vote.withheld_ungrounded && vote.withheld_ungrounded.high_severity_flags) || [];
  if (flags.length) {
    const items = flags.map(f =>
      `<li><span class="sig-badge sig-high">ungrounded</span> ${escHtml(f.type || 'flag')} in ${escHtml(f.section || '?')}: ${escHtml(f.description || '')}</li>`
    ).join('');
    parts.push(`<div class="grounding-banner warning"><strong>These high-severity flags were not used for the vote.</strong> Their quotes were missing or not in the bill.<ul>${items}</ul></div>`);
  } else if (!vote.truncation_blocking && vote.blocking_reason) {
    parts.push(`<div class="grounding-banner warning"><strong>Ungrounded material was withheld.</strong> ${escHtml(vote.blocking_reason)}</div>`);
  }
  el.innerHTML = parts.join('');
}

function formatBasis(basis) {
  if (!basis) return 'compass';
  if (basis.startsWith('delegate:')) return `following delegate · ${escHtml(basis.slice(9).trim())}`;
  if (basis === 'values') return 'your values statement';
  if (basis === 'compass') return 'your compass (fallback)';
  return escHtml(basis);
}


async function override(idx, position) {
  const v = currentVote.section_votes[idx];
  const previous = v.position;
  if (previous === position) return;

  try {
    await apiFetch('/api/avatar/override', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bill_title: currentVote.bill_title,
        section_id: v.section_id,
        position,
        previous_position: previous,
      }),
    });
    v.position = position;
    v._overridden = true;
    // re-render this section + tally
    const el = document.getElementById(`vote-${idx}`);
    el.outerHTML = renderSection(v, idx);
    document.getElementById(`vote-${idx}`).querySelectorAll('.override-opt').forEach(btn =>
      btn.addEventListener('click', () => override(parseInt(btn.dataset.idx, 10), btn.dataset.pos)));
    renderTally(currentVote);
  } catch (err) {
    showError(`Override failed: ${err.message}`);
  }
}

// ── Utilities ──────────────────────────────────────────────────────────────────

function showError(msg) { errorText.textContent = msg; errorEl.classList.remove('hidden'); errorEl.scrollIntoView({ behavior: 'smooth' }); }
function hideError() { errorEl.classList.add('hidden'); }
function escHtml(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

loadHandoff();
