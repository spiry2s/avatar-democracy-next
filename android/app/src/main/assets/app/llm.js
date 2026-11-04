/*
 * Direct Anthropic Messages API client for standalone mode.
 *
 * Calls api.anthropic.com straight from the WebView using the citizen's OWN
 * key (entered in settings, stored only on this device). Anthropic supports
 * browser-origin calls when the anthropic-dangerous-direct-browser-access
 * header is sent — "dangerous" refers to embedding a key in published web
 * apps, not to this paste-your-own-key pattern.
 */
(function () {
  'use strict';

  var API_URL = 'https://api.anthropic.com/v1/messages';

  // The shim replaces window.fetch; keep a handle on the real one.
  var nativeFetch = window.fetch.bind(window);

  function apiKey() {
    return (window.ADStore.settings().apiKey || '').trim();
  }

  function requireKey() {
    if (!apiKey()) {
      throw new Error('No Anthropic API key configured. Tap the gear button and paste your key (get one at console.anthropic.com).');
    }
  }

  /**
   * One Messages API call. opts: {model, system, prompt, maxTokens, thinking}.
   * Returns the joined text of all text blocks (thinking blocks skipped),
   * matching the Python `"".join(b.text for b in content if hasattr(b,"text"))`.
   */
  async function call(opts) {
    requireKey();
    var body = {
      model: opts.model,
      max_tokens: opts.maxTokens || 16000,
      messages: [{ role: 'user', content: opts.prompt }]
    };
    if (opts.system) body.system = opts.system;
    if (opts.thinking) body.thinking = { type: 'adaptive' };

    var res = await nativeFetch(API_URL, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': apiKey(),
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true'
      },
      body: JSON.stringify(body)
    });

    if (!res.ok) {
      var detail = 'Claude API error (HTTP ' + res.status + ')';
      try {
        var err = await res.json();
        if (err && err.error && err.error.message) detail = err.error.message;
      } catch (e) { /* keep generic detail */ }
      if (res.status === 401) detail = 'Invalid API key. Check the key in settings (gear button).';
      throw new Error(detail);
    }

    var msg = await res.json();
    return (msg.content || [])
      .filter(function (b) { return b.type === 'text' && typeof b.text === 'string'; })
      .map(function (b) { return b.text; })
      .join('')
      .trim();
  }

  /** Strip markdown code fences, mirroring the Python helpers. */
  function stripFences(text) {
    text = (text || '').trim();
    if (text.indexOf('```') === 0) {
      var lines = text.split('\n');
      var end = lines[lines.length - 1].trim() === '```' ? lines.length - 1 : lines.length;
      text = lines.slice(1, end).join('\n');
    }
    return text;
  }

  function parseJson(text, what) {
    try {
      return JSON.parse(stripFences(text));
    } catch (e) {
      throw new Error('Could not parse ' + (what || 'response') + ' as JSON: ' + e.message + '\n\n' + String(text).slice(0, 400));
    }
  }

  window.ADLlm = { call: call, stripFences: stripFences, parseJson: parseJson };
})();
