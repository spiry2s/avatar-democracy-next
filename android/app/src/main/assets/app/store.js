/*
 * Standalone-mode persistence. Mirrors the server's data/ JSON files using
 * localStorage, plus the device-local settings (API key, models, timings).
 * Loaded only when the app injects the standalone scripts — never on a server.
 */
(function () {
  'use strict';

  var LS = window.localStorage;

  function read(key, fallback) {
    try {
      var raw = LS.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) {
      return fallback;
    }
  }

  function write(key, value) {
    LS.setItem(key, JSON.stringify(value));
  }

  var DEFAULT_SETTINGS = {
    apiKey: '',
    model: 'claude-opus-4-8',          // mirrors backend/config.py MODEL
    checkerModel: 'claude-haiku-4-5',  // mirrors CHECKER_MODEL (capture defense)
    populationSize: 200,
    demoMode: true,                    // zero cooling-off timers, like the demo .bat
    multiModelCheck: true,
    delegateDriftCheck: true,
    crossBillConflictCheck: true
  };

  // Lifecycle constants (mirroring backend/config.py defaults; demoMode zeroes timers)
  var CONST = {
    thresholds: { ordinary: 25, major: 250, constitutional: 2500 },
    billCoolingOffSeconds: 7 * 24 * 3600,
    emergencyCoolingOffSeconds: 3600,
    emergencyThresholdMultiplier: 2,
    endorsementRevocationSeconds: 24 * 3600,
    delegateCoolingOffSeconds: 7 * 24 * 3600,
    sectionPassageRatio: 0.5,
    chamberVoteTau: 0.12,
    maxSinglePassChars: 200000
  };

  var Store = {
    settings: function () {
      var s = read('ad_settings', {});
      var merged = {};
      for (var k in DEFAULT_SETTINGS) merged[k] = (k in s) ? s[k] : DEFAULT_SETTINGS[k];
      return merged;
    },
    saveSettings: function (patch) {
      var s = Store.settings();
      for (var k in patch) s[k] = patch[k];
      write('ad_settings', s);
      return s;
    },

    constants: function () {
      var s = Store.settings();
      var c = {};
      for (var k in CONST) c[k] = CONST[k];
      if (s.demoMode) {
        c.billCoolingOffSeconds = 0;
        c.emergencyCoolingOffSeconds = 0;
        c.delegateCoolingOffSeconds = 0;
      }
      return c;
    },

    profile: function () { return read('ad_profile', null); },
    saveProfile: function (p) { p.updated_at = Date.now() / 1000; write('ad_profile', p); },

    bills: function () { return read('ad_bills', {}); },
    saveBill: function (bill) {
      var all = Store.bills();
      bill.updated_at = Date.now() / 1000;
      all[bill.id] = bill;
      write('ad_bills', all);
    },

    population: function () { return read('ad_population', null); },
    savePopulation: function (citizens) { write('ad_population', citizens); },

    auditLog: function () { return read('ad_audit', []); },
    logEvent: function (eventType, details) {
      var events = Store.auditLog();
      var event = { timestamp: Date.now() / 1000, event_type: eventType, details: details };
      events.push(event);
      // Cap so localStorage can't fill up over years of use.
      if (events.length > 2000) events = events.slice(events.length - 2000);
      write('ad_audit', events);
      return event;
    },

    wipeData: function () {
      LS.removeItem('ad_profile');
      LS.removeItem('ad_bills');
      LS.removeItem('ad_population');
      LS.removeItem('ad_audit');
    }
  };

  window.ADStore = Store;
})();
