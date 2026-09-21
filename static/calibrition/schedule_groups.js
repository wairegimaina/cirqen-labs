/* Calibration groups — selection and the move bar.
 *
 * Progressive enhancement only: the page is a plain form that posts to
 * regroup_schedules, so it works with JavaScript disabled. This adds the
 * running count, the "select all movable" shortcut, and shows the move bar
 * once something is selected.
 */
(function () {
  "use strict";

  var form = document.getElementById("sg-regroup-form");
  if (!form) return;

  var movebar = document.getElementById("sg-movebar");
  var countEl = document.getElementById("sg-count");
  var clearBtn = document.getElementById("sg-clear");

  function checkboxes() {
    return Array.prototype.slice.call(
      form.querySelectorAll('.sg-check:not(:disabled)')
    );
  }

  function selected() {
    return checkboxes().filter(function (box) { return box.checked; });
  }

  function refresh() {
    var n = selected().length;
    if (countEl) countEl.textContent = String(n);
    if (movebar) movebar.hidden = n === 0;
  }

  form.addEventListener("change", function (event) {
    if (event.target && event.target.classList.contains("sg-check")) refresh();
  });

  // "Select all movable" within one group. Toggles, so a second click clears
  // the group rather than leaving the operator to untick each row.
  form.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-select-group]");
    if (!trigger) return;
    event.preventDefault();

    var card = trigger.closest(".sg-card");
    if (!card) return;

    var boxes = Array.prototype.slice.call(
      card.querySelectorAll('.sg-check:not(:disabled)')
    );
    var allChecked = boxes.length > 0 && boxes.every(function (b) { return b.checked; });
    boxes.forEach(function (box) { box.checked = !allChecked; });
    trigger.textContent = allChecked ? "Select all movable" : "Clear group";
    refresh();
  });

  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      checkboxes().forEach(function (box) { box.checked = false; });
      form.querySelectorAll("[data-select-group]").forEach(function (el) {
        el.textContent = "Select all movable";
      });
      refresh();
    });
  }

  // Moving schedules is not obviously reversible, so confirm the real move.
  // The Preview button is deliberately exempt: previewing changes nothing.
  form.addEventListener("submit", function (event) {
    var submitter = event.submitter;
    if (submitter && submitter.name === "preview") return;

    var n = selected().length;
    if (n === 0) {
      event.preventDefault();
      return;
    }
    var monthSelect = form.querySelector('select[name="target_month"]');
    var monthLabel = monthSelect
      ? monthSelect.options[monthSelect.selectedIndex].text
      : "the selected month";

    var ok = window.confirm(
      "Move " + n + " schedule" + (n === 1 ? "" : "s") + " to " + monthLabel + "?"
    );
    if (!ok) event.preventDefault();
  });

  refresh();
})();
