/* Avatar setup — questionnaire, values, delegates */

let questionnaire = null;
let profile = null;
const answers = {};

const qEl = document.getElementById('questionnaire');
const compassStatus = document.getElementById('compass-status');
const compassReadout = document.getElementById('compass-readout');
const valuesInput = document.getElementById('values-input');
const delegatesList = document.getElementById('delegates-list');
const delegatesCount = document.getElementById('delegates-count');
const saveBtn = document.getElementById('save-btn');
const saveStatus = document.getElementById('save-status');

// ── Load questionnaire + existing profile ─────────────────────────────────────

async function init() {
  const [qRes, pRes] = await Promise.all([
    apiFetch('/api/questionnaire').then(r => r.json()),
    apiFetch('/api/profile').then(r => r.json()),
  ]);
  questionnaire = qRes;
  profile = pRes.profile;

  if (profile) {
    Object.assign(answers, profile.questionnaire_answers || {});
    valuesInput.value = profile.values || '';
  }

  renderLegend();
  renderQuestionnaire();
  if (profile && profile.compass && Object.keys(profile.compass).length) {
    renderCompass(profile.compass, true);
  }
  renderDelegates();
}

function renderLegend() {
  const legend = document.getElementById("axis-legend");
  if (!legend || !questionnaire) return;
  const axes = questionnaire.axes || {};
  legend.innerHTML = `<dl class="axis-legend">${Object.entries(axes).map(([axis, labels]) => `
    <div>
      <dt>${escHtml(axis)}</dt>
      <dd>${escHtml(labels.neg)} <span aria-hidden="true">·</span> ${escHtml(labels.pos)}</dd>
    </div>`).join("")}</dl>`;
}

function renderQuestionnaire() {
  qEl.innerHTML = questionnaire.questions.map(q => `
    <div class="q-item">
      <p class="q-statement">${escHtml(q.statement)}</p>
      <div class="q-scale" data-qid="${escHtml(q.id)}">
        ${questionnaire.scale.map(s => `
          <button class="q-opt ${answers[q.id] === s.value ? 'selected' : ''}"
                  data-value="${s.value}" title="${escHtml(s.label)}">
            ${escHtml(s.label)}
          </button>`).join('')}
      </div>
    </div>
  `).join('');

  qEl.querySelectorAll('.q-scale').forEach(scale => {
    const qid = scale.dataset.qid;
    scale.querySelectorAll('.q-opt').forEach(btn => {
      btn.addEventListener('click', () => {
        answers[qid] = parseInt(btn.dataset.value, 10);
        scale.querySelectorAll('.q-opt').forEach(b => b.classList.toggle('selected', b === btn));
      });
    });
  });
}

function renderCompass(compass, saved) {
  const axes = questionnaire.axes || {};
  const order = Object.keys(axes);
  compassReadout.innerHTML = order.map((axis) => {
    const a = axes[axis] || { neg: "low", pos: "high" };
    const raw = compass && typeof compass[axis] === "number" ? compass[axis] : 0;
    const pct = ((raw + 1) / 2) * 100;
    return `<div class="axis-row">
      <span class="axis-neg">${escHtml(a.neg)}</span>
      <div class="axis-track"><div class="axis-fill" style="left:${pct}%"></div></div>
      <span class="axis-pos">${escHtml(a.pos)}</span>
    </div>`;
  }).join("");
  compassReadout.classList.remove("hidden");
  compassStatus.textContent = saved ? "Saved" : "Not saved";
}

document.getElementById("compass-clear").addEventListener("click", () => {
  Object.keys(answers).forEach((key) => delete answers[key]);
  renderQuestionnaire();
  compassStatus.textContent = "Not saved";
  saveStatus.textContent = "Answers cleared on this page. Save to store that.";
});

// ── Save compass + values ─────────────────────────────────────────────────────

saveBtn.addEventListener('click', async () => {
  saveBtn.disabled = true;
  saveStatus.textContent = 'Saving…';
  try {
    const res = await apiFetch('/api/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questionnaire_answers: answers, values: valuesInput.value }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Save failed');
    const data = await res.json();
    profile = data.profile;
    renderCompass(profile.compass, true);
    saveStatus.textContent = "Saved. Refresh this page and the same answers come back.";
    setTimeout(() => (saveStatus.textContent = ''), 2500);
  } catch (err) {
    saveStatus.textContent = `Error: ${err.message}`;
  } finally {
    saveBtn.disabled = false;
  }
});

// ── Delegates ─────────────────────────────────────────────────────────────────

document.getElementById('del-add').addEventListener('click', async () => {
  const issue = document.getElementById('del-issue').value.trim();
  const name = document.getElementById('del-name').value.trim();
  const note = document.getElementById('del-note').value.trim();
  if (!issue || !name) {
    saveStatus.textContent = 'Delegate needs both an issue area and a name.';
    return;
  }
  if (!profile) {
    saveStatus.textContent = 'Save your compass & values first, then add delegates.';
    return;
  }
  try {
    const res = await apiFetch('/api/profile/delegates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ issue_area: issue, name, note }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Add failed');
    const data = await res.json();
    profile = data.profile;
    document.getElementById('del-issue').value = '';
    document.getElementById('del-name').value = '';
    document.getElementById('del-note').value = '';
    renderDelegates();
  } catch (err) {
    saveStatus.textContent = `Error: ${err.message}`;
  }
});

async function revokeDelegate(issue) {
  if (!confirm(`Revoke your delegate for "${issue}"? This takes effect immediately.`)) return;
  try {
    const res = await apiFetch(`/api/profile/delegates/${encodeURIComponent(issue)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Revoke failed');
    profile = (await res.json()).profile;
    renderDelegates();
  } catch (err) {
    saveStatus.textContent = `Error: ${err.message}`;
  }
}

function renderDelegates() {
  const dels = (profile && profile.delegates) || [];
  delegatesCount.textContent = dels.length;
  if (!dels.length) {
    delegatesList.innerHTML = '<p class="muted" style="margin-top:14px">No delegates yet.</p>';
    return;
  }
  delegatesList.innerHTML = dels.map(d => {
    const pending = !d.active;
    const status = pending
      ? `<span class="sig-badge sig-medium">pending · ${fmtRemaining(d.pending_seconds_remaining)}</span>`
      : `<span class="sig-badge sig-low">active</span>`;
    const srcCount = (d.sources || []).length;
    const sources = (d.sources || []).map(s => `<div class="source-row">
        <div class="source-head">
          <span class="source-title">${escHtml(s.title)}</span>
          <button class="btn-link src-remove" data-issue="${escHtml(d.issue_area)}" data-src="${escHtml(s.id)}">remove</button>
        </div>
        <p class="source-text muted">${escHtml(s.text)}</p>
      </div>`).join('');
    return `<div class="delegate-item">
      <button class="btn-link revoke" data-issue="${escHtml(d.issue_area)}">Revoke</button>
      <div class="delegate-main">
        <span class="delegate-issue">${escHtml(d.issue_area)}</span>
        <span class="delegate-name">${escHtml(d.name)}</span>
        ${status}
      </div>
      ${d.note ? `<p class="muted">${escHtml(d.note)}</p>` : ''}
      <div class="delegate-sources">
        <div class="sources-label">${srcCount ? `Recorded positions (${srcCount}) — your Avatar cites these` : 'No recorded positions yet — your Avatar approximates from general knowledge (lower confidence)'}</div>
        ${sources}
        <details class="add-source">
          <summary>+ Add recorded position</summary>
          <input type="text" class="title-field full-width src-title" placeholder="Source title (e.g. '2023 statement on surveillance')">
          <textarea class="src-text" rows="3" placeholder="Paste the delegate's actual position or quote..."></textarea>
          <button class="btn-secondary src-add" data-issue="${escHtml(d.issue_area)}">Attach source</button>
        </details>
      </div>
    </div>`;
  }).join('');

  delegatesList.querySelectorAll('.revoke').forEach(btn =>
    btn.addEventListener('click', () => revokeDelegate(btn.dataset.issue)));
  delegatesList.querySelectorAll('.src-add').forEach(btn =>
    btn.addEventListener('click', () => {
      const item = btn.closest('.delegate-item');
      addSource(btn.dataset.issue, item.querySelector('.src-title').value, item.querySelector('.src-text').value);
    }));
  delegatesList.querySelectorAll('.src-remove').forEach(btn =>
    btn.addEventListener('click', () => removeSource(btn.dataset.issue, btn.dataset.src)));
}

async function addSource(issue, title, text) {
  if (!text.trim()) { saveStatus.textContent = 'Source text is required.'; return; }
  try {
    const res = await apiFetch(`/api/profile/delegates/${encodeURIComponent(issue)}/sources`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, text }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Add source failed');
    profile = (await res.json()).profile;
    renderDelegates();
  } catch (err) { saveStatus.textContent = `Error: ${err.message}`; }
}

async function removeSource(issue, srcId) {
  try {
    const res = await apiFetch(`/api/profile/delegates/${encodeURIComponent(issue)}/sources/${encodeURIComponent(srcId)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Remove failed');
    profile = (await res.json()).profile;
    renderDelegates();
  } catch (err) { saveStatus.textContent = `Error: ${err.message}`; }
}

function fmtRemaining(seconds) {
  if (seconds <= 0) return 'active soon';
  const days = Math.floor(seconds / 86400);
  if (days >= 1) return `${days}d left`;
  const hours = Math.floor(seconds / 3600);
  if (hours >= 1) return `${hours}h left`;
  const mins = Math.floor(seconds / 60);
  return `${mins}m left`;
}

function escHtml(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

init();
