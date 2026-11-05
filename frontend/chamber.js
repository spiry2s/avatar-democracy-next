/* Chamber — the Proxy Chamber population dashboard */

const AXES = {
  economic:    { neg: 'state-led / redistributive', pos: 'free-market' },
  social:      { neg: 'progressive', pos: 'traditional' },
  liberty:     { neg: 'security / order', pos: 'civil liberties' },
  environment: { neg: 'climate-priority', pos: 'growth-priority' },
  foreign:     { neg: 'non-interventionist', pos: 'interventionist' },
  governance:  { neg: 'centralized / federal', pos: 'localized / states' },
};

const ARCH = {
  libertarian:   { c: '#f5c542', label: 'Libertarian' },
  progressive:   { c: '#4bdc8a', label: 'Progressive' },
  conservative:  { c: '#5b7fff', label: 'Conservative' },
  populist_left: { c: '#ff6b6b', label: 'Populist left' },
  centrist:      { c: '#9aa0b4', label: 'Centrist' },
};

let dist = null;
let operatorCompass = null;
let axisX = 'economic';
let axisY = 'social';

async function init() {
  const [p, prof] = await Promise.all([
    apiFetch('/api/population').then(r => r.json()),
    apiFetch('/api/profile').then(r => r.json()).catch(() => ({ profile: null })),
  ]);
  dist = p;
  operatorCompass = prof.profile ? prof.profile.compass : null;
  document.getElementById('pop-size').value = dist.size;
  fillAxisPickers();
  renderAll();
}

function renderAll() {
  renderStat();
  renderArchetypes();
  renderAxisMeans();
  renderScatter();
}

function renderStat() {
  const total = dist.size + (operatorCompass ? 1 : 0);
  document.getElementById('stat-total').textContent = total.toLocaleString();
  document.getElementById('stat-caption').textContent = operatorCompass
    ? `registered Avatars — you + ${dist.size.toLocaleString()} synthetic citizens`
    : 'registered Avatars (synthetic population)';
}

function fillAxisPickers() {
  const xs = document.getElementById('axis-x');
  const ys = document.getElementById('axis-y');
  const opts = (dist.axes || Object.keys(AXES)).map(a => `<option value="${a}">${a}</option>`).join('');
  xs.innerHTML = opts; ys.innerHTML = opts;
  xs.value = axisX; ys.value = axisY;
  xs.addEventListener('change', () => { axisX = xs.value; renderScatter(); });
  ys.addEventListener('change', () => { axisY = ys.value; renderScatter(); });
}

function renderArchetypes() {
  const total = dist.size || 1;
  const entries = Object.entries(dist.archetypes || {}).sort((a, b) => b[1] - a[1]);
  document.getElementById('archetypes').innerHTML = entries.map(([k, v]) => {
    const a = ARCH[k] || { c: '#9aa0b4', label: k };
    const pct = (v / total) * 100;
    return `<div class="arch-row">
      <span class="arch-swatch" style="background:${a.c}"></span>
      <span class="arch-name">${a.label}</span>
      <div class="progress-track arch-track"><div class="progress-fill" style="width:${pct}%;background:${a.c}"></div></div>
      <span class="arch-count">${v} · ${pct.toFixed(0)}%</span>
    </div>`;
  }).join('');
}

function renderAxisMeans() {
  const means = dist.axis_means || {};
  document.getElementById('axis-means').innerHTML = Object.keys(AXES).map(axis => {
    const v = means[axis] ?? 0;
    const a = AXES[axis];
    const pct = ((v + 1) / 2) * 100;
    return `<div class="axis-row">
      <span class="axis-neg">${a.neg}</span>
      <div class="axis-track"><div class="axis-fill" style="left:${pct}%"></div></div>
      <span class="axis-pos">${a.pos}</span>
    </div>`;
  }).join('');
}

function renderScatter() {
  const svg = document.getElementById('scatter');
  const ax = AXES[axisX], ay = AXES[axisY];

  const pt = (vx, vy) => [((vx + 1) / 2) * 100, (1 - ((vy + 1) / 2)) * 100];

  const dots = (dist.citizens || []).map(c => {
    const [x, y] = pt((c.compass || {})[axisX] ?? 0, (c.compass || {})[axisY] ?? 0);
    const col = (ARCH[c.archetype] || { c: '#9aa0b4' }).c;
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="1.3" fill="${col}" fill-opacity="0.75"/>`;
  }).join('');

  let op = '';
  if (operatorCompass) {
    const [x, y] = pt(operatorCompass[axisX] ?? 0, operatorCompass[axisY] ?? 0);
    op = `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.8" fill="#ffffff" stroke="#0f1117" stroke-width="0.7"/>`;
  }

  const grid = `<line x1="50" y1="0" x2="50" y2="100" stroke="currentColor" stroke-opacity="0.15" stroke-width="0.4"/>
    <line x1="0" y1="50" x2="100" y2="50" stroke="currentColor" stroke-opacity="0.15" stroke-width="0.4"/>`;
  svg.innerHTML = grid + dots + op;

  const wrap = document.getElementById('scatter-wrap');
  wrap.querySelectorAll('.sc-label').forEach(e => e.remove());
  addLabel(wrap, 'top', ay.pos);
  addLabel(wrap, 'bottom', ay.neg);
  addLabel(wrap, 'left', ax.neg);
  addLabel(wrap, 'right', ax.pos);

  const legend = Object.entries(ARCH).map(([k, a]) =>
    `<span class="leg-item"><span class="arch-swatch" style="background:${a.c}"></span>${a.label}</span>`).join('');
  document.getElementById('scatter-legend').innerHTML = legend +
    (operatorCompass ? `<span class="leg-item"><span class="arch-swatch" style="background:#fff;border:1px solid #0f1117"></span>You</span>` : '');
}

function addLabel(wrap, pos, text) {
  const s = document.createElement('span');
  s.className = `sc-label ${pos}`;
  s.textContent = text;
  wrap.appendChild(s);
}

document.getElementById('regen-btn').addEventListener('click', async () => {
  const size = parseInt(document.getElementById('pop-size').value, 10) || 200;
  const btn = document.getElementById('regen-btn');
  const status = document.getElementById('regen-status');
  btn.disabled = true;
  status.textContent = 'Regenerating…';
  try {
    const res = await apiFetch('/api/population/regenerate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ size }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Regenerate failed');
    dist = await res.json();
    renderAll();
    status.textContent = 'Done ✓';
    setTimeout(() => (status.textContent = ''), 2000);
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});

init();
