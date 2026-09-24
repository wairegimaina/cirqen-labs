// JS for Calibration/standard_form.html (Create/Edit Standard)
// Relies on a global `window.STANDARD_FORM_CONFIG` set by the template:
//   {
//     parameterCreateUrl: "{% url 'calibration:parameter_create' %}",
//     parameterOptions: [{ id, name }, ...]
//   }

(function () {
  function getManagementInput(suffix) {
    return document.querySelector(
      `#standardForm input[name="parameters-${suffix}"]`,
    );
  }

  function buildParameterOptionsHtml() {
    const options =
      (window.STANDARD_FORM_CONFIG &&
        window.STANDARD_FORM_CONFIG.parameterOptions) ||
      [];
    return options
      .map((p) => `<option value="${escapeHTML(p.id)}">${escapeHTML(p.name)}</option>`)
      .join("");
  }

  // Turn a Django form.errors object (e.g. {"name": ["This field is required."]})
  // into a readable string for display in an alert.
  function formatFormErrors(errors) {
    if (!errors) return "";
    if (typeof errors === "string") return errors;
    try {
      return Object.entries(errors)
        .map(
          ([field, messages]) =>
            `${field}: ${(Array.isArray(messages) ? messages : [messages]).join(" ")}`,
        )
        .join(" ");
    } catch (e) {
      return "";
    }
  }

  // Add new parameter row to the standard form
  window.addParameter = function () {
    const parametersSection = document.getElementById("parameters-section");
    const totalFormsInput = getManagementInput("TOTAL_FORMS");
    const index = parseInt(totalFormsInput.value, 10);

    const newParameter = document.createElement("div");
    newParameter.className = "parameter-row";
    newParameter.setAttribute("data-parameter-index", index);

    newParameter.innerHTML = `
            <div class="row g-3 align-items-end">
                <div class="col-md-5">
                    <label class="form-label">Parameter</label>
                    <select class="form-select parameter-select" name="parameters-${index}-parameter" required>
                        <option value="">Select Parameter</option>
                        ${buildParameterOptionsHtml()}
                    </select>
                </div>
                <div class="col-md-4">
                    <label class="form-label">Uncertainty (k=2)</label>
                    <div class="input-group">
                        <input type="number" class="form-control" name="parameters-${index}-uncertainty"
                               step="0.000001" placeholder="0.001" required>
                        <span class="input-group-text">±</span>
                    </div>
                </div>
                <div class="col-md-3 text-end">
                    <button type="button" class="btn btn-outline-danger btn-sm delete-parameter" onclick="removeParameter(this)"
                            title="Remove parameter" aria-label="Remove parameter">
                        <i class="fas fa-trash"></i> Remove
                    </button>
                </div>
            </div>
            <input type="hidden" name="parameters-${index}-id" value="">
            <input type="hidden" name="parameters-${index}-DELETE" value="">
        `;

    parametersSection.appendChild(newParameter);

    // Keep Django's real management form in sync
    totalFormsInput.value = index + 1;

    // Animate the new parameter row in
    newParameter.style.opacity = "0";
    newParameter.style.transform = "translateY(20px)";
    setTimeout(() => {
      newParameter.style.transition = "all 0.3s ease";
      newParameter.style.opacity = "1";
      newParameter.style.transform = "translateY(0)";
    }, 100);
  };

  // Remove a parameter row
  window.removeParameter = function (button) {
    const parameterRow = button.closest(".parameter-row");
    const parametersSection = document.getElementById("parameters-section");

    const visibleRows = Array.from(parametersSection.children).filter(
      (row) => row.style.display !== "none",
    );
    if (visibleRows.length <= 1) {
      alert("At least one parameter is required.");
      return;
    }

    const idField = parameterRow.querySelector('input[name$="-id"]');
    const isExistingRow = !!(idField && idField.value);

    parameterRow.style.transition = "all 0.3s ease";
    parameterRow.style.opacity = "0";
    parameterRow.style.transform = "translateY(-20px)";

    setTimeout(() => {
      if (isExistingRow) {
        // Existing (already-saved) parameter row: mark it for deletion
        // but keep it in the DOM (hidden) so the DELETE flag is submitted
        // and Django's formset can remove the related row server-side.
        const deleteField = parameterRow.querySelector(
          'input[name$="-DELETE"]',
        );
        if (deleteField) {
          deleteField.value = "on";
        }

        const requiredFields = parameterRow.querySelectorAll("[required]");
        requiredFields.forEach((field) => {
          field.removeAttribute("required");
          field.disabled = true; // Also disable to prevent submission
        });

        parameterRow.style.display = "none";
      } else {
        // Newly added (unsaved) row: remove it entirely and shrink
        // Django's TOTAL_FORMS count to match.
        parametersSection.removeChild(parameterRow);
        const totalFormsInput = getManagementInput("TOTAL_FORMS");
        totalFormsInput.value = Math.max(
          0,
          parseInt(totalFormsInput.value, 10) - 1,
        );
      }
    }, 300);
  };

  document.addEventListener("DOMContentLoaded", function () {
    const standardForm = document.getElementById("standardForm");
    const calDateInput = document.getElementById("id_calibration_date");
    const dueDateInput = document.getElementById("id_calibration_due_date");

    // Validate standard form on submit
    standardForm.addEventListener("submit", function (e) {
      const parameterRows = document.querySelectorAll(
        '.parameter-row:not([style*="display: none"])',
      );
      if (parameterRows.length === 0) {
        e.preventDefault();
        alert("At least one parameter is required.");
        return false;
      }

      const calDate = new Date(calDateInput.value);
      const dueDate = new Date(dueDateInput.value);

      if (dueDate <= calDate) {
        e.preventDefault();
        alert("Due date must be after calibration date.");
        return false;
      }

      return true;
    });

    // Auto-calculate due date whenever the calibration date changes
    calDateInput.addEventListener("change", function () {
      const calDate = new Date(this.value);
      if (calDate) {
        const dueDate = new Date(calDate);
        dueDate.setFullYear(dueDate.getFullYear() + 1);
        dueDateInput.value = dueDate.toISOString().split("T")[0];
      }
    });

    // Default calibration date to today, but only when creating a new
    // standard (don't clobber an existing date when editing).
    if (!calDateInput.value) {
      calDateInput.value = new Date().toLocaleDateString("en-CA", { timeZone: "Africa/Nairobi" });
      calDateInput.dispatchEvent(new Event("change"));
    }

    // Handle the "Create New Parameter" form submission via AJAX
    const parameterForm = document.getElementById("parameterForm");
    parameterForm.addEventListener("submit", function (e) {
      e.preventDefault();
      const form = this;
      const formData = new FormData(form);
      const submitButton = document.getElementById("parameterSubmitBtn");

      if (!submitButton) {
        console.error("Submit button not found");
        return;
      }

      const buttonIcon = submitButton.querySelector(".fa-save");

      // Show loading state
      submitButton.disabled = true;
      if (buttonIcon) {
        buttonIcon.className = "fas fa-spinner fa-spin me-2";
      }

      const url = window.STANDARD_FORM_CONFIG.parameterCreateUrl;

      fetch(url, {
        method: "POST",
        body: formData,
        headers: {
          "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value,
          "X-Requested-With": "XMLHttpRequest",
        },
      })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
          }
          return response.json();
        })
        .then((data) => {
          if (data.success) {
            // Update all existing parameter dropdowns
            const selects = document.querySelectorAll(".parameter-select");
            selects.forEach((select) => {
              const option = document.createElement("option");
              option.value = data.parameter.id;
              option.text = data.parameter.name;
              select.appendChild(option);
            });

            // Remember it so any rows added later also get it
            if (
              window.STANDARD_FORM_CONFIG &&
              Array.isArray(window.STANDARD_FORM_CONFIG.parameterOptions)
            ) {
              window.STANDARD_FORM_CONFIG.parameterOptions.push({
                id: data.parameter.id,
                name: data.parameter.name,
              });
            }

            // Show success message
            const alertEl = document.createElement("div");
            alertEl.className =
              "alert alert-success alert-dismissible fade show";
            alertEl.innerHTML = `
                            <i class="fas fa-check-circle me-2"></i>
                            Parameter "${escapeHTML(data.parameter.name)}" created successfully!
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        `;
            form.parentElement.insertBefore(alertEl, form);

            // Auto-dismiss after 3 seconds
            setTimeout(() => {
              alertEl.remove();
            }, 3000);

            form.reset();
          } else {
            const errorText =
              formatFormErrors(data.errors) || "Failed to create parameter.";
            const alertEl = document.createElement("div");
            alertEl.className =
              "alert alert-danger alert-dismissible fade show";
            alertEl.innerHTML = `
                            <i class="fas fa-exclamation-circle me-2"></i>
                            Error: ${escapeHTML(errorText)}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        `;
            form.parentElement.insertBefore(alertEl, form);
          }
        })
        .catch((error) => {
          console.error("Error:", error);
          const alertEl = document.createElement("div");
          alertEl.className = "alert alert-danger alert-dismissible fade show";
          alertEl.innerHTML = `
                        <i class="fas fa-exclamation-triangle me-2"></i>
                        Error: Failed to create parameter. ${escapeHTML(error.message)}
                        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                    `;
          form.parentElement.insertBefore(alertEl, form);
        })
        .finally(() => {
          submitButton.disabled = false;
          if (buttonIcon) {
            buttonIcon.className = "fas fa-save me-2";
          }
        });
    });
  });
})();
