document.addEventListener("DOMContentLoaded", function () {
  // Global variables
  let parameterCount = 1;
  let parametersCache = [];
  let standardsCache = {};

  // Initialize the form
  function initializeForm() {
    loadParameters();
    attachEventListeners();
  }

  // Load parameters from API
  async function loadParameters() {
    try {
      const response = await fetch("/calibration/api/parameters/");
      if (!response.ok) throw new Error("Failed to fetch parameters");
      const data = await response.json();
      parametersCache = data.parameters;
      populateAllParameterDropdowns();
    } catch (error) {
      console.error("Error loading parameters:", error);
      alert("Failed to load parameters. Please try again.");
    }
  }

  // Populate all parameter dropdowns
  function populateAllParameterDropdowns() {
    document.querySelectorAll(".parameter-name-select").forEach((select) => {
      populateParameterOptions(select);
    });
  }

  // Populate a single parameter dropdown
  function populateParameterOptions(selectElement) {
    selectElement.innerHTML = '<option value="">Select a parameter...</option>';
    parametersCache.forEach((param) => {
      const option = document.createElement("option");
      option.value = param.name;
      option.textContent = `${param.name} (${param.unit})`;
      selectElement.appendChild(option);
    });
  }

  // Load standards for a parameter
  async function loadStandards(parameterName, selectElement) {
    if (!parameterName) return;

    if (standardsCache[parameterName]) {
      populateStandardOptions(selectElement, standardsCache[parameterName]);
      return;
    }

    try {
      const response = await fetch(
        `/calibration/api/standards/?parameter_name=${encodeURIComponent(
          parameterName
        )}`
      );
      if (!response.ok) throw new Error("Failed to fetch standards");
      const data = await response.json();
      standardsCache[parameterName] = data.standards;
      populateStandardOptions(selectElement, data.standards);
    } catch (error) {
      console.error("Error loading standards:", error);
      selectElement.innerHTML =
        '<option value="">Error loading standards</option>';
    }
  }

  // Populate standard dropdown
  function populateStandardOptions(selectElement, standards) {
    selectElement.innerHTML = '<option value="">Select a standard...</option>';
    standards.forEach((standard) => {
      const option = document.createElement("option");
      option.value = standard.id;
      option.textContent = `${standard.serial_number} - ${standard.name}`;
      selectElement.appendChild(option);
    });
  }

  // Get standard details including uncertainty
  async function getStandardDetails(standardId, parameterName, parameterIndex) {
    try {
      const response = await fetch(
        `/calibration/api/standards/?standard_id=${standardId}&parameter_name=${encodeURIComponent(
          parameterName
        )}`
      );
      if (!response.ok) throw new Error("Failed to fetch standard details");
      const data = await response.json();

      if (data.standards && data.standards.length > 0) {
        const standard = data.standards[0];
        let uncertainty = "0.001"; // Default value

        // Try to get parameter-specific uncertainty
        try {
          const paramResponse = await fetch(
            `/calibration/api/standard-parameters/?standard_id=${standardId}&parameter_name=${encodeURIComponent(
              parameterName
            )}`
          );
          if (paramResponse.ok) {
            const paramData = await paramResponse.json();
            if (paramData.uncertainty) {
              uncertainty = paramData.uncertainty;
            }
          }
        } catch (error) {
          console.error("Error fetching standard parameter details:", error);
        }

        // Update UI
        const referenceUncertaintyInput = document.getElementById(
          `id_parameters-${parameterIndex}-reference_uncertainty`
        );
        if (referenceUncertaintyInput) {
          referenceUncertaintyInput.value = uncertainty;
          referenceUncertaintyInput.classList.add("auto-filled");
        }

        const standardInfoDiv = document.getElementById(
          `standard-info-${parameterIndex}`
        );
        if (standardInfoDiv) {
          standardInfoDiv.innerHTML = `
                        <small class="text-muted">
                            Model: ${standard.model_number || "N/A"} | 
                            Manufacturer: ${standard.manufacturer || "N/A"} | 
                            Cal Due: ${
                              standard.calibration_due_date || "N/A"
                            } | 
                            Uncertainty: ${uncertainty}
                        </small>
                    `;
        }
      }
    } catch (error) {
      console.error("Error getting standard details:", error);
      resetStandardDetails(parameterIndex);
    }
  }

  // Reset standard details
  function resetStandardDetails(parameterIndex) {
    const referenceUncertaintyInput = document.getElementById(
      `id_parameters-${parameterIndex}-reference_uncertainty`
    );
    if (referenceUncertaintyInput) {
      referenceUncertaintyInput.value = "0.001";
      referenceUncertaintyInput.classList.remove("auto-filled");
    }
    const standardInfoDiv = document.getElementById(
      `standard-info-${parameterIndex}`
    );
    if (standardInfoDiv) standardInfoDiv.innerHTML = "";
  }

  // Update formset indices
  function updateFormsetIndices() {
    updateParameterIndices();
    document.querySelectorAll(".parameter-form").forEach((form) => {
      const parameterIndex = form.dataset.formsetIndex;
      updateSubParameterIndices(parameterIndex);
      updateSetValueIndices(parameterIndex);
    });
  }

  // Update parameter indices
  function updateParameterIndices() {
    const parameterForms = document.querySelectorAll(".parameter-form");
    parameterForms.forEach((form, index) => {
      form.dataset.formsetIndex = index;

      // Update input names and IDs
      form.querySelectorAll("input, select, textarea").forEach((input) => {
        if (input.name && input.name.startsWith("parameters-")) {
          const newName = input.name.replace(
            /parameters-\d+/,
            `parameters-${index}`
          );
          input.name = newName;
          input.id = input.id ? `id_${newName}` : "";
        }
      });

      // Update labels for attributes
      form.querySelectorAll("label[for]").forEach((label) => {
        if (label.getAttribute("for").startsWith("id_parameters-")) {
          const newFor = label
            .getAttribute("for")
            .replace(/id_parameters-\d+/, `id_parameters-${index}`);
          label.setAttribute("for", newFor);
        }
      });

      // Update standard info div ID
      const standardInfoDiv = form.querySelector(".standard-info");
      if (standardInfoDiv) {
        standardInfoDiv.id = `standard-info-${index}`;
      }

      // Update checkbox
      const checkbox = form.querySelector('input[type="checkbox"]');
      if (checkbox) {
        checkbox.name = `parameters-${index}-has_sub_parameters`;
        checkbox.id = `id_parameters-${index}-has_sub_parameters`;
        const checkboxLabel = form.querySelector(`label[for="${checkbox.id}"]`);
        if (checkboxLabel) {
          checkboxLabel.setAttribute("for", checkbox.id);
        }
      }

      // Update sub-parameter container ID
      const subContainer = form.querySelector(".sub-formset-container");
      if (subContainer) {
        subContainer.id = `sub-parameter-container-${index}`;
      }

      // Update sub-parameter forms container ID
      const subParamForms = form.querySelector(".sub-parameter-forms");
      if (subParamForms) {
        subParamForms.id = `sub-parameter-forms-${index}`;
      }

      // Update set-value forms container ID
      const setValueForms = form.querySelector(".set-value-forms");
      if (setValueForms) {
        setValueForms.id = `set-value-forms-${index}`;
      }

      // Update button data attributes
      const addSubParamBtn = form.querySelector(".add-sub-parameter");
      const addSetValueBtn = form.querySelector(".add-set-value");
      if (addSubParamBtn) addSubParamBtn.dataset.parentIndex = index;
      if (addSetValueBtn) addSetValueBtn.dataset.parentIndex = index;
    });

    // Update total forms count
    updateTotalForms("parameters", parameterForms.length);
  }

  // Update sub-parameter indices
  function updateSubParameterIndices(parameterIndex) {
    const subParamForms = document.querySelectorAll(
      `#sub-parameter-forms-${parameterIndex} .sub-parameter-form`
    );
    subParamForms.forEach((form, index) => {
      form.dataset.subParamIndex = index;

      form.querySelectorAll("input, select").forEach((input) => {
        if (input.name && input.name.includes("sub_parameters")) {
          const newName = input.name.replace(
            /sub_parameters-\d+/,
            `sub_parameters-${index}`
          );
          input.name = newName;
          input.id = input.id ? `id_${newName}` : "";
        }
      });

      // Update labels for attributes
      form.querySelectorAll("label[for]").forEach((label) => {
        if (label.getAttribute("for").includes("sub_parameters")) {
          const newFor = label
            .getAttribute("for")
            .replace(/sub_parameters-\d+/, `sub_parameters-${index}`);
          label.setAttribute("for", newFor);
        }
      });
    });

    updateTotalForms(
      `parameters-${parameterIndex}-sub_parameters`,
      subParamForms.length
    );
  }

  // Update set value indices
  function updateSetValueIndices(parameterIndex) {
    const setValueForms = document.querySelectorAll(
      `#set-value-forms-${parameterIndex} .set-value-form`
    );
    setValueForms.forEach((form, index) => {
      form.dataset.setValueIndex = index;

      form.querySelectorAll("input, select").forEach((input) => {
        if (input.name && input.name.includes("set_values")) {
          const newName = input.name.replace(
            /set_values-\d+/,
            `set_values-${index}`
          );
          input.name = newName;
          input.id = input.id ? `id_${newName}` : "";
        }
      });

      // Update labels for attributes
      form.querySelectorAll("label[for]").forEach((label) => {
        if (label.getAttribute("for").includes("set_values")) {
          const newFor = label
            .getAttribute("for")
            .replace(/set_values-\d+/, `set_values-${index}`);
          label.setAttribute("for", newFor);
        }
      });
    });

    updateTotalForms(
      `parameters-${parameterIndex}-set_values`,
      setValueForms.length
    );
  }

  // Update total forms count
  function updateTotalForms(formsetName, count) {
    const totalFormsInput = document.getElementById(
      `id_${formsetName}-TOTAL_FORMS`
    );
    if (totalFormsInput) {
      totalFormsInput.value = count;
    }
  }

  // Update sub-parameter options in set value forms
  function updateSubParameterOptions(parameterIndex) {
    // Get all current sub-parameter names for this parameter
    const subParamInputs = document.querySelectorAll(
      `#sub-parameter-forms-${parameterIndex} input[name$="-name"]`
    );
    const subParamNames = Array.from(subParamInputs)
      .map((input, index) => ({ name: input.value.trim(), index: index }))
      .filter((item) => item.name);

    // Update set value sub-parameter dropdowns
    document
      .querySelectorAll(
        `#set-value-forms-${parameterIndex} select[name$="-sub_parameter"]`
      )
      .forEach((select) => {
        const currentValue = select.value;
        select.innerHTML = '<option value="">Select sub-parameter...</option>';

        subParamNames.forEach((item) => {
          const option = document.createElement("option");
          option.value = item.index;
          option.textContent = item.name;
          select.appendChild(option);
        });

        // Restore previous selection if it still exists
        if (
          currentValue &&
          select.querySelector(`option[value="${currentValue}"]`)
        ) {
          select.value = currentValue;
        }
      });
  }

  // Toggle sub-parameters visibility
  function toggleSubParameters(parameterIndex) {
    const checkbox = document.getElementById(
      `id_parameters-${parameterIndex}-has_sub_parameters`
    );
    const container = document.getElementById(
      `sub-parameter-container-${parameterIndex}`
    );
    const subParamSelects = document.querySelectorAll(
      `#set-value-forms-${parameterIndex} .sub-parameter-select`
    );

    if (checkbox && container) {
      if (checkbox.checked) {
        container.classList.remove("hidden");
        subParamSelects.forEach((select) => {
          select.classList.remove("hidden");
          select.querySelector("select").value = "";
        });
        // Add required attribute back when showing
        container.querySelectorAll("[required]").forEach((input) => {
          input.required = true;
        });
      } else {
        container.classList.add("hidden");
        subParamSelects.forEach((select) => {
          select.classList.add("hidden");
          select.querySelector("select").value = "";
        });
        // Remove required attribute when hiding
        container.querySelectorAll("[required]").forEach((input) => {
          input.required = false;
        });
      }

      updateSubParameterOptions(parameterIndex);
    }
  }
  // Add a new parameter
  function addParameter() {
    const parameterForms = document.getElementById("parameter-forms");
    const newIndex = parameterCount;

    const newForm = document.createElement("div");
    newForm.className = "parameter-form formset-container mb-3";
    newForm.dataset.formsetIndex = newIndex;

    newForm.innerHTML = `
            <input type="hidden" name="parameters-${newIndex}-id" value="">
            <input type="hidden" name="parameters-${newIndex}-DELETE" id="id_parameters-${newIndex}-DELETE">
            
            <div class="row">
                <div class="col-md-3 form-group">
                    <label for="id_parameters-${newIndex}-name" class="form-label">Parameter Name</label>
                    <select name="parameters-${newIndex}-name" id="id_parameters-${newIndex}-name" class="form-control parameter-name-select" required>
                        <option value="">Select a parameter...</option>
                    </select>
                </div>
                <div class="col-md-2 form-group">
                    <label for="id_parameters-${newIndex}-unit" class="form-label">Unit</label>
                    <input type="text" name="parameters-${newIndex}-unit" id="id_parameters-${newIndex}-unit" class="form-control" readonly>
                </div>
                <div class="col-md-2 form-group">
                    <label for="id_parameters-${newIndex}-num_readings" class="form-label">Number of Readings</label>
                    <input type="number" name="parameters-${newIndex}-num_readings" id="id_parameters-${newIndex}-num_readings" class="form-control" min="3" max="20" value="5" required>
                </div>
                <div class="col-md-3 form-group">
                    <label for="id_parameters-${newIndex}-standard_reference" class="form-label">Standard Reference</label>
                    <select name="parameters-${newIndex}-standard_reference" id="id_parameters-${newIndex}-standard_reference" class="form-control standard-select">
                        <option value="">Select a standard...</option>
                    </select>
                    <div class="standard-info" id="standard-info-${newIndex}"></div>
                </div>
                <div class="col-md-2 form-group">
                   <button type="button" class="btn btn-delete-gradient btn-sm delete-parameter mt-4 delete-formset">
                      <i class="fas fa-trash-alt me-1"></i> Delete Parameter
                    </button>
                </div>
            </div>
            
            <div class="row">
                <div class="col-md-3 form-group">
                    <label for="id_parameters-${newIndex}-reference_uncertainty" class="form-label">Reference Uncertainty (k=2)</label>
                    <input type="number" name="parameters-${newIndex}-reference_uncertainty" id="id_parameters-${newIndex}-reference_uncertainty" class="form-control" step="0.000001" value="0.001" required readonly>
                </div>
                <div class="col-md-2 form-group">
                    <label for="id_parameters-${newIndex}-coverage_factor" class="form-label">Coverage Factor</label>
                    <input type="number" name="parameters-${newIndex}-coverage_factor" id="id_parameters-${newIndex}-coverage_factor" class="form-control" step="0.1" value="2.0" min="1" required>
                </div>
                <div class="col-md-2 form-group">
                    <label for="id_parameters-${newIndex}-tolerance" class="form-label">Tolerance (±)</label>
                    <input type="number" name="parameters-${newIndex}-tolerance" id="id_parameters-${newIndex}-tolerance" class="form-control" step="0.000001" value="1.0" min="0">
                </div>
                <div class="col-md-2 form-group">
                    <label for="id_parameters-${newIndex}-order" class="form-label">Order</label>
                    <input type="number" name="parameters-${newIndex}-order" id="id_parameters-${newIndex}-order" class="form-control" min="0" value="${newIndex}">
                </div>
                <div class="col-md-3 form-group">
                    <div class="form-check mt-4">
                        <input type="checkbox" class="form-check-input" id="id_parameters-${newIndex}-has_sub_parameters" name="parameters-${newIndex}-has_sub_parameters">
                        <label class="form-check-label" for="id_parameters-${newIndex}-has_sub_parameters">Has Sub-Parameters</label>
                    </div>
                </div>
            </div>

            <!-- Sub-Parameter Formset -->
            <div class="sub-formset-container hidden" id="sub-parameter-container-${newIndex}">
                <h4 class="mb-3">Sub-Parameters</h4>
                
                <!-- Sub-Parameter Management Fields -->
                <input type="hidden" name="parameters-${newIndex}-sub_parameters-TOTAL_FORMS" value="0" id="id_parameters-${newIndex}-sub_parameters-TOTAL_FORMS">
                <input type="hidden" name="parameters-${newIndex}-sub_parameters-INITIAL_FORMS" value="0">
                <input type="hidden" name="parameters-${newIndex}-sub_parameters-MIN_NUM_FORMS" value="0">
                <input type="hidden" name="parameters-${newIndex}-sub_parameters-MAX_NUM_FORMS" value="1000">
                
                <div class="sub-parameter-forms" id="sub-parameter-forms-${newIndex}">
                    <!-- Sub-parameter forms will be added here -->
                </div>
                <button type="button" class="btn btn-primary btn-sm add-sub-parameter add-formset" data-parent-index="${newIndex}">Add Sub-Parameter</button>
            </div>

            <!-- Set Value Formset -->
            <div class="set-value-formset sub-formset-container">
                <h4 class="mb-3">Set Values</h4>
                
                <!-- Set Value Management Fields -->
                <input type="hidden" name="parameters-${newIndex}-set_values-TOTAL_FORMS" value="1" id="id_parameters-${newIndex}-set_values-TOTAL_FORMS">
                <input type="hidden" name="parameters-${newIndex}-set_values-INITIAL_FORMS" value="0">
                <input type="hidden" name="parameters-${newIndex}-set_values-MIN_NUM_FORMS" value="1">
                <input type="hidden" name="parameters-${newIndex}-set_values-MAX_NUM_FORMS" value="1000">
                
                <div class="set-value-forms" id="set-value-forms-${newIndex}">
                    <div class="set-value-form formset-container mb-3" data-set-value-index="0">
                        <input type="hidden" name="parameters-${newIndex}-set_values-0-id" value="">
                        <input type="hidden" name="parameters-${newIndex}-set_values-0-DELETE" id="id_parameters-${newIndex}-set_values-0-DELETE">
                        <div class="row">
                            <div class="col-md-4 form-group">
                                <label for="id_parameters-${newIndex}-set_values-0-value" class="form-label">Set Value</label>
                                <input type="number" name="parameters-${newIndex}-set_values-0-value" id="id_parameters-${newIndex}-set_values-0-value" class="form-control" step="0.000001" placeholder="Set value" required>
                            </div>
                            <div class="col-md-3 form-group">
                                <label for="id_parameters-${newIndex}-set_values-0-order" class="form-label">Order</label>
                                <input type="number" name="parameters-${newIndex}-set_values-0-order" id="id_parameters-${newIndex}-set_values-0-order" class="form-control" min="0" value="0">
                            </div>
                            <div class="col-md-3 form-group sub-parameter-select hidden">
                                <label for="id_parameters-${newIndex}-set_values-0-sub_parameter" class="form-label">Sub-Parameter</label>
                                <select name="parameters-${newIndex}-set_values-0-sub_parameter" id="id_parameters-${newIndex}-set_values-0-sub_parameter" class="form-control">
                                    <option value="">Select sub-parameter...</option>
                                </select>
                            </div>
                            <div class="col-md-2 form-group">
                                <button type="button" class="btn btn-delete-modern btn-sm delete-set-value mt-4 delete-formset">
                                  <i class="fas fa-trash-alt me-1"></i> Delete Set Value
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
                <button type="button" class="btn btn-primary btn-sm add-set-value add-formset" data-parent-index="${newIndex}">Add Set Value</button>
            </div>
        `;

    parameterForms.appendChild(newForm);

    // Populate parameter options for the new form
    const newSelect = newForm.querySelector(".parameter-name-select");
    populateParameterOptions(newSelect);

    updateFormsetIndices();
    parameterCount++;
  }

  // Add a set value
  function addSetValue(button) {
    const parameterIndex = button.dataset.parentIndex;
    const setValueForms = document.getElementById(
      `set-value-forms-${parameterIndex}`
    );
    if (!setValueForms) {
      console.error(
        `Set value forms container not found for parameter index ${parameterIndex}`
      );
      return;
    }

    const setValueCount =
      setValueForms.querySelectorAll(".set-value-form").length;
    const hasSubParams =
      document.getElementById(
        `id_parameters-${parameterIndex}-has_sub_parameters`
      )?.checked || false;

    const newSetValue = document.createElement("div");
    newSetValue.className = "set-value-form formset-container mb-3";
    newSetValue.dataset.setValueIndex = setValueCount;
    newSetValue.innerHTML = `
            <input type="hidden" name="parameters-${parameterIndex}-set_values-${setValueCount}-id" value="">
            <input type="hidden" name="parameters-${parameterIndex}-set_values-${setValueCount}-DELETE" id="id_parameters-${parameterIndex}-set_values-${setValueCount}-DELETE">
            <div class="row">
                <div class="col-md-4 form-group">
                    <label for="id_parameters-${parameterIndex}-set_values-${setValueCount}-value" class="form-label">Set Value</label>
                    <input type="number" name="parameters-${parameterIndex}-set_values-${setValueCount}-value" id="id_parameters-${parameterIndex}-set_values-${setValueCount}-value" class="form-control" step="0.000001" placeholder="Set value" required>
                </div>
                <div class="col-md-3 form-group">
                    <label for="id_parameters-${parameterIndex}-set_values-${setValueCount}-order" class="form-label">Order</label>
                    <input type="number" name="parameters-${parameterIndex}-set_values-${setValueCount}-order" id="id_parameters-${parameterIndex}-set_values-${setValueCount}-order" class="form-control" min="0" value="${setValueCount}">
                </div>
                <div class="col-md-3 form-group sub-parameter-select ${
                  hasSubParams ? "" : "hidden"
                }">
                    <label for="id_parameters-${parameterIndex}-set_values-${setValueCount}-sub_parameter" class="form-label">Sub-Parameter</label>
                    <select name="parameters-${parameterIndex}-set_values-${setValueCount}-sub_parameter" id="id_parameters-${parameterIndex}-set_values-${setValueCount}-sub_parameter" class="form-control">
                        <option value="">Select sub-parameter...</option>
                    </select>
                </div>
                <div class="col-md-2 form-group">
                    <button type="button" class="btn btn-delete-modern btn-sm delete-set-value mt-4 delete-formset">
                      <i class="fas fa-trash-alt me-1"></i> Delete Set Value
                    </button>
                </div>
            </div>
        `;

    setValueForms.appendChild(newSetValue);
    updateSetValueIndices(parameterIndex);
    updateSubParameterOptions(parameterIndex);
  }

  // Add a sub-parameter
  function addSubParameter(button) {
    const parameterIndex = button.dataset.parentIndex;
    const subParamForms = document.getElementById(
      `sub-parameter-forms-${parameterIndex}`
    );
    if (!subParamForms) {
      console.error(
        `Sub parameter forms container not found for parameter index ${parameterIndex}`
      );
      return;
    }

    const subParamCount = subParamForms.querySelectorAll(
      ".sub-parameter-form"
    ).length;

    const newSubParam = document.createElement("div");
    newSubParam.className = "sub-parameter-form formset-container mb-3";
    newSubParam.dataset.subParamIndex = subParamCount;
    newSubParam.innerHTML = `
            <input type="hidden" name="parameters-${parameterIndex}-sub_parameters-${subParamCount}-id" value="">
            <input type="hidden" name="parameters-${parameterIndex}-sub_parameters-${subParamCount}-DELETE" id="id_parameters-${parameterIndex}-sub_parameters-${subParamCount}-DELETE">
            <div class="row">
                <div class="col-md-3 form-group">
                    <label for="id_parameters-${parameterIndex}-sub_parameters-${subParamCount}-name" class="form-label">Sub-Parameter Name</label>
                    <input type="text" name="parameters-${parameterIndex}-sub_parameters-${subParamCount}-name" id="id_parameters-${parameterIndex}-sub_parameters-${subParamCount}-name" class="form-control sub-parameter-name-input" placeholder="Enter sub-parameter name" required>
                </div>
                <div class="col-md-2 form-group">
                    <label for="id_parameters-${parameterIndex}-sub_parameters-${subParamCount}-tolerance" class="form-label">Tolerance (±)</label>
                    <input type="number" name="parameters-${parameterIndex}-sub_parameters-${subParamCount}-tolerance" id="id_parameters-${parameterIndex}-sub_parameters-${subParamCount}-tolerance" class="form-control" step="0.000001" value="1.0" min="0">
                </div>
                <div class="col-md-2 form-group">
                    <label for="id_parameters-${parameterIndex}-sub_parameters-${subParamCount}-order" class="form-label">Order</label>
                    <input type="number" name="parameters-${parameterIndex}-sub_parameters-${subParamCount}-order" id="id_parameters-${parameterIndex}-sub_parameters-${subParamCount}-order" class="form-control" min="0" value="${subParamCount}">
                </div>
                <div class="col-md-2 form-group">
                    <button type="button" class="btn btn-danger btn-sm delete-sub-parameter mt-4 delete-formset">Delete Sub-Parameter</button>
                </div>
            </div>
        `;

    subParamForms.appendChild(newSubParam);
    updateSubParameterIndices(parameterIndex);
    updateSubParameterOptions(parameterIndex);
  }

  // Delete a parameter
  function deleteParameter(button) {
    const parameterForm = button.closest(".parameter-form");
    const parameterForms = document.getElementById("parameter-forms");
    const visibleForms = parameterForms.querySelectorAll(
      ".parameter-form:not(.hidden)"
    );

    if (visibleForms.length > 1) {
      const deleteInput = parameterForm.querySelector('input[name$="-DELETE"]');
      if (deleteInput) {
        deleteInput.value = "on";
      }
      parameterForm.classList.add("hidden");
      updateFormsetIndices();
    } else {
      alert("At least one parameter is required.");
    }
  }

  // Delete a sub-parameter
  function deleteSubParameter(button) {
    const subParamForm = button.closest(".sub-parameter-form");
    const parameterIndex =
      subParamForm.closest(".parameter-form").dataset.formsetIndex;
    const deleteInput = subParamForm.querySelector('input[name$="-DELETE"]');
    if (deleteInput) {
      deleteInput.value = "on";
    }
    subParamForm.classList.add("hidden");
    updateSubParameterIndices(parameterIndex);
    updateSubParameterOptions(parameterIndex);
  }

  // Delete a set value
  function deleteSetValue(button) {
    const setValueForm = button.closest(".set-value-form");
    const parameterIndex =
      setValueForm.closest(".parameter-form").dataset.formsetIndex;
    const setValueForms = document.getElementById(
      `set-value-forms-${parameterIndex}`
    );

    if (!setValueForms) return;

    const visibleForms = setValueForms.querySelectorAll(
      ".set-value-form:not(.hidden)"
    );
    if (visibleForms.length > 1) {
      const deleteInput = setValueForm.querySelector('input[name$="-DELETE"]');
      if (deleteInput) {
        deleteInput.value = "on";
      }
      setValueForm.classList.add("hidden");
      updateSetValueIndices(parameterIndex);
    } else {
      alert("At least one set value is required.");
    }
  }

  // Clear the form
  function clearForm() {
    if (
      !confirm(
        "Are you sure you want to clear all form data? This action cannot be undone."
      )
    ) {
      return;
    }

    const parameterForms = document.getElementById("parameter-forms");

    // Keep only the first parameter form
    const allForms = parameterForms.querySelectorAll(".parameter-form");
    for (let i = 1; i < allForms.length; i++) {
      allForms[i].remove();
    }

    // Reset the first form
    const firstForm = parameterForms.querySelector(".parameter-form");
    if (firstForm) {
      // Clear all input values except hidden fields
      firstForm
        .querySelectorAll('input[type="text"], input[type="number"], textarea')
        .forEach((input) => {
          if (input.type !== "hidden") {
            input.value = input.defaultValue || "";
            input.classList.remove("auto-filled");
          }
        });

      // Reset selects
      firstForm.querySelectorAll("select").forEach((select) => {
        if (select.classList.contains("parameter-name-select")) {
          populateParameterOptions(select);
        } else {
          select.innerHTML = '<option value="">Select...</option>';
        }
        select.selectedIndex = 0;
      });

      // Reset checkbox and hide sub-parameters
      const checkbox = firstForm.querySelector('input[type="checkbox"]');
      if (checkbox) {
        checkbox.checked = false;
      }
      const subContainer = firstForm.querySelector(".sub-formset-container");
      if (subContainer) {
        subContainer.classList.add("hidden");
      }

      // Clear sub-parameters
      const subParamForms = firstForm.querySelector(".sub-parameter-forms");
      if (subParamForms) {
        subParamForms.innerHTML = "";
      }

      // Reset set values to one default
      const setValueForms = firstForm.querySelector(".set-value-forms");
      if (setValueForms) {
        setValueForms.innerHTML = `
                    <div class="set-value-form formset-container mb-3" data-set-value-index="0">
                        <input type="hidden" name="parameters-0-set_values-0-id" value="">
                        <input type="hidden" name="parameters-0-set_values-0-DELETE" id="id_parameters-0-set_values-0-DELETE">
                        <div class="row">
                            <div class="col-md-4 form-group">
                                <label for="id_parameters-0-set_values-0-value" class="form-label">Set Value</label>
                                <input type="number" name="parameters-0-set_values-0-value" id="id_parameters-0-set_values-0-value" class="form-control" step="0.000001" placeholder="Set value" required>
                            </div>
                            <div class="col-md-3 form-group">
                                <label for="id_parameters-0-set_values-0-order" class="form-label">Order</label>
                                <input type="number" name="parameters-0-set_values-0-order" id="id_parameters-0-set_values-0-order" class="form-control" min="0" value="0">
                            </div>
                            <div class="col-md-3 form-group sub-parameter-select hidden">
                                <label for="id_parameters-0-set_values-0-sub_parameter" class="form-label">Sub-Parameter</label>
                                <select name="parameters-0-set_values-0-sub_parameter" id="id_parameters-0-set_values-0-sub_parameter" class="form-control">
                                    <option value="">Select sub-parameter...</option>
                                </select>
                            </div>
                            <div class="col-md-2 form-group">
                                <button type="button" class="btn btn-danger btn-sm delete-set-value mt-4 delete-formset">Delete Set Value</button>
                            </div>
                        </div>
                    </div>
                `;
      }

      // Clear standard info
      const standardInfoDiv = firstForm.querySelector(".standard-info");
      if (standardInfoDiv) {
        standardInfoDiv.innerHTML = "";
      }
    }

    // Clear procedure details
    document
      .querySelectorAll(
        '#procedureForm input[type="text"], #procedureForm textarea'
      )
      .forEach((input) => {
        if (!input.closest(".parameter-form") && !input.name.includes("csrf")) {
          input.value = "";
        }
      });

    // Reset environmental conditions to defaults
    const tempInput = document.getElementById("id_temperature");
    const humidityInput = document.getElementById("id_humidity");
    const pressureInput = document.getElementById("id_pressure");
    if (tempInput) tempInput.value = "23.0";
    if (humidityInput) humidityInput.value = "50.0";
    if (pressureInput) pressureInput.value = "101.325";

    // Reset counters and indices
    parameterCount = 1;
    updateFormsetIndices();
  }

  // Handle parameter name change
  function handleParameterChange(select) {
    const parameterForm = select.closest(".parameter-form");
    if (!parameterForm) return;

    const parameterIndex = parameterForm.dataset.formsetIndex;
    const parameterName = select.value;
    const unitInput = document.getElementById(
      `id_parameters-${parameterIndex}-unit`
    );
    const standardSelect = document.getElementById(
      `id_parameters-${parameterIndex}-standard_reference`
    );

    // Find selected parameter
    const parameter = parametersCache.find(
      (param) => param.name === parameterName
    );

    // Update unit and reset other fields
    if (parameter && unitInput) {
      unitInput.value = parameter.unit;
      unitInput.classList.add("auto-filled");
      if (standardSelect) {
        loadStandards(parameterName, standardSelect);
      }
    } else {
      if (unitInput) {
        unitInput.value = "";
        unitInput.classList.remove("auto-filled");
      }
      if (standardSelect) {
        standardSelect.innerHTML =
          '<option value="">Select a standard...</option>';
      }
    }

    resetStandardDetails(parameterIndex);
  }

  // Handle standard change
  function handleStandardChange(select) {
    const parameterForm = select.closest(".parameter-form");
    if (!parameterForm) return;

    const parameterIndex = parameterForm.dataset.formsetIndex;
    const parameterNameSelect = document.getElementById(
      `id_parameters-${parameterIndex}-name`
    );
    const parameterName = parameterNameSelect ? parameterNameSelect.value : "";

    if (select.value && parameterName) {
      getStandardDetails(select.value, parameterName, parameterIndex);
    } else {
      resetStandardDetails(parameterIndex);
    }
  }

  // Handle sub-parameter name change
  function handleSubParameterNameChange(input) {
    const subParamForm = input.closest(".sub-parameter-form");
    if (!subParamForm) return;

    const parameterForm = subParamForm.closest(".parameter-form");
    if (!parameterForm) return;

    const parameterIndex = parameterForm.dataset.formsetIndex;
    updateSubParameterOptions(parameterIndex);
  }

  // Attach event listeners
  function attachEventListeners() {
    // Parameter name change
    document.addEventListener("change", (e) => {
      if (e.target.classList.contains("parameter-name-select")) {
        handleParameterChange(e.target);
      }
    });

    // Standard change
    document.addEventListener("change", (e) => {
      if (e.target.classList.contains("standard-select")) {
        handleStandardChange(e.target);
      }
    });

    // Sub-parameter name change
    document.addEventListener("input", (e) => {
      if (e.target.classList.contains("sub-parameter-name-input")) {
        handleSubParameterNameChange(e.target);
      }
    });

    // Sub-parameter checkbox change
    document.addEventListener("change", (e) => {
      if (e.target.matches('input[name$="has_sub_parameters"]')) {
        const parameterForm = e.target.closest(".parameter-form");
        if (parameterForm) {
          const parameterIndex = parameterForm.dataset.formsetIndex;
          toggleSubParameters(parameterIndex);
        }
      }
    });

    // Button clicks (using event delegation)
    document.addEventListener("click", (e) => {
      if (e.target.classList.contains("add-parameter")) {
        e.preventDefault();
        addParameter();
      } else if (e.target.classList.contains("add-set-value")) {
        e.preventDefault();
        addSetValue(e.target);
      } else if (e.target.classList.contains("add-sub-parameter")) {
        e.preventDefault();
        addSubParameter(e.target);
      } else if (e.target.classList.contains("delete-parameter")) {
        e.preventDefault();
        deleteParameter(e.target);
      } else if (e.target.classList.contains("delete-sub-parameter")) {
        e.preventDefault();
        deleteSubParameter(e.target);
      } else if (e.target.classList.contains("delete-set-value")) {
        e.preventDefault();
        deleteSetValue(e.target);
      }
    });

    // Clear form button
    const clearFormBtn = document.getElementById("clearForm");
    if (clearFormBtn) {
      clearFormBtn.addEventListener("click", (e) => {
        e.preventDefault();
        clearForm();
      });
    }

    // Form submission validation
    const procedureForm = document.getElementById("procedureForm");
    if (procedureForm) {
      procedureForm.addEventListener("submit", function (e) {
        // Add any custom validation here if needed
        const visibleParameterForms = document.querySelectorAll(
          ".parameter-form:not(.hidden)"
        );
        if (visibleParameterForms.length === 0) {
          e.preventDefault();
          alert("At least one parameter is required.");
          return false;
        }

        // Validate that each parameter has at least one set value
        let hasInvalidParameter = false;
        visibleParameterForms.forEach((form) => {
          const parameterIndex = form.dataset.formsetIndex;
          const setValueForms = document.querySelectorAll(
            `#set-value-forms-${parameterIndex} .set-value-form:not(.hidden)`
          );
          if (setValueForms.length === 0) {
            hasInvalidParameter = true;
          }
        });

        if (hasInvalidParameter) {
          e.preventDefault();
          alert("Each parameter must have at least one set value.");
          return false;
        }
      });
    }
  }

  // Initialize the form
  initializeForm();
});

// Form submission validation
const procedureForm = document.getElementById('procedureForm');
if (procedureForm) {
    procedureForm.addEventListener('submit', function(e) {
        // Validate visible fields only
        const visibleParameterForms = document.querySelectorAll('.parameter-form:not(.hidden)');
        if (visibleParameterForms.length === 0) {
            e.preventDefault();
            alert('At least one parameter is required.');
            return false;
        }
        
        // Validate that each parameter has at least one set value
        let hasInvalidParameter = false;
        visibleParameterForms.forEach(form => {
            const parameterIndex = form.dataset.formsetIndex;
            const setValueForms = document.querySelectorAll(`#set-value-forms-${parameterIndex} .set-value-form:not(.hidden)`);
            if (setValueForms.length === 0) {
                hasInvalidParameter = true;
            }
            
            // Only validate sub-parameters if they're visible
            const hasSubParams = document.getElementById(`id_parameters-${parameterIndex}-has_sub_parameters`)?.checked || false;
            if (hasSubParams) {
                const subParamForms = document.querySelectorAll(`#sub-parameter-forms-${parameterIndex} .sub-parameter-form:not(.hidden)`);
                subParamForms.forEach(subForm => {
                    const nameInput = subForm.querySelector('input[name$="-name"]');
                    if (nameInput && !nameInput.value.trim()) {
                        hasInvalidParameter = true;
                        nameInput.focus();
                    }
                });
            }
        });
        
        if (hasInvalidParameter) {
            e.preventDefault();
            alert('Please fill in all required fields.');
            return false;
        }
    });
};

