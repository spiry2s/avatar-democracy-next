/* Bills — the legislative lifecycle: propose → endorse → cooling-off → vote */

const SCOPE_HINTS = {
  ordinary: "Ordinary legislation — lowest endorsement threshold.",
  major: "Major reform — higher endorsement threshold.",
  constitutional: "Constitutional change — highest endorsement threshold.",
};

const STATE_LABELS = {
  draft: "Draft",
  endorsing: "Endorsing",
  cooling_off: "Cooling off",
  voting: "Voting open",
  passed: "Passed",
  failed: "Failed",
  withdrawn: "Withdrawn",
};

const ARCH = {
  libertarian:   { c: '#f5c542', label: 'Libertarian' },
  progressive:   { c: '#4bdc8a', label: 'Progressive' },
  conservative:  { c: '#5b7fff', label: 'Conservative' },
  populist_left: { c: '#ff6b6b', label: 'Populist left' },
  centrist:      { c: '#9aa0b4', label: 'Centrist' },
};

const listView = document.getElementById('list-view');
const detailView = document.getElementById('detail-view');
const billsList = document.getElementById('bills-list');

// ── Propose ───────────────────────────────────────────────────────────────────

const proposeTitle = document.getElementById('propose-title');
const proposeText = document.getElementById('propose-text');
const proposeScope = document.getElementById('propose-scope');
const proposeBtn = document.getElementById('propose-btn');
const proposeStatus = document.getElementById('propose-status');
const scopeHint = document.getElementById('scope-hint');

function updateProposeBtn() {
  proposeBtn.disabled = !(proposeTitle.value.trim() && proposeText.value.trim());
}
proposeTitle.addEventListener('input', updateProposeBtn);
proposeText.addEventListener('input', updateProposeBtn);
proposeScope.addEventListener('change', () => { scopeHint.textContent = SCOPE_HINTS[proposeScope.value]; });
scopeHint.textContent = SCOPE_HINTS.ordinary;

proposeBtn.addEventListener('click', async () => {
  proposeBtn.disabled = true;
  proposeStatus.textContent = 'Proposing…';
  try {
    const res = await fetch('/api/bills', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: proposeTitle.value.trim(),
        text: proposeText.value.trim(),
        scope: proposeScope.value,
        emergency: document.getElementById('propose-emergency').checked,
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Propose failed');
    proposeTitle.value = '';
    proposeText.value = '';
    proposeStatus.textContent = 'Proposed ✓';
    setTimeout(() => (proposeStatus.textContent = ''), 2000);
    loadList();
  } catch (err) {
    proposeStatus.textContent = `Error: ${err.message}`;
  } finally {
    updateProposeBtn();
  }
});

// ── List ────────────────────────────────────────────────────────────────────

async function loadList() {
  try {
    const data = await fetch('/api/bills').then(r => r.json());
    renderList(data.bills || []);
  } catch (err) {
    billsList.innerHTML = `<p class="muted">Could not load bills: ${escHtml(err.message)}</p>`;
  }
}

function renderList(bills) {
  if (!bills.length) {
    billsList.innerHTML = '<p class="muted">No bills yet. Propose one above.</p>';
    return;
  }
  billsList.innerHTML = bills.map(b => {
    const progress = (b.state === 'endorsing')
      ? progressBar(b.total_endorsements, b.endorsement_threshold)
      : '';
    return `<div class="bill-card" data-id="${escHtml(b.id)}">
      <div class="bill-card-main">
        <span class="state-badge state-${b.state}">${STATE_LABELS[b.state] || b.state}</span>
        ${b.emergency ? '<span class="badge-emergency">⚡ Emergency</span>' : ''}
        <span class="bill-card-title">${escHtml(b.title)}</span>
        <span class="muted bill-card-scope">${escHtml(b.scope)}</span>
      </div>
      ${progress}
      ${resultLine(b)}
      ${b.conflicts && b.conflicts.length ? `<p class="conflict-line">⚠ conflicts with ${b.conflicts.length} passed bill(s)</p>` : ''}
    </div>`;
  }).join('');
  billsList.querySelectorAll('.bill-card').forEach(card =>
    card.addEventListener('click', () => openDetail(card.dataset.id)));
}

function resultLine(b) {
  if (!b.result) return '';
  return `<p class="muted bill-card-result">${b.result.sections_passed}/${b.result.sections_total} sections passed</p>`;
}

function progressBar(current, total) {
  const pct = total > 0 ? Math.min(100, (current / total) * 100) : 0;
  return `<div class="progress">
    <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
    <span class="progress-label">${current.toLocaleString()} / ${total.toLocaleString()}</span>
  </div>`;
}

// ── Detail ────────────────────────────────────────────────────────────────────

let currentBill = null;

document.getElementById('back-btn').addEventListener('click', () => {
  detailView.classList.add('hidden');
  listView.classList.remove('hidden');
  loadList();
});

async function openDetail(billId) {
  try {
    const data = await fetch(`/api/bills/${billId}`).then(r => r.json());
    currentBill = data.bill;
    listView.classList.add('hidden');
    detailView.classList.remove('hidden');
    window.scrollTo(0, 0);
    renderDetail(currentBill);
  } catch (err) {
    alert(`Could not load bill: ${err.message}`);
  }
}

function renderDetail(b) {
  document.getElementById('detail-eyebrow').innerHTML =
    `<span class="state-badge state-${b.state}">${STATE_LABELS[b.state] || b.state}</span> · ${escHtml(b.scope)}`
    + (b.emergency ? ' · <span class="badge-emergency">⚡ Emergency</span>' : '');
  document.getElementById('detail-title').textContent = b.title;

  let html = '';

  // Emergency + conflict banners (shown at every stage)
  if (b.emergency) {
    html += `<div class="banner emergency-banner"><span><strong>⚡ Emergency fast-track.</strong> This bill skips the normal cooling-off period and required a higher endorsement bar to invoke. It is flagged for mandatory post-hoc review.</span></div>`;
  }
  if (b.conflicts && b.conflicts.length) {
    html += `<div class="banner conflict-banner"><span><strong>⚠ Conflicts with already-passed legislation:</strong>
      <ul>${b.conflicts.map(c => `<li><strong>${escHtml(c.title || c.bill_id)}</strong> — ${escHtml(c.conflict)}</li>`).join('')}</ul></span></div>`;
  }

  // Stage-specific controls
  if (b.state === 'draft') html += draftPanel(b);
  if (b.state === 'endorsing') html += endorsingPanel(b);
  if (b.state === 'cooling_off') html += coolingPanel(b);
  if (b.state === 'voting') html += votingPanel(b);
  if (b.state === 'passed' || b.state === 'failed') html += resultPanel(b);

  // Always: version history
  html += versionsPanel(b);

  document.getElementById('detail-body').innerHTML = html;
  wireDetail(b);
}

function draftPanel(b) {
  return `<div class="result-card"><div class="result-card-header"><h3>Draft</h3></div>
    <div class="result-card-body">
      <p class="muted">Edit the text into new versions, then open it for endorsement.</p>
      <textarea id="new-version-text" rows="6" placeholder="New version text...">${escHtml(currentText(b))}</textarea>
      <input type="text" id="new-version-note" class="title-field full-width" placeholder="What changed? (optional note)">
      <div class="input-footer-inline">
        <button class="btn-secondary" id="add-version-btn">Save new version</button>
        <button class="btn-primary" id="open-btn">Open for endorsement</button>
      </div>
    </div></div>`;
}

function endorsingPanel(b) {
  const endorsed = (b.endorsements || []).some(e => e.citizen_id === 'local');
  const remaining = Math.max(0, b.endorsement_threshold - b.total_endorsements);
  return `<div class="result-card"><div class="result-card-header">
      <h3>Endorsement</h3><span class="badge">${b.total_endorsements.toLocaleString()} / ${b.endorsement_threshold.toLocaleString()}</span>
    </div>
    <div class="result-card-body">
      ${progressBar(b.total_endorsements, b.endorsement_threshold)}
      <p class="muted">Reaching the threshold freezes the bill and starts the cooling-off period. ${remaining > 0 ? `${remaining.toLocaleString()} more needed.` : 'Threshold met!'}</p>
      <div class="input-footer-inline">
        ${endorsed
          ? `<button class="btn-secondary" id="revoke-btn">Revoke my endorsement</button>`
          : `<button class="btn-primary" id="endorse-btn">Endorse with my Avatar</button>`}
      </div>
      <div class="sim-control">
        <span class="muted">Demo — simulate other citizens:</span>
        <input type="number" id="sim-count" class="title-field sim-input" value="${remaining || 10}" min="1">
        <button class="btn-secondary" id="sim-btn">Add simulated endorsements</button>
      </div>
    </div></div>`;
}

function coolingPanel(b) {
  const secs = b.cooling_off_remaining || 0;
  return `<div class="result-card"><div class="result-card-header"><h3>Cooling-off period</h3></div>
    <div class="result-card-body">
      <p>The bill is frozen at version ${b.frozen_version}. Voting opens when the cooling-off period ends.</p>
      <p class="countdown">${secs > 0 ? `Voting opens in ${fmtDuration(secs)}` : 'Cooling-off complete — refresh to open voting.'}</p>
      ${b.analysis ? '<p class="muted">✓ Neutral analysis ready.</p>' : '<p class="muted">Analysis will run when voting opens.</p>'}
      <button class="btn-secondary" id="refresh-detail-btn">Refresh</button>
    </div></div>`;
}

function votingPanel(b) {
  return `<div class="result-card"><div class="result-card-header"><h3>Voting open</h3></div>
    <div class="result-card-body">
      <p>The cooling-off period is over. Your Avatar will read the frozen bill and vote on each section.</p>
      <div class="status hidden" id="vote-status" style="margin:14px 0">
        <div class="status-spinner"></div>
        <div class="status-text"><div class="status-primary">Your Avatar is voting…</div>
        <div class="status-secondary">Analyzing the frozen bill and applying your configuration.</div></div>
      </div>
      <button class="btn-primary" id="cast-vote-btn">Cast Avatar vote</button>
    </div></div>`;
}

function chamberBar(t) {
  const total = (t.yes || 0) + (t.no || 0) + (t.abstain || 0);
  if (!total) return '';
  const pct = n => `${(n / total * 100).toFixed(1)}%`;
  return `<div class="chamber">
    <div class="chamber-bar">
      <div class="cb-seg cb-yes" style="width:${pct(t.yes)}"></div>
      <div class="cb-seg cb-no" style="width:${pct(t.no)}"></div>
      <div class="cb-seg cb-abstain" style="width:${pct(t.abstain)}"></div>
    </div>
    <div class="chamber-counts">
      <span class="cb-yes-t">${(t.yes || 0).toLocaleString()} yes</span>
      <span class="cb-no-t">${(t.no || 0).toLocaleString()} no</span>
      <span class="muted">${(t.abstain || 0).toLocaleString()} abstain</span>
      ${(t.needs_human_review || 0) ? `<span class="badge-review">${Number(t.needs_human_review).toLocaleString()} needs human review (excluded from yes/no)</span>` : ''}
    </div>
  </div>`;
}

function blocBreakdown(byArch) {
  if (!byArch || !Object.keys(byArch).length) return '';
  const rows = Object.keys(ARCH).filter(k => byArch[k]).map(k => {
    const t = byArch[k];
    const tot = (t.yes || 0) + (t.no || 0) + (t.abstain || 0);
    if (!tot) return '';
    const pct = n => `${(n / tot * 100).toFixed(0)}%`;
    const a = ARCH[k];
    return `<div class="bloc-row">
      <span class="arch-swatch" style="background:${a.c}"></span>
      <span class="bloc-name">${a.label}</span>
      <div class="chamber-bar bloc-bar">
        <div class="cb-seg cb-yes" style="width:${pct(t.yes)}"></div>
        <div class="cb-seg cb-no" style="width:${pct(t.no)}"></div>
        <div class="cb-seg cb-abstain" style="width:${pct(t.abstain)}"></div>
      </div>
      <span class="bloc-count">${t.yes || 0}y / ${t.no || 0}n</span>
    </div>`;
  }).join('');
  return `<details class="bloc-breakdown"><summary>How the blocs voted</summary>${rows}</details>`;
}

function resultPanel(b) {
  const r = b.result || { sections_passed: 0, sections_total: 0, per_section: [] };
  const v = b.vote || {};
  const pop = r.population_size;
  const tensionHtml = (v.key_tension && v.key_tension.trim())
    ? `<div class="tension"><strong>⚖ Tension in your configuration:</strong> ${escHtml(v.key_tension)}</div>` : '';

  // Lead with the chamber tally per section; fold in the operator Avatar's reasoning.
  const sections = (r.per_section || []).map(t => {
    const sv = (v.section_votes || []).find(x => x.section_id === t.section_id) || {};
    const pos = (sv.position || 'abstain').toLowerCase();
    const noContest = t.contested === false;
    const passBadge = noContest
      ? `<span class="sig-badge sig-medium">no contest</span>`
      : `<span class="sig-badge ${t.passed ? 'sig-low' : 'sig-high'}">${t.passed ? 'section passes' : 'section fails'}</span>`;
    const itemClass = noContest ? 'vote-abstain' : (t.passed ? 'vote-yes' : 'vote-no');
    return `<div class="vote-item ${itemClass}">
      <div class="vote-header">
        <span class="section-id">${escHtml(t.section_id)}</span>
        <span class="section-heading">${escHtml(t.heading || sv.heading || '')}</span>
        ${passBadge}
      </div>
      ${chamberBar(t)}
      ${noContest ? '' : blocBreakdown(t.by_archetype)}
      ${sv.divergent ? `<div class="diverge-line"><span class="badge-diverge">⚠ second Claude pass</span> another Claude model voted differently — treat as low-confidence. This is not a separate vendor.</div>` : ''}
      ${sv.comparison_abstained ? `<div class="diverge-line"><span class="badge-review">⚠ models disagree — abstained</span> ${escHtml(sv.comparison_note || '')}</div>` : ''}
      ${sv.needs_human_review ? `<div class="diverge-line"><span class="badge-review">⚠ human review</span> ${escHtml(sv.comparison_note || '')}</div>` : ''}
      ${sv.delegate_drift ? `<div class="diverge-line"><span class="badge-drift">⚠ delegate drift</span> this vote contradicts the delegate's recorded positions — review before trusting it</div>` : ''}
      ${sv.reasoning ? `<p class="vote-reasoning"><span class="op-tag pos-${pos}">your Avatar: ${escHtml(sv.position)}</span>${escHtml(sv.reasoning)}</p>` : ''}
      ${sv.basis ? `<div class="vote-footer"><span class="vote-basis">${formatBasis(sv.basis)}</span></div>` : ''}
    </div>`;
  }).join('');

  return `<div class="result-card">
    <div class="result-card-header">
      <h3>Chamber outcome</h3>
      <span class="state-badge state-${b.state}">${STATE_LABELS[b.state]}</span>
    </div>
    <div class="result-card-body">
      <p><strong>${r.sections_passed} of ${r.sections_contested ?? r.sections_total} contested sections passed</strong>${pop ? ` across a Proxy Chamber of ${pop.toLocaleString()} Avatars` : ''}. A bill passes only if every contested section passes (no Christmas-tree bills); procedural sections everyone abstains on are "no contest".</p>
      ${v.recommendation ? `<p class="muted">Your Avatar's view: ${escHtml(v.recommendation)}</p>` : ''}
      ${tensionHtml}
    </div>
  </div>
  <div class="result-card">
    <div class="result-card-header"><h3>Section-by-section</h3><span class="badge">${(r.per_section || []).length}</span></div>
    <div class="result-card-body">${sections || '<p class="muted">No sections.</p>'}</div>
  </div>`;
}

function versionsPanel(b) {
  const rows = (b.versions || []).map(v => `<div class="version-row">
    <span class="version-num">v${v.version}</span>
    <span class="version-note">${escHtml(v.note || '')}</span>
    <span class="muted version-date">${fmtTime(v.created_at)}</span>
    ${v.version === b.frozen_version ? '<span class="sig-badge sig-medium">frozen</span>' : ''}
  </div>`).join('');
  return `<div class="result-card"><div class="result-card-header"><h3>Version history</h3>
    <span class="badge">${(b.versions || []).length}</span></div>
    <div class="result-card-body">${rows}</div></div>`;
}

// ── Detail action wiring ───────────────────────────────────────────────────────

function wireDetail(b) {
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('click', fn); };

  on('add-version-btn', async () => {
    const text = document.getElementById('new-version-text').value.trim();
    const note = document.getElementById('new-version-note').value.trim();
    if (!text) return;
    await action(`/api/bills/${b.id}/versions`, 'POST', { text, note });
  });
  on('open-btn', () => action(`/api/bills/${b.id}/open`, 'POST'));
  on('endorse-btn', () => action(`/api/bills/${b.id}/endorse`, 'POST'));
  on('revoke-btn', () => action(`/api/bills/${b.id}/endorse`, 'DELETE'));
  on('refresh-detail-btn', () => openDetail(b.id));
  on('sim-btn', () => {
    const count = parseInt(document.getElementById('sim-count').value, 10) || 1;
    action(`/api/bills/${b.id}/simulate-endorsements`, 'POST', { count });
  });
  on('cast-vote-btn', async () => {
    const status = document.getElementById('vote-status');
    const btn = document.getElementById('cast-vote-btn');
    if (status) status.classList.remove('hidden');
    if (btn) btn.disabled = true;
    await action(`/api/bills/${b.id}/vote`, 'POST', null, true);
  });
}

async function action(url, method, body, isVote) {
  try {
    const opts = { method };
    if (body) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error((await res.json()).detail || `${method} ${url} failed`);
    const data = await res.json();
    currentBill = data.bill;
    renderDetail(currentBill);
  } catch (err) {
    if (isVote) {
      const s = document.getElementById('vote-status');
      if (s) s.classList.add('hidden');
    }
    alert(err.message);
    openDetail(currentBill ? currentBill.id : '');
  }
}

// ── Shared helpers (mirrors vote.js) ────────────────────────────────────────────

function currentText(b) {
  const cur = (b.versions || []).find(v => v.version === b.current_version);
  return cur ? cur.text : ((b.versions || [])[ (b.versions || []).length - 1 ] || {}).text || '';
}

function formatBasis(basis) {
  if (!basis) return 'compass';
  if (basis.startsWith('delegate:')) return `following delegate · ${escHtml(basis.slice(9).trim())}`;
  if (basis === 'values') return 'your values statement';
  if (basis === 'compass') return 'your compass (fallback)';
  return escHtml(basis);
}
function conf(c) { return c === 'high' ? 'low' : c === 'low' ? 'high' : 'medium'; }

function fmtDuration(s) {
  if (s <= 0) return 'moments';
  const d = Math.floor(s / 86400); if (d >= 1) return `${d} day${d > 1 ? 's' : ''}`;
  const h = Math.floor(s / 3600); if (h >= 1) return `${h} hour${h > 1 ? 's' : ''}`;
  const m = Math.floor(s / 60); if (m >= 1) return `${m} minute${m > 1 ? 's' : ''}`;
  return `${Math.floor(s)} seconds`;
}
function fmtTime(ts) { return ts ? new Date(ts * 1000).toLocaleString() : ''; }

function escHtml(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

document.getElementById('refresh-bills').addEventListener('click', loadList);
loadList();
