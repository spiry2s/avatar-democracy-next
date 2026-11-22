/* History — render the audit log */

const auditBody = document.getElementById('audit-body');
const auditChain = document.getElementById('audit-chain');

const LABELS = {
  profile_created: { icon: '◆', text: 'Avatar configured' },
  profile_updated: { icon: '✎', text: 'Compass & values updated' },
  delegate_added: { icon: '+', text: 'Delegate added (cooling-off)' },
  delegate_revoked: { icon: '−', text: 'Delegate revoked' },
  delegate_source_added: { icon: '🔖', text: 'Delegate position attached' },
  vote_cast: { icon: '🗳', text: 'Vote cast' },
  vote_overridden: { icon: '⤺', text: 'Vote overridden' },
  bill_proposed: { icon: '📄', text: 'Bill proposed' },
  bill_opened: { icon: '📣', text: 'Bill opened for endorsement' },
  bill_endorsed: { icon: '👍', text: 'Bill endorsed' },
  endorsement_revoked: { icon: '−', text: 'Endorsement revoked' },
  bill_frozen: { icon: '❄', text: 'Bill frozen (cooling-off)' },
  bill_voted: { icon: '🗳', text: 'Bill voted' },
  bill_passed: { icon: '✓', text: 'Bill passed' },
  bill_failed: { icon: '✗', text: 'Bill failed' },
  bill_conflict_flagged: { icon: '⚠', text: 'Cross-bill conflict flagged' },
  population_generated: { icon: '👥', text: 'Population generated' },
  vote_unverified_analysis: { icon: '⚠', text: 'Vote refused: analysis not verified' },
  delegate_source_removed: { icon: '−', text: 'Delegate source removed' },
};

async function load() {
  auditBody.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await apiFetch('/api/audit').then(r => r.json());
    render(data.events || []);
    renderChain(await apiFetch('/api/audit/verify').then(r => r.json()));
  } catch (err) {
    auditBody.innerHTML = `<p class="muted">Could not load audit log: ${escHtml(err.message)}</p>`;
  }
}

function renderChain(c) {
  if (!auditChain) return;
  if (!c || typeof c.ok !== 'boolean') { auditChain.innerHTML = ''; return; }
  const legacy = c.legacy ? ` ${c.legacy} older entr${c.legacy === 1 ? 'y was' : 'ies were'} written before chaining and cannot be checked.` : '';
  if (c.ok) {
    auditChain.innerHTML = `<p class="audit-chain ok">✓ History intact: ${escHtml(c.verified)} entr${c.verified === 1 ? 'y' : 'ies'} verified by hash chain.${legacy}`
      + (c.verified ? ` <span class="muted">Head: <code>${escHtml(String(c.head).slice(0, 16))}…</code></span>` : '') + '</p>';
  } else {
    auditChain.innerHTML = `<p class="audit-chain broken">⚠ History was altered at line ${escHtml(c.broken_at)}: ${escHtml(c.reason)}. Entries from that point cannot be trusted.</p>`;
  }
}

function render(events) {
  if (!events.length) {
    auditBody.innerHTML = '<p class="muted">No activity yet. Configure your Avatar and cast a vote to see history here.</p>';
    return;
  }
  auditBody.innerHTML = events.map(e => {
    const meta = LABELS[e.event_type] || { icon: '•', text: e.event_type };
    return `<div class="audit-item">
      <span class="audit-icon">${meta.icon}</span>
      <div class="audit-content">
        <div class="audit-head">
          <span class="audit-type">${escHtml(meta.text)}</span>
          <span class="muted audit-time">${fmtTime(e.timestamp)}</span>
        </div>
        <div class="audit-detail">${detail(e)}</div>
      </div>
    </div>`;
  }).join('');
}

function detail(e) {
  const d = e.details || {};
  switch (e.event_type) {
    case 'vote_cast': {
      const review = Number(d.needs_human_review) || 0;
      const reviewText = review
        ? `, <span class="badge-review">${review} needs human review (excluded from yes/no)</span>`
        : '';
      return `<strong>${escHtml(d.bill_title || 'a bill')}</strong> — ${d.sections} sections · `
        + `${d.yes} yes, ${d.no} no, ${d.abstain} abstain${reviewText}`;
    }
    case 'vote_overridden':
      return `<strong>${escHtml(d.bill_title || '')}</strong> · ${escHtml(d.section_id || '')}: `
        + `<span class="muted">${escHtml(d.from || '?')}</span> → <strong>${escHtml(d.to || '?')}</strong>`;
    case 'delegate_added':
      return `<strong>${escHtml(d.issue_area)}</strong> → ${escHtml(d.name)} `
        + `<span class="muted">(active ${fmtTime(d.active_at)})</span>`;
    case 'delegate_revoked':
      return `<strong>${escHtml(d.issue_area)}</strong>`;
    case 'delegate_source_added':
      return `<strong>${escHtml(d.issue_area)}</strong> · ${escHtml(d.title || 'position')}`;
    case 'profile_created':
    case 'profile_updated':
      return `<span class="muted">${d.values_len || 0} chars of values · compass set</span>`;
    case 'bill_proposed':
      return `<strong>${escHtml(d.title || '')}</strong> <span class="muted">(${escHtml(d.scope || '')})</span>`
        + (d.emergency ? ' <span class="badge-emergency">⚡ Emergency</span>' : '');
    case 'bill_conflict_flagged':
      return `<span class="muted">conflicts with ${d.count || 0} passed bill(s)</span>`;
    case 'bill_opened':
      return `<strong>${escHtml(d.title || '')}</strong> — open for endorsement`;
    case 'bill_endorsed':
      return `endorsed · <span class="muted">${d.total ?? 0} total</span>`;
    case 'endorsement_revoked':
      return `<span class="muted">endorsement withdrawn</span>`;
    case 'bill_frozen':
      return `frozen at v${d.frozen_version ?? '?'} <span class="muted">· cooling-off until ${fmtTime(d.cooling_off_until)}</span>`;
    case 'bill_voted':
      return `<strong>${escHtml(d.title || '')}</strong> — ${d.sections_passed}/${d.sections_total} sections passed`
        + (d.population ? ` <span class="muted">(chamber of ${Number(d.population).toLocaleString()})</span>` : '');
    case 'bill_passed':
    case 'bill_failed':
      return `<strong>${escHtml(d.title || '')}</strong>`;
    case 'population_generated':
      return `<span class="muted">${Number(d.size || 0).toLocaleString()} synthetic citizens</span>`;
    default:
      return `<span class="muted">${escHtml(JSON.stringify(d))}</span>`;
  }
}

function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function escHtml(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

document.getElementById('refresh-btn').addEventListener('click', load);
load();
