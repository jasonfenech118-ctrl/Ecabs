/* Fill a vehicle's make/model from the fleet register once its registration is
   typed, so details already saved on the Vehicles page aren't typed again.

   Mark the registration input with data-vehicle-reg, and point it at the boxes
   to fill:
     data-fill-make-model="#id_vehicle_make_model"   (one combined box)
     data-fill-make="..."  data-fill-model="..."     (separate boxes)

   A value the user typed themselves is never overwritten — only an empty box,
   or one still holding what we filled in last time. */
(function () {
  "use strict";

  /* Match on letters and digits only, so "ALY 309", "aly-309" and "ALY309"
     all find the same vehicle. */
  function lookup(reg) {
    var map = window.VEHICLE_MAKES || {};
    var key = (reg || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
    return map[key] || "";
  }

  function fill(el, value) {
    if (!el || !value) return;
    var last = el.dataset.autofilled || "";
    if (el.value.trim() === "" || el.value === last) {
      el.value = value;
      el.dataset.autofilled = value;
      // Let auto-save and any listeners know the value moved.
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function apply(input) {
    var makeModel = lookup(input.value);
    if (!makeModel) return;

    var combined = input.getAttribute("data-fill-make-model");
    if (combined) fill(document.querySelector(combined), makeModel);

    // Separate Make / Model boxes: first word is the make, the rest the model.
    var makeSel = input.getAttribute("data-fill-make");
    var modelSel = input.getAttribute("data-fill-model");
    if (makeSel || modelSel) {
      var bits = makeModel.trim().split(/\s+/);
      if (makeSel) fill(document.querySelector(makeSel), bits[0] || "");
      if (modelSel) fill(document.querySelector(modelSel), bits.slice(1).join(" "));
    }
  }

  function wire() {
    document.querySelectorAll("[data-vehicle-reg]").forEach(function (input) {
      if (input.dataset.vehicleWired) return;
      input.dataset.vehicleWired = "1";
      ["change", "input", "blur"].forEach(function (evt) {
        input.addEventListener(evt, function () { apply(input); });
      });
      if (input.value) apply(input);   // an existing claim being reopened
    });
  }

  document.addEventListener("DOMContentLoaded", wire);
  document.body && wire();
})();
