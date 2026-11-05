/* Register the static-shell worker. Browsers only install it on https or localhost.
   A waiting worker stays waiting until the page asks it to take over. */
(function () {
  if (!("serviceWorker" in navigator)) return;

  var reloading = false;
  var hadController = !!navigator.serviceWorker.controller;

  function showUpdate(reg) {
    if (document.getElementById("sw-update")) return;
    var button = document.createElement("button");
    button.id = "sw-update";
    button.type = "button";
    button.className = "sw-update";
    button.textContent = "Update available, reload";
    button.addEventListener("click", function () {
      var worker = reg.waiting;
      if (!worker) return;
      button.disabled = true;
      worker.postMessage({ type: "SKIP_WAITING" });
    });
    document.body.appendChild(button);
  }

  function watch(reg) {
    if (reg.waiting && navigator.serviceWorker.controller) {
      showUpdate(reg);
    }
    reg.addEventListener("updatefound", function () {
      var installing = reg.installing;
      if (!installing) return;
      installing.addEventListener("statechange", function () {
        if (installing.state === "installed" && navigator.serviceWorker.controller) {
          showUpdate(reg);
        }
      });
    });
  }

  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js").then(watch).catch(function () {});
  });

  navigator.serviceWorker.addEventListener("controllerchange", function () {
    if (!hadController) {
      hadController = true;
      return;
    }
    if (reloading) return;
    reloading = true;
    window.location.reload();
  });
})();
