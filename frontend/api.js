/* One fetch helper for /api. Sends the stored access token, and asks for one after 401.
   Class and attribute helpers return fixed strings. Callers must not paste model text into class names. */
(function () {
  var STORAGE_KEY = "avatarDemocracyAccessToken";

  window.SIG_CLASSES = { high: "sig-high", medium: "sig-medium", low: "sig-low" };
  window.FLAG_CLASSES = { high: "flag-high", medium: "flag-medium", low: "flag-low" };
  window.STATE_CLASSES = {
    draft: "state-draft",
    endorsing: "state-endorsing",
    cooling_off: "state-cooling_off",
    voting: "state-voting",
    passed: "state-passed",
    failed: "state-failed",
    withdrawn: "state-withdrawn"
  };
  window.VOTE_CLASSES = { yes: "vote-yes", no: "vote-no", abstain: "vote-abstain" };
  window.POS_CLASSES = { yes: "pos-yes", no: "pos-no", abstain: "pos-abstain" };
  // High confidence is the calm badge; low confidence is the warning badge.
  window.CONFIDENCE_CLASSES = { high: "sig-low", medium: "sig-medium", low: "sig-high" };
  window.VOTE_VALUES = { yes: "yes", no: "no", abstain: "abstain" };

  window.allowClass = function (map, value, fallback) {
    var key = String(value == null ? "" : value).trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : fallback;
  };
  window.claimMark = function (grounded) {
    return grounded === false ? " claim-ungrounded" : "";
  };
  window.passedClass = function (passed) {
    return passed ? "sig-low" : "sig-high";
  };
  window.voteOutcomeClass = function (noContest, passed) {
    if (noContest) return "vote-abstain";
    return passed ? "vote-yes" : "vote-no";
  };
  window.currentClass = function (selected, option) {
    return selected === option ? "current" : "";
  };
  window.voteDomId = function (idx) {
    var n = Number(idx);
    if (!Number.isInteger(n) || n < 0 || n > 100000) return "vote-x";
    return "vote-" + String(n);
  };
  window.domIndex = function (idx) {
    var n = Number(idx);
    if (!Number.isInteger(n) || n < 0 || n > 100000) return "0";
    return String(n);
  };
  window.cssPercent = function (value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return "0%";
    n = Math.max(0, Math.min(100, n));
    return String(Math.round(n * 10) / 10) + "%";
  };
  window.cssColor = function (value) {
    var text = String(value || "");
    if (/^#[0-9a-fA-F]{3}$/.test(text) || /^#[0-9a-fA-F]{6}$/.test(text)) return text;
    return "#9aa0b4";
  };
  window.htmlNumber = function (value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return "0";
    return String(Math.trunc(Math.max(0, n)));
  };

  function readToken() {
    try {
      return localStorage.getItem(STORAGE_KEY) || "";
    } catch (err) {
      return "";
    }
  }

  function showTokenPrompt() {
    if (document.getElementById("access-token-prompt")) return;
    var wrap = document.createElement("div");
    wrap.id = "access-token-prompt";
    wrap.className = "access-token-prompt";
    wrap.innerHTML =
      '<form class="access-token-form">' +
      '<label for="access-token-input">Enter access token</label>' +
      '<input id="access-token-input" name="access-token" type="password" autocomplete="off" spellcheck="false">' +
      '<button type="submit" class="btn-primary">Save</button>' +
      "</form>";
    document.body.appendChild(wrap);
    wrap.querySelector("form").addEventListener("submit", function (event) {
      event.preventDefault();
      var value = document.getElementById("access-token-input").value.trim();
      if (!value) return;
      try {
        localStorage.setItem(STORAGE_KEY, value);
      } catch (err) {
        return;
      }
      window.location.reload();
    });
  }

  window.apiFetch = function (url, options) {
    var opts = options ? Object.assign({}, options) : {};
    var headers = new Headers(opts.headers || {});
    var token = readToken();
    if (token) headers.set("X-Access-Token", token);
    // Do not set Content-Type here. FormData must keep the browser's multipart boundary.
    opts.headers = headers;
    return fetch(url, opts).then(function (response) {
      if (response.status === 401) showTokenPrompt();
      return response;
    });
  };
})();
