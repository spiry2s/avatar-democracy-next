/*
 * Standalone-mode shim. Injected (with the other app/*.js files) into every
 * frontend page served from APK assets. Two jobs:
 *
 * 1. Reroute the frontend's fetch('/api/...') calls to the in-app JS backend
 *    (ADLocalApi) instead of a network server. Everything else passes through.
 * 2. Provide the device settings UI (gear button): Anthropic API key, model
 *    choice, population size, demo timers, local-data wipe.
 *
 * Load order matters: store.js, prompts.js, llm.js, localapi.js, then this
 * file — llm.js captures the native fetch before this wrapper replaces it.
 */
(function () {
  'use strict';

  window.AVATAR_STANDALONE = true;

  // ── fetch rerouting ─────────────────────────────────────────────────────────

  var nativeFetch = window.fetch.bind(window);

  function pathOf(input) {
    var u = (input && typeof input === 'object' && 'url' in input) ? input.url : String(input);
    if (u.indexOf('/') === 0) return u;                 // relative: "/api/bills"
    try {
      var parsed = new URL(u, window.location.href);
      if (parsed.origin === window.location.origin) return parsed.pathname + parsed.search;
    } catch (e) { /* fall through */ }
    return null; // cross-origin → not ours
  }

  window.fetch = function (input, opts) {
    var path = pathOf(input);
    if (path && path.indexOf('/api/') === 0) {
      return window.ADLocalApi.handle(path, opts || {});
    }
    return nativeFetch(input, opts);
  };

  // ── settings UI ─────────────────────────────────────────────────────────────

  var MAIN_MODELS = [
    ['claude-opus-5-5', 'Claude Opus 5.5 (recommended)'],
    ['claude-sonnet-5-5', 'Claude Sonnet 5.5 (cheaper)'],
    ['claude-fable-5-1', 'Claude Fable 5.1 (most capable)']
  ];
  var CHECKER_MODELS = [
    ['claude-haiku-5-5', 'Claude Haiku 5.5 (recommended)'],
    ['claude-sonnet-5-5', 'Claude Sonnet 5.5']
  ];

  function el(tag, css, text) {
    var node = document.createElement(tag);
    if (css) node.style.cssText = css;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function buildPanel() {
    var s = window.ADStore.settings();

    var overlay = el('div',
      'position:fixed;inset:0;background:rgba(8,9,12,.82);z-index:99998;display:none;overflow:auto;');
    overlay.id = 'ad-settings-overlay';

    var panel = el('div',
      'max-width:430px;margin:7vh auto;background:#161a24;border:1px solid #2a3042;border-radius:14px;' +
      'padding:22px;color:#e8eaf0;font:14px/1.45 system-ui,sans-serif;');

    panel.appendChild(el('div', 'font-size:18px;font-weight:700;margin-bottom:4px;', 'Standalone settings'));
    panel.appendChild(el('div', 'color:#7c82a0;margin-bottom:16px;font-size:12.5px;',
      'This app runs entirely on your phone. Bills, your Avatar profile, and the audit log stay on this device. ' +
      'Claude is called directly with your own API key.'));

    function label(text) { return el('div', 'margin:12px 0 4px;color:#aab0c8;font-size:12px;', text); }
    var inputCss = 'width:100%;box-sizing:border-box;background:#0f1117;color:#e8eaf0;border:1px solid #2a3042;' +
      'border-radius:8px;padding:9px 10px;font-size:13px;';

    panel.appendChild(label('Anthropic API key (console.anthropic.com)'));
    var keyInput = el('input', inputCss);
    keyInput.type = 'password';
    keyInput.placeholder = 'sk-ant-...';
    keyInput.value = s.apiKey;
    panel.appendChild(keyInput);

    panel.appendChild(label('Main model (analysis + Avatar votes)'));
    var modelSel = el('select', inputCss);
    MAIN_MODELS.forEach(function (mm) {
      var o = el('option', '', mm[1]); o.value = mm[0];
      if (mm[0] === s.model) o.selected = true;
      modelSel.appendChild(o);
    });
    panel.appendChild(modelSel);

    panel.appendChild(label('Checker model (independent cross-check, capture defense)'));
    var checkerSel = el('select', inputCss);
    CHECKER_MODELS.forEach(function (mm) {
      var o = el('option', '', mm[1]); o.value = mm[0];
      if (mm[0] === s.checkerModel) o.selected = true;
      checkerSel.appendChild(o);
    });
    panel.appendChild(checkerSel);

    panel.appendChild(label('Synthetic population size (Proxy Chamber)'));
    var popInput = el('input', inputCss);
    popInput.type = 'number';
    popInput.min = '0'; popInput.max = '20000';
    popInput.value = String(s.populationSize);
    panel.appendChild(popInput);

    var demoWrap = el('label', 'display:flex;gap:8px;align-items:center;margin:14px 0 4px;cursor:pointer;');
    var demoCheck = el('input', '');
    demoCheck.type = 'checkbox';
    demoCheck.checked = !!s.demoMode;
    demoWrap.appendChild(demoCheck);
    demoWrap.appendChild(el('span', 'font-size:13px;', 'Demo mode — skip cooling-off waiting periods'));
    panel.appendChild(demoWrap);

    var msg = el('div', 'min-height:18px;color:#7ee2a0;font-size:12px;margin-top:8px;', '');
    panel.appendChild(msg);

    var btnRow = el('div', 'display:flex;gap:10px;margin-top:12px;');
    var saveBtn = el('button',
      'flex:1;background:#4f7cff;color:#fff;border:0;border-radius:8px;padding:11px;font-size:14px;font-weight:600;cursor:pointer;',
      'Save & close');
    var wipeBtn = el('button',
      'background:#2a3042;color:#ff9b9b;border:0;border-radius:8px;padding:11px 14px;font-size:13px;cursor:pointer;',
      'Wipe data');
    btnRow.appendChild(saveBtn);
    btnRow.appendChild(wipeBtn);
    panel.appendChild(btnRow);

    panel.appendChild(el('div', 'color:#5a6078;font-size:11px;margin-top:14px;',
      'Your key is stored only in this app’s local storage on this device and sent only to api.anthropic.com.'));

    saveBtn.addEventListener('click', function () {
      window.ADStore.saveSettings({
        apiKey: keyInput.value.trim(),
        model: modelSel.value,
        checkerModel: checkerSel.value,
        populationSize: Math.max(0, Math.min(20000, parseInt(popInput.value, 10) || 200)),
        demoMode: demoCheck.checked
      });
      overlay.style.display = 'none';
    });

    var wipeArmed = false;
    wipeBtn.addEventListener('click', function () {
      if (!wipeArmed) {
        wipeArmed = true;
        wipeBtn.textContent = 'Tap again to confirm';
        msg.style.color = '#ff9b9b';
        msg.textContent = 'This deletes bills, profile, population and audit log on this device (not your API key).';
        return;
      }
      window.ADStore.wipeData();
      wipeArmed = false;
      wipeBtn.textContent = 'Wipe data';
      msg.style.color = '#7ee2a0';
      msg.textContent = 'Local data wiped.';
    });

    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) overlay.style.display = 'none';
    });

    overlay.appendChild(panel);
    return overlay;
  }

  function init() {
    var overlay = buildPanel();
    document.body.appendChild(overlay);

    var gear = el('button',
      'position:fixed;right:14px;bottom:14px;z-index:99997;width:46px;height:46px;border-radius:50%;' +
      'background:#1a1d27;color:#aab0c8;border:1px solid #2a3042;font-size:21px;cursor:pointer;' +
      'box-shadow:0 2px 10px rgba(0,0,0,.45);', '⚙');
    gear.id = 'ad-settings-gear';
    gear.title = 'Standalone settings (API key, models)';
    gear.addEventListener('click', function () {
      // Rebuild so the panel always shows current values.
      var fresh = buildPanel();
      overlay.replaceWith(fresh);
      overlay = fresh;
      overlay.style.display = 'block';
    });
    document.body.appendChild(gear);

    // First run: no key yet → open settings so the app is usable immediately.
    if (!window.ADStore.settings().apiKey) {
      overlay.style.display = 'block';
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
