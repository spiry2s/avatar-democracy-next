/* Example bill only. No network, no storage, no model call. */
(function () {
  "use strict";

  var bill = document.getElementById("example-bill");
  var list = document.getElementById("example-sections");
  var panel = document.getElementById("example-reading");
  var secondButton = document.getElementById("example-second");
  if (!bill || !list || !panel) return;

  var original = bill.textContent;
  var showSecond = false;

  function normalize(value) {
    return String(value).replace(/\s+/g, " ").trim();
  }

  function quoteFound(quote) {
    return normalize(original).indexOf(normalize(quote)) !== -1;
  }

  function selectedButton() {
    return list.querySelector("button[aria-pressed='true']");
  }

  function paintBill(quote) {
    bill.textContent = original;
    if (!quote || !quoteFound(quote)) return;
    var flat = original;
    var at = flat.indexOf(quote);
    if (at < 0) return;
    bill.textContent = "";
    bill.appendChild(document.createTextNode(flat.slice(0, at)));
    var mark = document.createElement("mark");
    mark.textContent = quote;
    bill.appendChild(mark);
    bill.appendChild(document.createTextNode(flat.slice(at + quote.length)));
  }

  function render() {
    var button = selectedButton();
    if (!button) return;
    var quote = button.getAttribute("data-quote") || "";
    var found = quoteFound(quote);
    paintBill(found ? quote : "");
    panel.replaceChildren();

    var heading = document.createElement("p");
    heading.className = "example-kicker";
    heading.textContent = "Example reading";
    panel.appendChild(heading);

    var summary = document.createElement("p");
    summary.textContent = button.getAttribute("data-summary") || "";
    panel.appendChild(summary);

    var check = document.createElement("p");
    check.className = "example-check";
    if (found) {
      check.textContent = "Example quote check: these words appear in the fictional bill, so the illustration shows them beside the reading.";
    } else {
      check.textContent = "Example quote check: these words do not appear in the fictional bill, so the illustration withholds the claim.";
    }
    panel.appendChild(check);

    var flag = button.getAttribute("data-flag");
    if (flag && found) {
      var note = document.createElement("p");
      note.textContent = flag;
      panel.appendChild(note);
    }

    if (showSecond && button.getAttribute("data-second")) {
      var other = document.createElement("p");
      other.textContent = button.getAttribute("data-second");
      panel.appendChild(other);
    }
  }

  function select(button) {
    var buttons = list.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].setAttribute("aria-pressed", buttons[i] === button ? "true" : "false");
    }
    render();
  }

  list.addEventListener("click", function (event) {
    var button = event.target.closest("button");
    if (!button || !list.contains(button)) return;
    select(button);
  });

  list.addEventListener("keydown", function (event) {
    var buttons = Array.prototype.slice.call(list.querySelectorAll("button"));
    var current = selectedButton();
    var index = buttons.indexOf(current);
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    var next = event.key === "ArrowDown" ? index + 1 : index - 1;
    if (next < 0) next = buttons.length - 1;
    if (next >= buttons.length) next = 0;
    buttons[next].focus();
    select(buttons[next]);
  });

  if (secondButton) {
    secondButton.addEventListener("click", function () {
      showSecond = !showSecond;
      secondButton.setAttribute("aria-pressed", showSecond ? "true" : "false");
      secondButton.textContent = showSecond
        ? "Hide the second example reading"
        : "Show a second example reading";
      render();
    });
  }

  var initial = selectedButton() || list.querySelector("button");
  if (initial) select(initial);
})();
