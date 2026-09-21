/**
 * Calibration Procedure Form
 * Handles dynamic formset management for parameters, sub-parameters, and set values.
 */

document.addEventListener("DOMContentLoaded", () => {

  // ─── State ────────────────────────────────────────────────────────────────
  let parameterCount = 1;
  let parametersCache = [];
  const standardsCache = {};

  // ─── API ──────────────────────────────────────────────────────────────────

  async function loadParameters() {
    try {
      const res = await fetch("/calibration/api/parameters/");
      if (!res.ok) throw new Error("Failed to fetch parameters");
      const data = await res.json();
      parametersCache = data.parameters;
      document.querySelectorAll(".parameter-name-select").forEach(populateParameterOptions);
    } catch (err) {
      console.error("Error loading parameters:", err);
      alert("Failed to load parameters. Please try again.");
    }
  }

  async function loadStandards(parameterName, selectEl) {
    if (!parameterName) return;
    if (standardsCache[parameterName]) {
      return populateStandardOptions(selectEl, standardsCache[parameterName]);
    }
    try {
      const res = await fetch(`/calibration/api/standards/?parameter_name=${encodeURIComponent(parameterName)}`);
      if (!res.ok) throw new Error("Failed to fetch standards");
      const data = await res.json();
      standardsCache[parameterName] = data.standards;
      populateStandardOptions(selectEl, data.standards);
    } catch (err) {
      console.error("Error loading standards:", err);
      selectEl.innerHTML = '<option value="">Error loading standards</option>';
    }
  }

  async function fetchStandardDetails(standardId, parameterName, paramIdx) {
    try {
      const res = await fetch(
        `/calibration/api/standards/?standard_id=${standardId}&parameter_name=${encodeURIComponent(parameterName)}`
      );
      if (!res.ok) throw new Error("Failed to fetch standard details");
      const { standards } = await res.json();
      if (!standards?.length) return resetStandardDetails(paramIdx);

      const standard = standards[0];
      let uncertainty = "0.001";

      try {
        const paramRes = await fetch(
          `/calibration/api/standard-parameters/?standard_id=${standardId}&parameter_name=${encodeURIComponent(parameterName)}`
        );
        if (paramRes.ok) {
          const paramData = await paramRes.json();
          if (paramData.uncertainty) uncertainty = paramData.uncertainty;
        }
      } catch (err) {
        console.error("Error fetching standard parameter details:", err);
      }

      const uncertaintyInput = document.getElementById(`id_parameters-${paramIdx}-reference_uncertainty`);
      if (uncertaintyInput) {
        uncertaintyInput.value = uncertainty;
        uncertaintyInput.classList.add("auto-filled");
      }

      const infoDiv = document.getElementById(`standard-info-${paramIdx}`);
      if (infoDiv) {
        infoDiv.innerHTML = `
          <small class="text-muted">
            Model: ${escapeHTML(standard.model_number || "N/A")} |
            Manufacturer: ${escapeHTML(standard.manufacturer || "N/A")} |
            Cal Due: ${escapeHTML(standard.calibration_due_date || "N/A")} |
            Uncertainty: ${escapeHTML(uncertainty)}
          </small>`;
      }
    } catch (err) {
      console.error("Error getting standard details:", err);
      resetStandardDetails(paramIdx);
    }
  }

  // ─── DOM Helpers ──────────────────────────────────────────────────────────

  function populateParameterOptions(selectEl) {
    selectEl.innerHTML = '<option value="">Select a parameter...</option>';
    parametersCache.forEach(({ name, unit }) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = `${name} (${unit})`;
      selectEl.appendChild(opt);
    });
  }

  function populateStandardOptions(selectEl, standards) {
    selectEl.innerHTML = '<option value="">Select a standard...</option>';
    standards.forEach(({ id, serial_number, name }) => {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = `${serial_number} - ${name}`;
      selectEl.appendChild(opt);
    });
  }

  function resetStandardDetails(paramIdx) {
    const input = document.getElementById(`id_parameters-${paramIdx}-reference_uncertainty`);
    if (input) { input.value = "0.001"; input.classList.remove("auto-filled"); }
    const infoDiv = document.getElementById(`standard-info-${paramIdx}`);
    if (infoDiv) infoDiv.innerHTML = "";
  }

  function setTotalForms(formsetName, count) {
    const el = document.getElementById(`id_${formsetName}-TOTAL_FORMS`);
    if (el) el.value = count;
  }

  // Re-index input[name]/id and label[for] inside a container
  function reindexFields(container, namePattern, nameReplacement, labelPattern, labelReplacement) {
    container.querySelectorAll("input, select, textarea").forEach((el) => {
      if (el.name?.match(namePattern)) {
        el.name = el.name.replace(namePattern, nameReplacement);
        if (el.id) el.id = `id_${el.name}`;
      }
    });
    container.querySelectorAll("label[for]").forEach((label) => {
      const val = label.getAttribute("for");
      if (val?.match(labelPattern)) label.setAttribute("for", val.replace(labelPattern, labelReplacement));
    });
  }

  // ─── Index Updaters ───────────────────────────────────────────────────────

  function updateParameterIndices() {
    const forms = document.querySelectorAll(".parameter-form");
    forms.forEach((form, idx) => {
      form.dataset.formsetIndex = idx;

      reindexFields(form, /parameters-\d+/g, `parameters-${idx}`, /id_parameters-\d+/g, `id_parameters-${idx}`);

      const standardInfo = form.querySelector(".standard-info");
      if (standardInfo) standardInfo.id = `standard-info-${idx}`;

      const checkbox = form.querySelector('input[type="checkbox"]');
      if (checkbox) {
        checkbox.name = `parameters-${idx}-has_sub_parameters`;
        checkbox.id   = `id_parameters-${idx}-has_sub_parameters`;
      }

      const containerIds = {
        ".sub-formset-container": `sub-parameter-container-${idx}`,
        ".sub-parameter-forms":   `sub-parameter-forms-${idx}`,
        ".set-value-forms":       `set-value-forms-${idx}`,
      };
      Object.entries(containerIds).forEach(([sel, id]) => {
        const el = form.querySelector(sel);
        if (el) el.id = id;
      });

      const addSubBtn = form.querySelector(".add-sub-parameter");
      const addValBtn = form.querySelector(".add-set-value");
      if (addSubBtn) addSubBtn.dataset.parentIndex = idx;
      if (addValBtn) addValBtn.dataset.parentIndex = idx;
    });
    setTotalForms("parameters", forms.length);
  }

  function updateSubParameterIndices(paramIdx) {
    const forms = document.querySelectorAll(`#sub-parameter-forms-${paramIdx} .sub-parameter-form`);
    forms.forEach((form, idx) => {
      form.dataset.subParamIndex = idx;
      reindexFields(form, /sub_parameters-\d+/g, `sub_parameters-${idx}`, /sub_parameters-\d+/g, `sub_parameters-${idx}`);
    });
    setTotalForms(`parameters-${paramIdx}-sub_parameters`, forms.length);
  }

  function updateSetValueIndices(paramIdx) {
    const forms = document.querySelectorAll(`#set-value-forms-${paramIdx} .set-value-form`);
    forms.forEach((form, idx) => {
      form.dataset.setValueIndex = idx;
      reindexFields(form, /set_values-\d+/g, `set_values-${idx}`, /set_values-\d+/g, `set_values-${idx}`);
    });
    setTotalForms(`parameters-${paramIdx}-set_values`, forms.length);
  }

  function updateAllIndices() {
    updateParameterIndices();
    document.querySelectorAll(".parameter-form").forEach((form) => {
      const idx = form.dataset.formsetIndex;
      updateSubParameterIndices(idx);
      updateSetValueIndices(idx);
    });
  }

  // ─── Sub-Parameter Options ────────────────────────────────────────────────

  function updateSubParameterOptions(paramIdx) {
    const subParamNames = Array.from(
      document.querySelectorAll(`#sub-parameter-forms-${paramIdx} input[name$="-name"]`)
    ).map((input, idx) => ({ name: input.value.trim(), idx })).filter((item) => item.name);

    document.querySelectorAll(`#set-value-forms-${paramIdx} select[name$="-sub_parameter"]`).forEach((select) => {
      const prev = select.value;
      select.innerHTML = '<option value="">Select sub-parameter...</option>';
      subParamNames.forEach(({ name, idx }) => {
        const opt = document.createElement("option");
        opt.value = idx;
        opt.textContent = name;
        select.appendChild(opt);
      });
      if (prev && select.querySelector(`option[value="${prev}"]`)) select.value = prev;
    });
  }

  // ─── Toggle Sub-Parameters ────────────────────────────────────────────────

  function toggleSubParameters(paramIdx) {
    const checkbox  = document.getElementById(`id_parameters-${paramIdx}-has_sub_parameters`);
    const container = document.getElementById(`sub-parameter-container-${paramIdx}`);
    const selects   = document.querySelectorAll(`#set-value-forms-${paramIdx} .sub-parameter-select`);
    if (!checkbox || !container) return;

    const show = checkbox.checked;
    container.classList.toggle("hidden", !show);
    selects.forEach((wrap) => {
      wrap.classList.toggle("hidden", !show);
      const sel = wrap.querySelector("select");
      if (sel) sel.value = "";
    });
    container.querySelectorAll("[required]").forEach((el) => { el.required = show; });
    updateSubParameterOptions(paramIdx);
  }

  // ─── HTML Templates ───────────────────────────────────────────────────────

  function setValueRowHTML(paramIdx, valIdx, showSubParam = false) {
    const p = `parameters-${paramIdx}-set_values-${valIdx}`;
    return `
      <div class="set-value-form formset-container mb-3" data-set-value-index="${valIdx}">
        <input type="hidden" name="${p}-id" value="">
        <input type="hidden" name="${p}-DELETE" id="id_${p}-DELETE">
        <div class="row">
          <div class="col-md form-group">
            <label for="id_${p}-value" class="form-label">Set Value</label>
            <input type="number" name="${p}-value" id="id_${p}-value"
              class="form-control" step="0.000001" placeholder="Set value" required>
          </div>
          <div class="col-md-3 form-group">
            <label for="id_${p}-order" class="form-label">Order</label>
            <input type="number" name="${p}-order" id="id_${p}-order"
              class="form-control" min="0" value="${valIdx}">
          </div>
          <div class="col-md-3 form-group sub-parameter-select ${showSubParam ? "" : "hidden"}">
            <label for="id_${p}-sub_parameter" class="form-label">Sub-Parameter</label>
            <select name="${p}-sub_parameter" id="id_${p}-sub_parameter" class="form-control">
              <option value="">Select sub-parameter...</option>
            </select>
          </div>
          <div class="col-md-auto form-group d-flex align-items-end">
            <button type="button"
              class="btn btn-outline-danger btn-sm delete-set-value delete-formset">
              <i class="fas fa-trash-alt"></i> Delete
            </button>
          </div>
        </div>
      </div>`;
  }

  function subParameterRowHTML(paramIdx, subIdx) {
    const p = `parameters-${paramIdx}-sub_parameters-${subIdx}`;
    return `
      <div class="sub-parameter-form formset-container mb-3" data-sub-param-index="${subIdx}">
        <input type="hidden" name="${p}-id" value="">
        <input type="hidden" name="${p}-DELETE" id="id_${p}-DELETE">
        <div class="row">
          <div class="col-md-3 form-group">
            <label for="id_${p}-name" class="form-label">Sub-Parameter Name</label>
            <input type="text" name="${p}-name" id="id_${p}-name"
              class="form-control sub-parameter-name-input"
              placeholder="Enter sub-parameter name" required>
          </div>
          <div class="col-md-2 form-group">
            <label for="id_${p}-tolerance" class="form-label">Tolerance (±)</label>
            <input type="number" name="${p}-tolerance" id="id_${p}-tolerance"
              class="form-control" step="0.000001" value="1.0" min="0">
          </div>
          <div class="col-md-2 form-group">
            <label for="id_${p}-order" class="form-label">Order</label>
            <input type="number" name="${p}-order" id="id_${p}-order"
              class="form-control" min="0" value="${subIdx}">
          </div>
          <div class="col-md-2 form-group d-flex align-items-end">
            <button type="button"
              class="btn btn-outline-danger btn-sm delete-sub-parameter delete-formset">
              <i class="fas fa-trash-alt"></i> Delete
            </button>
          </div>
        </div>
      </div>`;
  }

  function parameterFormHTML(idx) {
    const p = `parameters-${idx}`;
    return `
      <input type="hidden" name="${p}-id" value="">
      <input type="hidden" name="${p}-DELETE" id="id_${p}-DELETE">

      <div class="row">
        <div class="col-md-3 form-group">
          <label for="id_${p}-name" class="form-label">Parameter Name</label>
          <select name="${p}-name" id="id_${p}-name"
            class="form-control parameter-name-select" required>
            <option value="">Select a parameter...</option>
          </select>
        </div>
        <div class="col-md-2 form-group">
          <label for="id_${p}-unit" class="form-label">Unit</label>
          <input type="text" name="${p}-unit" id="id_${p}-unit" class="form-control" readonly>
        </div>
        <div class="col-md-2 form-group">
          <label for="id_${p}-num_readings" class="form-label">Number of Readings</label>
          <input type="number" name="${p}-num_readings" id="id_${p}-num_readings"
            class="form-control" min="3" max="20" value="5" required>
        </div>
        <div class="col-md-3 form-group">
          <label for="id_${p}-standard_reference" class="form-label">Standard Reference</label>
          <select name="${p}-standard_reference" id="id_${p}-standard_reference"
            class="form-control standard-select">
            <option value="">Select a standard...</option>
          </select>
          <div class="standard-info" id="standard-info-${idx}"></div>
        </div>
        <div class="col-md-2 form-group d-flex align-items-end justify-content-end">
          <button type="button"
            class="btn btn-outline-danger btn-sm delete-parameter delete-formset">
            <i class="fas fa-trash-alt"></i> Delete parameter
          </button>
        </div>
      </div>

      <div class="row">
        <div class="col-md-3 form-group">
          <label for="id_${p}-reference_uncertainty" class="form-label">
            Reference Uncertainty (k=2)
          </label>
          <input type="number" name="${p}-reference_uncertainty"
            id="id_${p}-reference_uncertainty"
            class="form-control" step="0.000001" value="0.001" required readonly>
        </div>
        <div class="col-md-2 form-group">
          <label for="id_${p}-coverage_factor" class="form-label">Coverage Factor</label>
          <input type="number" name="${p}-coverage_factor" id="id_${p}-coverage_factor"
            class="form-control" step="0.1" value="2.0" min="1" required>
        </div>
        <div class="col-md-2 form-group">
          <label for="id_${p}-tolerance" class="form-label">Tolerance (±)</label>
          <input type="number" name="${p}-tolerance" id="id_${p}-tolerance"
            class="form-control" step="0.000001" value="1.0" min="0">
        </div>
        <div class="col-md-2 form-group">
          <label for="id_${p}-order" class="form-label">Order</label>
          <input type="number" name="${p}-order" id="id_${p}-order"
            class="form-control" min="0" value="${idx}">
        </div>
        <div class="col-md-3 form-group d-flex align-items-end">
          <div class="form-check mb-2">
            <input type="checkbox" class="form-check-input"
              id="id_${p}-has_sub_parameters" name="${p}-has_sub_parameters">
            <label class="form-check-label" for="id_${p}-has_sub_parameters">
              Has Sub-Parameters
            </label>
          </div>
        </div>
      </div>

      <div class="sub-formset-container hidden" id="sub-parameter-container-${idx}">
        <h4 class="mb-3">Sub-Parameters</h4>
        <input type="hidden" name="${p}-sub_parameters-TOTAL_FORMS"
          value="0" id="id_${p}-sub_parameters-TOTAL_FORMS">
        <input type="hidden" name="${p}-sub_parameters-INITIAL_FORMS" value="0">
        <input type="hidden" name="${p}-sub_parameters-MIN_NUM_FORMS" value="0">
        <input type="hidden" name="${p}-sub_parameters-MAX_NUM_FORMS" value="1000">
        <div class="sub-parameter-forms" id="sub-parameter-forms-${idx}"></div>
        <button type="button"
          class="btn btn-secondary btn-sm add-sub-parameter add-formset"
          data-parent-index="${idx}">
          <i class="fas fa-plus"></i> Add sub-parameter
        </button>
      </div>

      <div class="set-value-formset sub-formset-container">
        <h4 class="mb-3">Set Values</h4>
        <input type="hidden" name="${p}-set_values-TOTAL_FORMS"
          value="1" id="id_${p}-set_values-TOTAL_FORMS">
        <input type="hidden" name="${p}-set_values-INITIAL_FORMS" value="0">
        <input type="hidden" name="${p}-set_values-MIN_NUM_FORMS" value="1">
        <input type="hidden" name="${p}-set_values-MAX_NUM_FORMS" value="1000">
        <div class="set-value-forms" id="set-value-forms-${idx}">
          ${setValueRowHTML(idx, 0)}
        </div>
        <button type="button"
          class="btn btn-secondary btn-sm add-set-value add-formset"
          data-parent-index="${idx}">
          <i class="fas fa-plus"></i> Add set value
        </button>
      </div>`;
  }

  // ─── Add / Delete Handlers ────────────────────────────────────────────────

  function addParameter() {
    const container = document.getElementById("parameter-forms");
    const form = document.createElement("div");
    form.className = "parameter-form";
    form.dataset.formsetIndex = parameterCount;
    form.innerHTML = parameterFormHTML(parameterCount);
    container.appendChild(form);
    populateParameterOptions(form.querySelector(".parameter-name-select"));
    updateAllIndices();
    parameterCount++;
  }

  function addSetValue(btn) {
    const paramIdx = btn.dataset.parentIndex;
    const container = document.getElementById(`set-value-forms-${paramIdx}`);
    if (!container) return console.error(`Set value container not found for param ${paramIdx}`);
    const count = container.querySelectorAll(".set-value-form").length;
    const hasSubParams = document.getElementById(`id_parameters-${paramIdx}-has_sub_parameters`)?.checked || false;
    container.insertAdjacentHTML("beforeend", setValueRowHTML(paramIdx, count, hasSubParams));
    updateSetValueIndices(paramIdx);
    updateSubParameterOptions(paramIdx);
  }

  function addSubParameter(btn) {
    const paramIdx = btn.dataset.parentIndex;
    const container = document.getElementById(`sub-parameter-forms-${paramIdx}`);
    if (!container) return console.error(`Sub-parameter container not found for param ${paramIdx}`);
    const count = container.querySelectorAll(".sub-parameter-form").length;
    container.insertAdjacentHTML("beforeend", subParameterRowHTML(paramIdx, count));
    updateSubParameterIndices(paramIdx);
    updateSubParameterOptions(paramIdx);
  }

  function deleteParameter(btn) {
    const form = btn.closest(".parameter-form");
    if (document.querySelectorAll(".parameter-form:not(.hidden)").length <= 1)
      return alert("At least one parameter is required.");
    const del = form.querySelector('input[name$="-DELETE"]');
    if (del) del.value = "on";
    form.classList.add("hidden");
    updateAllIndices();
  }

  function deleteSubParameter(btn) {
    const form = btn.closest(".sub-parameter-form");
    const paramIdx = form.closest(".parameter-form").dataset.formsetIndex;
    const del = form.querySelector('input[name$="-DELETE"]');
    if (del) del.value = "on";
    form.classList.add("hidden");
    updateSubParameterIndices(paramIdx);
    updateSubParameterOptions(paramIdx);
  }

  function deleteSetValue(btn) {
    const form = btn.closest(".set-value-form");
    const paramIdx = form.closest(".parameter-form").dataset.formsetIndex;
    const container = document.getElementById(`set-value-forms-${paramIdx}`);
    if (!container) return;
    if (container.querySelectorAll(".set-value-form:not(.hidden)").length <= 1)
      return alert("At least one set value is required.");
    const del = form.querySelector('input[name$="-DELETE"]');
    if (del) del.value = "on";
    form.classList.add("hidden");
    updateSetValueIndices(paramIdx);
  }

  // ─── Change Handlers ──────────────────────────────────────────────────────

  function handleParameterChange(select) {
    const form = select.closest(".parameter-form");
    if (!form) return;
    const paramIdx  = form.dataset.formsetIndex;
    const paramName = select.value;
    const unitInput = document.getElementById(`id_parameters-${paramIdx}-unit`);
    const stdSelect = document.getElementById(`id_parameters-${paramIdx}-standard_reference`);
    const param     = parametersCache.find((p) => p.name === paramName);

    if (param) {
      if (unitInput) { unitInput.value = param.unit; unitInput.classList.add("auto-filled"); }
      if (stdSelect) loadStandards(paramName, stdSelect);
    } else {
      if (unitInput) { unitInput.value = ""; unitInput.classList.remove("auto-filled"); }
      if (stdSelect) stdSelect.innerHTML = '<option value="">Select a standard...</option>';
    }
    resetStandardDetails(paramIdx);
  }

  function handleStandardChange(select) {
    const form = select.closest(".parameter-form");
    if (!form) return;
    const paramIdx  = form.dataset.formsetIndex;
    const paramName = document.getElementById(`id_parameters-${paramIdx}-name`)?.value || "";
    if (select.value && paramName) fetchStandardDetails(select.value, paramName, paramIdx);
    else resetStandardDetails(paramIdx);
  }

  function handleSubParameterNameChange(input) {
    const paramForm = input.closest(".sub-parameter-form")?.closest(".parameter-form");
    if (paramForm) updateSubParameterOptions(paramForm.dataset.formsetIndex);
  }

  // ─── Form Clear ───────────────────────────────────────────────────────────

  function clearForm() {
    if (!confirm("Are you sure you want to clear all form data? This action cannot be undone.")) return;

    const container = document.getElementById("parameter-forms");
    [...container.querySelectorAll(".parameter-form")].slice(1).forEach((f) => f.remove());

    const firstForm = container.querySelector(".parameter-form");
    if (firstForm) {
      firstForm.querySelectorAll('input:not([type="hidden"]), textarea').forEach((el) => {
        el.value = el.defaultValue || "";
        el.classList.remove("auto-filled");
      });
      firstForm.querySelectorAll("select").forEach((sel) => {
        if (sel.classList.contains("parameter-name-select")) populateParameterOptions(sel);
        else sel.innerHTML = '<option value="">Select...</option>';
        sel.selectedIndex = 0;
      });
      const checkbox = firstForm.querySelector('input[type="checkbox"]');
      if (checkbox) checkbox.checked = false;
      firstForm.querySelector(".sub-formset-container")?.classList.add("hidden");
      const subForms = firstForm.querySelector(".sub-parameter-forms");
      if (subForms) subForms.innerHTML = "";
      const setValueForms = firstForm.querySelector(".set-value-forms");
      if (setValueForms) setValueForms.innerHTML = setValueRowHTML(0, 0);
      const stdInfo = firstForm.querySelector(".standard-info");
      if (stdInfo) stdInfo.innerHTML = "";
    }

    document.querySelectorAll('#procedureForm input[type="text"], #procedureForm textarea').forEach((el) => {
      if (!el.closest(".parameter-form") && !el.name.includes("csrf")) el.value = "";
    });

    const defaults = { id_temperature: "23.0", id_humidity: "50.0", id_pressure: "101.325" };
    Object.entries(defaults).forEach(([id, val]) => {
      const el = document.getElementById(id);
      if (el) el.value = val;
    });

    parameterCount = 1;
    updateAllIndices();
  }

  // ─── Validation ───────────────────────────────────────────────────────────

  function validateForm(e) {
    const visibleParams = document.querySelectorAll(".parameter-form:not(.hidden)");
    if (!visibleParams.length) {
      e.preventDefault();
      return alert("At least one parameter is required.");
    }

    let invalid = false;
    visibleParams.forEach((form) => {
      const paramIdx = form.dataset.formsetIndex;
      if (!document.querySelectorAll(`#set-value-forms-${paramIdx} .set-value-form:not(.hidden)`).length) {
        invalid = true;
      }
      if (document.getElementById(`id_parameters-${paramIdx}-has_sub_parameters`)?.checked) {
        document.querySelectorAll(`#sub-parameter-forms-${paramIdx} .sub-parameter-form:not(.hidden)`).forEach((sub) => {
          const nameInput = sub.querySelector('input[name$="-name"]');
          if (nameInput && !nameInput.value.trim()) { invalid = true; nameInput.focus(); }
        });
      }
    });

    if (invalid) {
      e.preventDefault();
      alert("Please fill in all required fields.");
      return false;
    }
  }

  // ─── Loading / Success Overlay ────────────────────────────────────────────

  function attachOverlayBehavior() {
    const form           = document.getElementById("procedureForm");
    const loadingOverlay = document.getElementById("loadingOverlay");
    const successOverlay = document.getElementById("successMessage");
    const loadingContent = document.querySelector(".loading-content");
    const successContent = document.querySelector(".success-message");
    const createAnother  = document.getElementById("createAnother");

    if (!form || !loadingOverlay || !successOverlay) return;

    form.addEventListener("submit", (e) => {
      // Run validation first — only show overlay if valid
      const visibleParams = document.querySelectorAll(".parameter-form:not(.hidden)");
      if (!visibleParams.length) return; // validateForm will handle alert

      e.preventDefault();

      loadingOverlay.style.display = "flex";
      loadingContent.style.display = "block";
      successOverlay.style.display = "none";
      successContent.style.display = "none";

      setTimeout(() => {
        loadingContent.style.display = "none";
        loadingOverlay.style.display = "none";
        successOverlay.style.display = "flex";
        successContent.style.display = "block";

        setTimeout(() => form.submit(), 2000);
      }, 3000);
    });

    createAnother?.addEventListener("click", () => {
      loadingOverlay.style.display = "none";
      successOverlay.style.display = "none";
      successContent.style.display = "none";
      form.reset();
      document.getElementById("id_parameters-TOTAL_FORMS").value = "1";
    });
  }

  // ─── Event Delegation ─────────────────────────────────────────────────────

  function attachEventListeners() {
    document.addEventListener("change", (e) => {
      if (e.target.classList.contains("parameter-name-select")) return handleParameterChange(e.target);
      if (e.target.classList.contains("standard-select"))       return handleStandardChange(e.target);
      if (e.target.matches('input[name$="has_sub_parameters"]')) {
        const form = e.target.closest(".parameter-form");
        if (form) toggleSubParameters(form.dataset.formsetIndex);
      }
    });

    document.addEventListener("input", (e) => {
      if (e.target.classList.contains("sub-parameter-name-input")) handleSubParameterNameChange(e.target);
    });

    document.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const handlers = {
        "add-parameter":        () => addParameter(),
        "add-set-value":        () => addSetValue(btn),
        "add-sub-parameter":    () => addSubParameter(btn),
        "delete-parameter":     () => deleteParameter(btn),
        "delete-sub-parameter": () => deleteSubParameter(btn),
        "delete-set-value":     () => deleteSetValue(btn),
      };
      for (const [cls, fn] of Object.entries(handlers)) {
        if (btn.classList.contains(cls)) { e.preventDefault(); fn(); break; }
      }
    });

    document.getElementById("clearForm")?.addEventListener("click", (e) => {
      e.preventDefault();
      clearForm();
    });

    document.getElementById("procedureForm")?.addEventListener("submit", validateForm);
  }

  // ─── Init ─────────────────────────────────────────────────────────────────

  loadParameters();
  attachEventListeners();
  attachOverlayBehavior();
});
