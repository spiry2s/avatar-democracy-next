/*
 * Standalone in-app backend: a JavaScript port of the FastAPI server
 * (main.py + backend/*.py). The fetch shim routes /api/* calls here, so the
 * unmodified frontend works with no PC server. State lives in localStorage
 * (ADStore); Claude is called directly (ADLlm) with the citizen's own key.
 *
 * Porting rule: request/response shapes, audit event names, and lifecycle
 * semantics mirror the Python byte-for-byte wherever the UI can observe them.
 */
(function () {
  'use strict';

  var P = window.ADPrompts;
  var AXIS_NAMES = Object.keys(P.AXES);

  function now() { return Date.now() / 1000; }

  function httpError(status, detail) {
    var e = new Error(detail);
    e.status = status;
    return e;
  }

  function uuidHex8() {
    var s = '';
    for (var i = 0; i < 8; i++) s += '0123456789abcdef'[Math.floor(Math.random() * 16)];
    return s;
  }

  function gauss(mu, sigma) {
    var u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return mu + sigma * Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
  }

  function clamp1(x) { return Math.max(-1.0, Math.min(1.0, x)); }
  function round3(x) { return Math.round(x * 1000) / 1000; }

  // ── questionnaire.py ────────────────────────────────────────────────────────

  function publicQuestionnaire() {
    return {
      scale: P.SCALE,
      questions: P.QUESTIONS.map(function (q) { return { id: q.id, statement: q.statement }; }),
      axes: P.AXES
    };
  }

  function scoreAnswers(answers) {
    var totals = {}, counts = {};
    AXIS_NAMES.forEach(function (a) { totals[a] = 0; counts[a] = 0; });
    var byId = {};
    P.QUESTIONS.forEach(function (q) { byId[q.id] = q; });
    for (var qid in (answers || {})) {
      var q = byId[qid];
      if (!q) continue;
      var val = parseInt(answers[qid], 10);
      if (isNaN(val)) continue;
      val = Math.max(-2, Math.min(2, val));
      totals[q.axis] += val * q.direction;
      counts[q.axis] += 1;
    }
    var compass = {};
    AXIS_NAMES.forEach(function (axis) {
      compass[axis] = counts[axis] === 0 ? 0.0 : round3(totals[axis] / (2 * counts[axis]));
    });
    return compass;
  }

  function describeCompass(compass) {
    var lines = [];
    for (var axis in compass) {
      var v = compass[axis];
      var labels = P.AXES[axis] || { neg: 'low', pos: 'high' };
      var lean;
      if (Math.abs(v) < 0.15) {
        lean = 'balanced / no strong lean';
      } else {
        var pole = v > 0 ? labels.pos : labels.neg;
        lean = (Math.abs(v) >= 0.6 ? 'strongly' : 'leans') + ' ' + pole;
      }
      var signed = (v >= 0 ? '+' : '') + v.toFixed(2);
      lines.push('  - ' + axis + ': ' + signed + ' (' + lean + ')');
    }
    return lines.join('\n');
  }

  // ── profile.py ──────────────────────────────────────────────────────────────

  function newProfile() {
    var t = now();
    return { citizen_id: 'local', created_at: t, updated_at: t, compass: {}, questionnaire_answers: {}, values: '', delegates: [] };
  }

  function delegateIsActive(d, t) { return (t === undefined ? now() : t) >= (d.active_at || 0); }

  function delegateToDict(d) {
    return {
      issue_area: d.issue_area, name: d.name, note: d.note || '',
      added_at: d.added_at, active_at: d.active_at, sources: d.sources || [],
      active: delegateIsActive(d),
      pending_seconds_remaining: Math.max(0, Math.floor((d.active_at || 0) - now()))
    };
  }

  function profileToDict(p) {
    return {
      citizen_id: p.citizen_id, created_at: p.created_at, updated_at: p.updated_at,
      compass: p.compass, questionnaire_answers: p.questionnaire_answers,
      values: p.values, delegates: (p.delegates || []).map(delegateToDict)
    };
  }

  function addDelegate(p, issueArea, name, note) {
    issueArea = (issueArea || '').trim().toLowerCase();
    name = (name || '').trim();
    if (!issueArea || !name) throw httpError(400, 'issue_area and name are required');
    var t = now();
    var delegate = {
      issue_area: issueArea, name: name, note: (note || '').trim(),
      added_at: t, active_at: t + window.ADStore.constants().delegateCoolingOffSeconds, sources: []
    };
    p.delegates = (p.delegates || []).filter(function (d) { return d.issue_area !== issueArea; });
    p.delegates.push(delegate);
    p.updated_at = t;
    return delegate;
  }

  // ── audit ───────────────────────────────────────────────────────────────────

  function logEvent(type, details) { window.ADStore.logEvent(type, details); }

  // ── summarizer.py ───────────────────────────────────────────────────────────

  async function analyzeBill(billText) {
    if (!billText || !billText.trim()) throw httpError(400, 'Bill text is empty');
    var c = window.ADStore.constants();
    if (billText.length > c.maxSinglePassChars) {
      billText = billText.slice(0, c.maxSinglePassChars) + '\n\n[BILL TRUNCATED FOR LENGTH]';
    }
    var s = window.ADStore.settings();
    var text = await window.ADLlm.call({
      model: s.model, maxTokens: 16000, thinking: true,
      system: P.SUMMARIZER_SYSTEM, prompt: P.ANALYSIS_PROMPT + billText
    });
    var data;
    try {
      data = window.ADLlm.parseJson(text, 'analysis');
    } catch (e) {
      throw httpError(502, 'Analysis produced unparseable output: ' + e.message);
    }
    return {
      title: data.title || 'Untitled bill',
      plain_summary: data.plain_summary || '',
      sections: data.sections || [],
      beneficiaries: data.beneficiaries || { benefits: [], costs: [], regulated: [] },
      red_flags: data.red_flags || [],
      comparable_laws: data.comparable_laws || [],
      open_questions: data.open_questions || [],
      metadata: data.metadata || {}
    };
  }

  // ── avatar.py ───────────────────────────────────────────────────────────────

  function buildConfigBlock(p) {
    var lines = ['CITIZEN CONFIGURATION', ''];
    lines.push('Political compass (fallback signal only):');
    lines.push(p.compass && Object.keys(p.compass).length ? describeCompass(p.compass) : '  (not set)');
    lines.push('');
    lines.push('Free-text values statement:');
    lines.push((p.values || '').trim() ? '  ' + p.values.trim() : '  (not set)');
    lines.push('');
    var active = (p.delegates || []).filter(function (d) { return delegateIsActive(d); });
    lines.push('Per-issue delegates (ACTIVE — follow these):');
    if (active.length) {
      active.forEach(function (d) {
        lines.push('  - ' + d.issue_area + ': ' + d.name + (d.note ? ' — note: ' + d.note : ''));
        if (d.sources && d.sources.length) {
          lines.push('      RECORDED POSITIONS for ' + d.name + ' — base your vote on these and cite them by title:');
          d.sources.forEach(function (s) {
            lines.push('        • [' + (s.title || 'position') + '] ' + (s.text || ''));
          });
        } else {
          lines.push('      (no recorded positions attached — approximate from general knowledge and lower confidence)');
        }
      });
    } else {
      lines.push('  (none active)');
    }
    var pending = (p.delegates || []).filter(function (d) { return !delegateIsActive(d); });
    if (pending.length) {
      lines.push('');
      lines.push('Per-issue delegates (PENDING cooling-off — do NOT follow yet):');
      pending.forEach(function (d) { lines.push('  - ' + d.issue_area + ': ' + d.name); });
    }
    return lines.join('\n');
  }

  function buildBillBlock(analysis) {
    var lines = ['BILL ANALYSIS: ' + (analysis.title || 'Untitled bill'), ''];
    lines.push('Plain summary: ' + (analysis.plain_summary || ''));
    lines.push('');
    var sections = analysis.sections || [];
    lines.push('SECTIONS TO VOTE ON (' + sections.length + '):');
    if (!sections.length) {
      lines.push("  (no sections were identified — treat the whole bill as one section with section_id 'whole_bill')");
    }
    sections.forEach(function (s) {
      lines.push('');
      lines.push('  [' + (s.section_id || '?') + '] ' + (s.heading || ''));
      lines.push('    ' + (s.summary || ''));
      if (s.significance) lines.push('    significance: ' + s.significance);
    });
    var redFlags = analysis.red_flags || [];
    if (redFlags.length) {
      lines.push('');
      lines.push('RED FLAGS surfaced by the independent analyst:');
      redFlags.forEach(function (f) {
        lines.push('  - [' + (f.severity || '?') + '] ' + (f.type || '') + ' in ' + (f.section || '?') + ': ' + (f.description || ''));
      });
    }
    var benef = analysis.beneficiaries || {};
    if (benef.benefits || benef.costs || benef.regulated) {
      lines.push('');
      lines.push('WHO IS AFFECTED:');
      if (benef.benefits && benef.benefits.length) lines.push('  benefits: ' + benef.benefits.join(', '));
      if (benef.costs && benef.costs.length) lines.push('  costs/pays: ' + benef.costs.join(', '));
      if (benef.regulated && benef.regulated.length) lines.push('  regulated: ' + benef.regulated.join(', '));
    }
    return lines.join('\n');
  }

  var POS_RE = /position['"]?\s*[:=]\s*['"]?(yes|no|abstain)/i;

  function coercePosition(value) {
    if (value && typeof value === 'object' && !Array.isArray(value)) value = value.position || '';
    var s = String(value).trim().toLowerCase();
    if (s === 'yes' || s === 'no' || s === 'abstain') return s;
    var m = POS_RE.exec(s);
    return m ? m[1].toLowerCase() : '';
  }

  function normKey(sectionId) {
    var s = String(sectionId).toLowerCase().replace(/[^a-z0-9]+/g, ' ');
    s = s.replace(/\b(?:section|sec)\b/g, ' ');
    return s.replace(/\s+/g, '');
  }

  function findDivergences(primary, checker) {
    var normChecker = {};
    for (var k in checker) normChecker[normKey(k)] = checker[k];
    var diverged = {};
    for (var sectionId in primary) {
      var other = normChecker[normKey(sectionId)];
      if (other !== undefined && other !== String(primary[sectionId]).trim().toLowerCase()) {
        diverged[sectionId] = true;
      }
    }
    return diverged;
  }

  async function crossCheck(analysis, profile) {
    var s = window.ADStore.settings();
    var prompt = buildConfigBlock(profile) + '\n\n---\n\n' + buildBillBlock(analysis) + '\n\n---\n' + P.CHECK_INSTRUCTIONS;
    var text = await window.ADLlm.call({
      model: s.checkerModel, maxTokens: 2000, system: P.AVATAR_SYSTEM, prompt: prompt
    });
    var data = window.ADLlm.parseJson(text, 'cross-check');
    var out = {};
    for (var k in data) {
      var pos = coercePosition(data[k]);
      if (pos) out[normKey(String(k))] = pos;
    }
    return out;
  }

  function delegateFollowedSections(profile, sectionVotes) {
    var grounded = {};
    (profile.delegates || []).forEach(function (d) {
      if (delegateIsActive(d) && d.sources && d.sources.length) {
        grounded[d.name.trim().toLowerCase()] = d.sources;
      }
    });
    var out = [];
    sectionVotes.forEach(function (sv) {
      var basis = sv.basis || '';
      if (basis.indexOf('delegate:') === 0) {
        var name = basis.slice('delegate:'.length).trim();
        if (grounded[name.toLowerCase()]) {
          out.push({ section_id: sv.section_id, delegate: name, sources: grounded[name.toLowerCase()], position: sv.position || 'abstain' });
        }
      }
    });
    return out;
  }

  async function detectDrift(profile, analysis, sectionVotes) {
    var targets = delegateFollowedSections(profile, sectionVotes);
    if (!targets.length) return {};
    var s = window.ADStore.settings();
    var summaries = {};
    (analysis.sections || []).forEach(function (sec) { summaries[String(sec.section_id)] = sec.summary || ''; });
    var lines = [
      'For each item, decide ONLY how the named delegate would vote on that section',
      'based strictly on their recorded positions below — not your own opinion.',
      'Output ONLY JSON mapping section_id to a position: {"<section_id>": "yes|no|abstain"}.',
      ''
    ];
    targets.forEach(function (t) {
      lines.push('[' + t.section_id + '] delegate: ' + t.delegate);
      lines.push('  section: ' + (summaries[String(t.section_id)] || ''));
      t.sources.forEach(function (src) {
        lines.push('  recorded position [' + (src.title || '') + ']: ' + (src.text || ''));
      });
      lines.push('');
    });
    var text = await window.ADLlm.call({ model: s.checkerModel, maxTokens: 1000, prompt: lines.join('\n') });
    var data = window.ADLlm.parseJson(text, 'drift check');
    var expected = {};
    for (var k in data) {
      var pos = coercePosition(data[k]);
      if (pos) expected[normKey(String(k))] = pos;
    }
    var cast = {};
    targets.forEach(function (t) { cast[t.section_id] = t.position; });
    return findDivergences(cast, expected);
  }

  async function castVote(analysis, profile) {
    var s = window.ADStore.settings();
    var prompt = buildConfigBlock(profile) + '\n\n---\n\n' + buildBillBlock(analysis) + '\n\n---\n' + P.VOTE_INSTRUCTIONS;
    var text = await window.ADLlm.call({
      model: s.model, maxTokens: 16000, thinking: true, system: P.AVATAR_SYSTEM, prompt: prompt
    });
    var data;
    try {
      data = window.ADLlm.parseJson(text, 'Avatar response');
    } catch (e) {
      throw httpError(502, 'Avatar produced unparseable output: ' + e.message);
    }

    var sectionVotes = (data.section_votes || []).map(function (v) {
      return {
        section_id: String(v.section_id !== undefined ? v.section_id : '?'),
        heading: v.heading || '',
        position: v.position || 'abstain',
        basis: v.basis || 'compass',
        reasoning: v.reasoning || '',
        confidence: v.confidence || 'low',
        divergent: false,
        delegate_drift: false
      };
    });

    // Capture defense: cross-check with a second model. Best-effort.
    if (s.multiModelCheck && sectionVotes.length) {
      try {
        var checker = await crossCheck(analysis, profile);
        var primary = {};
        sectionVotes.forEach(function (sv) { primary[sv.section_id] = sv.position; });
        var diverged = findDivergences(primary, checker);
        sectionVotes.forEach(function (sv) {
          if (diverged[sv.section_id]) { sv.divergent = true; sv.confidence = 'low'; }
        });
      } catch (e) { /* a checker failure must never break the primary vote */ }
    }

    // Capture defense: delegate drift. Best-effort.
    if (s.delegateDriftCheck && sectionVotes.length) {
      try {
        var drifted = await detectDrift(profile, analysis, sectionVotes.map(function (sv) {
          return { section_id: sv.section_id, basis: sv.basis, position: sv.position };
        }));
        sectionVotes.forEach(function (sv) { if (drifted[sv.section_id]) sv.delegate_drift = true; });
      } catch (e) { /* best-effort */ }
    }

    return {
      bill_title: analysis.title || 'Untitled bill',
      section_votes: sectionVotes,
      recommendation: data.recommendation || '',
      would_pass_sections: data.would_pass_sections || [],
      key_tension: data.key_tension || ''
    };
  }

  // ── chamber.py ──────────────────────────────────────────────────────────────

  function deterministicVote(compass, tag, tau) {
    var align = 0, norm = 0;
    AXIS_NAMES.forEach(function (axis) {
      var t = Number(tag[axis] || 0);
      align += Number(compass[axis] || 0) * t;
      norm += Math.abs(t);
    });
    var score = norm > 0 ? align / norm : 0;
    if (score > tau) return 'yes';
    if (score < -tau) return 'no';
    return 'abstain';
  }

  function aggregate(analysis, operatorVote, population, sectionTags, tau) {
    var opPositions = {};
    if (operatorVote) {
      (operatorVote.section_votes || []).forEach(function (sv) {
        opPositions[String(sv.section_id)] = sv.position || 'abstain';
      });
    }
    var tallies = [];
    (analysis.sections || []).forEach(function (section) {
      var sid = String(section.section_id !== undefined ? section.section_id : '?');
      var tag = sectionTags[sid] || {};
      var counts = { yes: 0, no: 0, abstain: 0 };
      var byArchetype = {};
      if (operatorVote) counts[opPositions[sid] || 'abstain'] += 1;
      population.forEach(function (citizen) {
        var v = deterministicVote(citizen.compass || {}, tag, tau);
        counts[v] += 1;
        var bucket = byArchetype[citizen.archetype || '?'] || (byArchetype[citizen.archetype || '?'] = { yes: 0, no: 0, abstain: 0 });
        bucket[v] += 1;
      });
      tallies.push({
        section_id: sid, heading: section.heading || '',
        yes: counts.yes, no: counts.no, abstain: counts.abstain,
        by_archetype: byArchetype
      });
    });
    return tallies;
  }

  function axisDoc() {
    return AXIS_NAMES.map(function (axis) {
      var lab = P.AXES[axis];
      return '  - ' + axis + ': -1 = advances "' + lab.neg + '", +1 = advances "' + lab.pos + '", 0 = irrelevant';
    }).join('\n');
  }

  async function tagSections(analysis) {
    if (!(analysis.sections || []).length) return {};
    var s = window.ADStore.settings();
    var lines = [
      'Tag each section of this bill on six political axes. For each section and each axis,',
      'give a number in [-1, 1] for which pole the section ADVANCES if enacted (0 if the axis',
      'is irrelevant to that section). Judge the actual mechanism, not the bill\'s framing.',
      '',
      'Axes:',
      axisDoc(),
      '',
      'Bill: ' + (analysis.title || ''),
      'Sections:'
    ];
    (analysis.sections || []).forEach(function (sec) {
      lines.push('  [' + (sec.section_id || '?') + '] ' + (sec.heading || '') + ': ' + (sec.summary || ''));
    });
    lines.push('');
    lines.push('Output ONLY JSON: {"<section_id>": {"economic": 0.0, "social": 0.0, "liberty": 0.0, "environment": 0.0, "foreign": 0.0, "governance": 0.0}, ...}. No prose, no fences.');

    var text = await window.ADLlm.call({ model: s.model, maxTokens: 8000, thinking: true, prompt: lines.join('\n') });
    var raw;
    try {
      raw = window.ADLlm.parseJson(text, 'axis tags');
    } catch (e) {
      throw httpError(502, e.message);
    }
    var tags = {};
    for (var sid in raw) {
      var vec = raw[sid];
      var clean = {};
      AXIS_NAMES.forEach(function (axis) {
        var v = (vec && typeof vec === 'object') ? Number(vec[axis] || 0) : 0;
        clean[axis] = Math.max(-1, Math.min(1, isNaN(v) ? 0 : v));
      });
      tags[String(sid)] = clean;
    }
    return tags;
  }

  // ── conflict.py ─────────────────────────────────────────────────────────────

  async function detectConflicts(analysis, passedBills) {
    if (!analysis || !passedBills.length) return [];
    var s = window.ADStore.settings();
    var sections = (analysis.sections || []).map(function (sec) { return sec.heading || ''; }).join('; ');
    var lines = [
      'A NEW bill is proposed. Identify which, if any, of the PREVIOUSLY PASSED bills it',
      'directly conflicts with — it contradicts, repeals, overrides, duplicates, or is',
      'legally incompatible with them. Be conservative: only flag genuine conflicts.',
      '',
      'NEW BILL: ' + (analysis.title || ''),
      '  summary: ' + (analysis.plain_summary || ''),
      '  sections: ' + sections,
      '',
      'PREVIOUSLY PASSED BILLS:'
    ];
    passedBills.forEach(function (b) {
      lines.push('  [' + b.id + '] ' + (b.title || '') + ': ' + (b.summary || ''));
    });
    lines.push('');
    lines.push('Output ONLY JSON: a list of {"bill_id": "<id>", "conflict": "<one sentence>"}.');
    lines.push('Return [] if there are no genuine conflicts. No prose, no markdown fences.');

    var text = await window.ADLlm.call({ model: s.checkerModel, maxTokens: 1000, prompt: lines.join('\n') });
    var data = window.ADLlm.parseJson(text, 'conflicts');
    if (data && !Array.isArray(data) && typeof data === 'object') data = data.conflicts || [];
    if (!Array.isArray(data)) return [];
    var titles = {};
    passedBills.forEach(function (b) { titles[b.id] = b.title || ''; });
    var out = [];
    data.forEach(function (item) {
      if (!item || typeof item !== 'object') return;
      var bid = String(item.bill_id || '').trim();
      if (titles.hasOwnProperty(bid)) {
        out.push({ bill_id: bid, title: titles[bid], conflict: String(item.conflict || '').trim() });
      }
    });
    return out;
  }

  // ── population.py ───────────────────────────────────────────────────────────

  function sampleCitizen(idx) {
    var names = Object.keys(P.ARCHETYPES);
    var archetype = names[Math.floor(Math.random() * names.length)];
    var center = P.ARCHETYPES[archetype];
    var compass = {};
    AXIS_NAMES.forEach(function (axis) {
      compass[axis] = round3(clamp1(center[axis] + gauss(0, 0.25)));
    });
    return { id: 'cit-' + String(idx).padStart(5, '0'), archetype: archetype, compass: compass };
  }

  function generatePopulation(size) {
    var n = size != null ? size : window.ADStore.settings().populationSize;
    n = Math.max(0, Math.min(20000, parseInt(n, 10) || 0));
    var citizens = [];
    for (var i = 0; i < n; i++) citizens.push(sampleCitizen(i));
    window.ADStore.savePopulation(citizens);
    return citizens;
  }

  function loadPopulation() {
    var pop = window.ADStore.population();
    return pop || generatePopulation();
  }

  function distribution(citizens) {
    citizens = citizens || loadPopulation();
    var counts = {}, sums = {};
    AXIS_NAMES.forEach(function (a) { sums[a] = 0; });
    citizens.forEach(function (c) {
      var key = c.archetype || '?';
      counts[key] = (counts[key] || 0) + 1;
      AXIS_NAMES.forEach(function (a) { sums[a] += Number((c.compass || {})[a] || 0); });
    });
    var n = citizens.length || 1;
    var means = {};
    AXIS_NAMES.forEach(function (a) { means[a] = round3(sums[a] / n); });
    return {
      size: citizens.length,
      archetypes: counts,
      axis_means: means,
      axes: AXIS_NAMES,
      citizens: citizens.map(function (c) { return { archetype: c.archetype || '?', compass: c.compass || {} }; })
    };
  }

  // ── bills.py ────────────────────────────────────────────────────────────────

  var VALID_SCOPES = ['ordinary', 'major', 'constitutional'];

  function totalEndorsements(bill) {
    return (bill.endorsements || []).length + (bill.simulated_endorsements || 0);
  }

  function billToDict(bill) {
    var d = {};
    for (var k in bill) d[k] = bill[k];
    d.total_endorsements = totalEndorsements(bill);
    if (bill.cooling_off_until) {
      d.cooling_off_remaining = Math.max(0, Math.floor(bill.cooling_off_until - now()));
    }
    return d;
  }

  function propose(title, text, scope, emergency) {
    if (!text || !text.trim()) throw httpError(400, 'Bill text is required');
    var c = window.ADStore.constants();
    scope = VALID_SCOPES.indexOf(scope) >= 0 ? scope : 'ordinary';
    var base = c.thresholds[scope] || 25;
    var threshold = emergency ? base * c.emergencyThresholdMultiplier : base;
    var t = now();
    return {
      id: 'bill-' + uuidHex8(),
      title: (title || 'Untitled bill').trim(),
      sponsor: 'local',
      scope: scope,
      state: 'draft',
      created_at: t,
      updated_at: t,
      versions: [{ version: 1, text: text, note: 'initial draft', created_at: t }],
      current_version: 1,
      endorsements: [],
      simulated_endorsements: 0,
      endorsement_threshold: Math.floor(threshold),
      cooling_off_until: null,
      frozen_version: null,
      analysis: null,
      vote: null,
      result: null,
      emergency: !!emergency,
      conflicts: []
    };
  }

  function addVersion(bill, text, note) {
    if (bill.state !== 'draft') throw httpError(400, 'Versions can only be edited while the bill is a draft');
    if (!text || !text.trim()) throw httpError(400, 'Version text is required');
    var v = bill.current_version + 1;
    bill.versions.push({ version: v, text: text, note: note || '', created_at: now() });
    bill.current_version = v;
    bill.updated_at = now();
  }

  function openForEndorsement(bill) {
    if (bill.state !== 'draft') throw httpError(400, 'Only a draft bill can be opened for endorsement');
    bill.state = 'endorsing';
    bill.updated_at = now();
  }

  function endorse(bill, citizenId) {
    if (bill.state !== 'endorsing') throw httpError(400, 'Bill is not open for endorsement');
    var exists = (bill.endorsements || []).some(function (e) { return e.citizen_id === citizenId; });
    if (exists) return; // idempotent
    var t = now();
    bill.endorsements.push({
      citizen_id: citizenId,
      created_at: t,
      revocable_until: t + window.ADStore.constants().endorsementRevocationSeconds
    });
    bill.updated_at = t;
  }

  function revokeEndorsement(bill, citizenId) {
    var t = now();
    for (var i = 0; i < (bill.endorsements || []).length; i++) {
      var e = bill.endorsements[i];
      if (e.citizen_id === citizenId) {
        if (t > (e.revocable_until || 0)) return false; // window passed; locked in
        bill.endorsements = bill.endorsements.filter(function (x) { return x.citizen_id !== citizenId; });
        bill.updated_at = t;
        return true;
      }
    }
    return false;
  }

  function freezeIfThresholdMet(bill) {
    if (bill.state !== 'endorsing') return false;
    if (totalEndorsements(bill) < bill.endorsement_threshold) return false;
    var c = window.ADStore.constants();
    bill.state = 'cooling_off';
    bill.frozen_version = bill.current_version;
    var cool = bill.emergency ? c.emergencyCoolingOffSeconds : c.billCoolingOffSeconds;
    bill.cooling_off_until = now() + cool;
    bill.updated_at = now();
    return true;
  }

  function refreshState(bill) {
    if (bill.state === 'cooling_off' && bill.cooling_off_until != null && now() >= bill.cooling_off_until) {
      bill.state = 'voting';
      bill.updated_at = now();
      return true;
    }
    return false;
  }

  function frozenText(bill) {
    var target = bill.frozen_version != null ? bill.frozen_version : bill.current_version;
    for (var i = 0; i < (bill.versions || []).length; i++) {
      if (bill.versions[i].version === target) return bill.versions[i].text;
    }
    return bill.versions && bill.versions.length ? bill.versions[bill.versions.length - 1].text : '';
  }

  function computeResult(sectionTallies, ratio) {
    var sections = [];
    var passedCount = 0, contested = 0;
    sectionTallies.forEach(function (t) {
      var decisive = t.yes + t.no;
      var isContested = decisive > 0;
      var passed = isContested && (t.yes / decisive) >= ratio;
      if (isContested) contested += 1;
      if (passed) passedCount += 1;
      var row = {};
      for (var k in t) row[k] = t[k];
      row.passed = passed;
      row.contested = isContested;
      sections.push(row);
    });
    return {
      per_section: sections,
      sections_passed: passedCount,
      sections_total: sections.length,
      sections_contested: contested,
      passed: contested > 0 && passedCount === contested
    };
  }

  function loadBill(billId) {
    var bill = window.ADStore.bills()[billId];
    return bill || null;
  }

  function listBills() {
    var all = window.ADStore.bills();
    var out = [];
    for (var id in all) out.push(all[id]);
    out.sort(function (a, b) { return b.created_at - a.created_at; });
    return out;
  }

  function persistRefresh(bill) {
    if (refreshState(bill)) window.ADStore.saveBill(bill);
    return bill;
  }

  async function freezeAndAnalyze(bill) {
    if (!freezeIfThresholdMet(bill)) return;
    logEvent('bill_frozen', {
      id: bill.id, frozen_version: bill.frozen_version,
      cooling_off_until: bill.cooling_off_until, emergency: bill.emergency
    });
    try {
      bill.analysis = await analyzeBill(frozenText(bill));
    } catch (e) {
      bill.analysis = null; // analysis will be retried lazily at vote time
    }
    var s = window.ADStore.settings();
    if (s.crossBillConflictCheck && bill.analysis) {
      try {
        var passed = listBills()
          .filter(function (b) { return b.state === 'passed' && b.id !== bill.id; })
          .map(function (b) {
            return { id: b.id, title: b.title, summary: (b.analysis || {}).plain_summary || b.title };
          });
        bill.conflicts = await detectConflicts(bill.analysis, passed);
        if (bill.conflicts.length) logEvent('bill_conflict_flagged', { id: bill.id, count: bill.conflicts.length });
      } catch (e) {
        bill.conflicts = [];
      }
    }
  }

  // ── extraction (standalone subset of extractors.py) ─────────────────────────

  async function extractFromForm(form) {
    var text = form.get('text');
    var url = form.get('url');
    var file = form.get('file');
    var provided = [text, url, file].filter(function (x) { return x !== null && x !== undefined && x !== ''; });
    if (provided.length === 0) throw httpError(400, 'Provide one of: text, url, or file');
    if (provided.length > 1) throw httpError(400, 'Provide only one of: text, url, or file (not multiple)');

    if (text) return String(text);
    if (url) {
      throw httpError(400, 'Fetching bills by URL is not available in the standalone app (browser cross-origin limits). Paste the bill text instead, or connect to a PC server.');
    }
    // file: a Blob/File from the upload control
    var name = (file.name || '').toLowerCase();
    var type = (file.type || '').toLowerCase();
    if (type.indexOf('pdf') >= 0 || name.slice(-4) === '.pdf') {
      throw httpError(400, 'PDF extraction is not available in the standalone app yet. Copy the bill text out of the PDF and paste it instead, or connect to a PC server.');
    }
    return await file.text();
  }

  // ── router ──────────────────────────────────────────────────────────────────

  function json(obj, status) {
    return new Response(JSON.stringify(obj), {
      status: status || 200,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  async function parseBody(opts) {
    var body = opts && opts.body;
    if (!body) return {};
    if (typeof body === 'string') {
      try { return JSON.parse(body); } catch (e) { return {}; }
    }
    return body; // FormData handled by callers that expect it
  }

  async function handle(path, opts) {
    var method = ((opts && opts.method) || 'GET').toUpperCase();
    var url = new URL(path, 'http://local');
    var p = url.pathname;
    var m;

    try {
      // ── health / questionnaire ──────────────────────────────────────────
      if (p === '/api/health' && method === 'GET') {
        return json({ status: 'ok', api_key_configured: !!window.ADStore.settings().apiKey, standalone: true });
      }
      if (p === '/api/questionnaire' && method === 'GET') {
        return json(publicQuestionnaire());
      }

      // ── analyze ─────────────────────────────────────────────────────────
      if (p === '/api/analyze' && method === 'POST') {
        if (!(opts && opts.body instanceof FormData)) throw httpError(400, 'Provide one of: text, url, or file');
        var billText;
        try {
          billText = await extractFromForm(opts.body);
        } catch (e) {
          if (e.status) throw e;
          throw httpError(400, 'Could not extract bill text: ' + e.message);
        }
        if (!billText || billText.trim().length < 100) {
          throw httpError(400, 'Extracted text is too short (' + (billText || '').length + ' chars). The source may not contain a bill or extraction failed.');
        }
        var analysis = await analyzeBill(billText);
        return json({ success: true, analysis: analysis, source_chars: billText.length });
      }

      // ── profile ─────────────────────────────────────────────────────────
      if (p === '/api/profile' && method === 'GET') {
        var prof = window.ADStore.profile();
        return json({ profile: prof ? profileToDict(prof) : null });
      }
      if (p === '/api/profile' && method === 'POST') {
        var req = await parseBody(opts);
        var existing = window.ADStore.profile();
        var isNew = existing === null;
        var prof2 = existing || newProfile();
        if (req.questionnaire_answers && Object.keys(req.questionnaire_answers).length) {
          prof2.questionnaire_answers = req.questionnaire_answers;
        }
        prof2.compass = scoreAnswers(prof2.questionnaire_answers);
        prof2.values = req.values || '';
        window.ADStore.saveProfile(prof2);
        logEvent(isNew ? 'profile_created' : 'profile_updated', { compass: prof2.compass, values_len: (prof2.values || '').length });
        return json({ profile: profileToDict(prof2) });
      }
      if (p === '/api/profile/delegates' && method === 'POST') {
        var dreq = await parseBody(opts);
        var prof3 = window.ADStore.profile();
        if (!prof3) throw httpError(400, 'Configure your Avatar before adding delegates.');
        var delegate = addDelegate(prof3, dreq.issue_area, dreq.name, dreq.note);
        window.ADStore.saveProfile(prof3);
        logEvent('delegate_added', {
          issue_area: delegate.issue_area, name: delegate.name, active_at: delegate.active_at,
          cooling_off_seconds: window.ADStore.constants().delegateCoolingOffSeconds
        });
        return json({ profile: profileToDict(prof3), added: delegateToDict(delegate) });
      }
      m = p.match(/^\/api\/profile\/delegates\/([^\/]+)$/);
      if (m && method === 'DELETE') {
        var issue = decodeURIComponent(m[1]).trim().toLowerCase();
        var prof4 = window.ADStore.profile();
        if (!prof4) throw httpError(400, 'No profile configured.');
        var before = (prof4.delegates || []).length;
        prof4.delegates = (prof4.delegates || []).filter(function (d) { return d.issue_area !== issue; });
        if (prof4.delegates.length === before) throw httpError(404, "No delegate for issue area '" + issue + "'.");
        window.ADStore.saveProfile(prof4);
        logEvent('delegate_revoked', { issue_area: issue });
        return json({ profile: profileToDict(prof4) });
      }
      m = p.match(/^\/api\/profile\/delegates\/([^\/]+)\/sources$/);
      if (m && method === 'POST') {
        var sreq = await parseBody(opts);
        var prof5 = window.ADStore.profile();
        if (!prof5) throw httpError(400, 'No profile configured.');
        var issue2 = decodeURIComponent(m[1]).trim().toLowerCase();
        var stext = (sreq.text || '').trim();
        if (!stext) throw httpError(400, 'Source text is required');
        var target = (prof5.delegates || []).filter(function (d) { return d.issue_area === issue2; })[0];
        if (!target) throw httpError(400, "No delegate for issue area '" + issue2 + "'");
        var src = { id: uuidHex8(), title: (sreq.title || '').trim() || 'position', text: stext, added_at: now() };
        target.sources = target.sources || [];
        target.sources.push(src);
        window.ADStore.saveProfile(prof5);
        logEvent('delegate_source_added', { issue_area: issue2, title: src.title });
        return json({ profile: profileToDict(prof5), added: src });
      }
      m = p.match(/^\/api\/profile\/delegates\/([^\/]+)\/sources\/([^\/]+)$/);
      if (m && method === 'DELETE') {
        var prof6 = window.ADStore.profile();
        if (!prof6) throw httpError(400, 'No profile configured.');
        var issue3 = decodeURIComponent(m[1]).trim().toLowerCase();
        var srcId = decodeURIComponent(m[2]);
        var found = false;
        (prof6.delegates || []).forEach(function (d) {
          if (d.issue_area === issue3 && d.sources) {
            var n0 = d.sources.length;
            d.sources = d.sources.filter(function (s) { return s.id !== srcId; });
            if (d.sources.length < n0) found = true;
          }
        });
        if (!found) throw httpError(404, 'Source not found.');
        window.ADStore.saveProfile(prof6);
        return json({ profile: profileToDict(prof6) });
      }

      // ── avatar ──────────────────────────────────────────────────────────
      if (p === '/api/avatar/vote' && method === 'POST') {
        var vreq = await parseBody(opts);
        var prof7 = window.ADStore.profile();
        if (!prof7) throw httpError(400, 'Configure your Avatar first (visit /avatar).');
        var vote = await castVote(vreq.analysis || {}, prof7);
        var yes = 0, no = 0, abstain = 0;
        vote.section_votes.forEach(function (v) {
          if (v.position === 'yes') yes++; else if (v.position === 'no') no++; else abstain++;
        });
        logEvent('vote_cast', {
          bill_title: vote.bill_title, sections: vote.section_votes.length,
          yes: yes, no: no, abstain: abstain
        });
        return json({ vote: vote });
      }
      if (p === '/api/avatar/override' && method === 'POST') {
        var oreq = await parseBody(opts);
        logEvent('vote_overridden', {
          bill_title: oreq.bill_title, section_id: oreq.section_id,
          from: oreq.previous_position || '', to: oreq.position
        });
        return json({ ok: true });
      }

      // ── audit ───────────────────────────────────────────────────────────
      if (p === '/api/audit' && method === 'GET') {
        var limit = parseInt(url.searchParams.get('limit') || '200', 10);
        var events = window.ADStore.auditLog().slice().reverse();
        if (limit) events = events.slice(0, limit);
        return json({ events: events });
      }

      // ── bills ───────────────────────────────────────────────────────────
      if (p === '/api/bills' && method === 'GET') {
        return json({ bills: listBills().map(function (b) { return billToDict(persistRefresh(b)); }) });
      }
      if (p === '/api/bills' && method === 'POST') {
        var breq = await parseBody(opts);
        if (!breq.text || !breq.text.trim()) throw httpError(400, 'Bill text is required');
        var bill = propose(breq.title, breq.text, breq.scope || 'ordinary', !!breq.emergency);
        window.ADStore.saveBill(bill);
        logEvent('bill_proposed', { id: bill.id, title: bill.title, scope: bill.scope, emergency: bill.emergency });
        return json({ bill: billToDict(bill) });
      }
      m = p.match(/^\/api\/bills\/([^\/]+)$/);
      if (m && method === 'GET') {
        var b1 = loadBill(m[1]);
        if (!b1) throw httpError(404, 'Bill not found');
        return json({ bill: billToDict(persistRefresh(b1)) });
      }
      m = p.match(/^\/api\/bills\/([^\/]+)\/versions$/);
      if (m && method === 'POST') {
        var b2 = loadBill(m[1]);
        if (!b2) throw httpError(404, 'Bill not found');
        var vq = await parseBody(opts);
        addVersion(b2, vq.text, vq.note || '');
        window.ADStore.saveBill(b2);
        return json({ bill: billToDict(b2) });
      }
      m = p.match(/^\/api\/bills\/([^\/]+)\/open$/);
      if (m && method === 'POST') {
        var b3 = loadBill(m[1]);
        if (!b3) throw httpError(404, 'Bill not found');
        openForEndorsement(b3);
        window.ADStore.saveBill(b3);
        logEvent('bill_opened', { id: b3.id, title: b3.title });
        return json({ bill: billToDict(b3) });
      }
      m = p.match(/^\/api\/bills\/([^\/]+)\/endorse$/);
      if (m && method === 'POST') {
        var b4 = loadBill(m[1]);
        if (!b4) throw httpError(404, 'Bill not found');
        endorse(b4, 'local');
        logEvent('bill_endorsed', { id: b4.id, total: totalEndorsements(b4) });
        await freezeAndAnalyze(b4);
        window.ADStore.saveBill(b4);
        return json({ bill: billToDict(b4) });
      }
      if (m && method === 'DELETE') {
        var b5 = loadBill(m[1]);
        if (!b5) throw httpError(404, 'Bill not found');
        if (!revokeEndorsement(b5, 'local')) {
          throw httpError(400, 'No revocable endorsement (not endorsed, or the 24h window has passed).');
        }
        window.ADStore.saveBill(b5);
        logEvent('endorsement_revoked', { id: b5.id });
        return json({ bill: billToDict(b5) });
      }
      m = p.match(/^\/api\/bills\/([^\/]+)\/simulate-endorsements$/);
      if (m && method === 'POST') {
        var b6 = loadBill(m[1]);
        if (!b6) throw httpError(404, 'Bill not found');
        if (b6.state !== 'endorsing') throw httpError(400, 'Bill is not open for endorsement');
        var sq = await parseBody(opts);
        b6.simulated_endorsements += Math.max(0, parseInt(sq.count || 1, 10) || 0);
        await freezeAndAnalyze(b6);
        window.ADStore.saveBill(b6);
        return json({ bill: billToDict(b6) });
      }
      m = p.match(/^\/api\/bills\/([^\/]+)\/vote$/);
      if (m && method === 'POST') {
        var b7 = loadBill(m[1]);
        if (!b7) throw httpError(404, 'Bill not found');
        persistRefresh(b7);
        if (b7.state !== 'voting') throw httpError(400, 'Bill is not open for voting (state: ' + b7.state + ').');
        var prof8 = window.ADStore.profile();
        if (!prof8) throw httpError(400, 'Configure your Avatar first (visit /avatar).');

        if (!b7.analysis) b7.analysis = await analyzeBill(frozenText(b7));
        var av = await castVote(b7.analysis, prof8);
        b7.vote = av;
        var pop = loadPopulation();
        var tau = window.ADStore.constants().chamberVoteTau;
        var tags = await tagSections(b7.analysis);
        var tallies = aggregate(b7.analysis, b7.vote, pop, tags, tau);

        b7.result = computeResult(tallies, window.ADStore.constants().sectionPassageRatio);
        b7.result.population_size = pop.length + 1; // + the operator
        b7.result.section_tags = tags;
        b7.state = b7.result.passed ? 'passed' : 'failed';
        window.ADStore.saveBill(b7);

        logEvent('bill_voted', {
          id: b7.id, title: b7.title,
          population: b7.result.population_size,
          sections_passed: b7.result.sections_passed,
          sections_total: b7.result.sections_total
        });
        logEvent(b7.result.passed ? 'bill_passed' : 'bill_failed', { id: b7.id, title: b7.title });
        return json({ bill: billToDict(b7) });
      }

      // ── population ──────────────────────────────────────────────────────
      if (p === '/api/population' && method === 'GET') {
        return json(distribution());
      }
      if (p === '/api/population/regenerate' && method === 'POST') {
        var rq = await parseBody(opts);
        var citizens = generatePopulation(rq.size != null ? rq.size : null);
        logEvent('population_generated', { size: citizens.length });
        return json(distribution(citizens));
      }

      throw httpError(404, 'Not found: ' + method + ' ' + p);
    } catch (e) {
      var status = e && e.status ? e.status : 500;
      return json({ detail: e && e.message ? e.message : 'Internal error' }, status);
    }
  }

  window.ADLocalApi = { handle: handle };
})();
