(function () {
  "use strict";

  // ----------------------------------------------------------------
  // Helpers
  // ----------------------------------------------------------------

  function getCsrf() {
    var el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  function showGlobalAlert(message, type, duration) {
    var alertDiv = document.createElement("div");
    alertDiv.className =
      "alert alert-" +
      type +
      " alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3";
    alertDiv.style.zIndex = "9999";
    var icon = type === "success" ? "check-circle" : "exclamation-circle";
    alertDiv.innerHTML =
      '<i class="fas fa-' +
      icon +
      ' me-2"></i>' +
      message +
      '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
    document.body.appendChild(alertDiv);
    setTimeout(function () {
      alertDiv.remove();
    }, duration || 5000);
  }

  function actionRow(type, id) {
    return (
      '<button class="btn btn-edit btn-action me-2" data-action="edit-' +
      type +
      '" data-id="' +
      id +
      '">' +
      '<i class="fas fa-edit"></i> Update' +
      "</button>" +
      '<button class="btn btn-delete btn-action" data-action="delete-' +
      type +
      '" data-id="' +
      id +
      '">' +
      '<i class="fas fa-trash"></i> Delete' +
      "</button>"
    );
  }

  function flashRow(row) {
    row.style.backgroundColor = "#d4edda";
    setTimeout(function () {
      row.style.transition = "background-color 0.5s ease";
      row.style.backgroundColor = "";
    }, 100);
  }

  // ----------------------------------------------------------------
  // AJAX table loader
  // ----------------------------------------------------------------

  function loadTables() {
    var stdBody = document.getElementById("standards-table-body");
    var paramBody = document.getElementById("parameters-table-body");

    stdBody.innerHTML =
      '<tr><td colspan="7" class="text-center text-muted py-3"><i class="fas fa-spinner fa-spin me-2"></i>Loading…</td></tr>';
    paramBody.innerHTML =
      '<tr><td colspan="4" class="text-center text-muted py-3"><i class="fas fa-spinner fa-spin me-2"></i>Loading…</td></tr>';

    fetch("/calibration/api/standards-parameters/", {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        renderStandards(data.standards, stdBody);
        renderParameters(data.parameters, paramBody);
      })
      .catch(function (err) {
        console.error("loadTables:", err);
        stdBody.innerHTML =
          '<tr><td colspan="7" class="text-center text-danger">Failed to load standards.</td></tr>';
        paramBody.innerHTML =
          '<tr><td colspan="4" class="text-center text-danger">Failed to load parameters.</td></tr>';
      });
  }

  function renderStandards(rows, tbody) {
    if (!rows || !rows.length) {
      tbody.innerHTML =
        '<tr><td colspan="7" class="text-center text-muted">No standards available.</td></tr>';
      return;
    }
    tbody.innerHTML = rows
      .map(function (s) {
        return (
          '<tr data-standard-id="' +
          s.id +
          '">' +
          "<td>" +
          (s.name || "") +
          "</td>" +
          "<td>" +
          (s.model_number || "") +
          "</td>" +
          "<td>" +
          (s.serial_number || "") +
          "</td>" +
          "<td>" +
          (s.manufacturer || "") +
          "</td>" +
          "<td>" +
          (s.calibration_date || "") +
          "</td>" +
          "<td>" +
          (s.calibration_due_date || "") +
          "</td>" +
          "<td>" +
          actionRow("standard", s.id) +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderParameters(rows, tbody) {
    if (!rows || !rows.length) {
      tbody.innerHTML =
        '<tr><td colspan="4" class="text-center text-muted">No parameters available.</td></tr>';
      return;
    }
    tbody.innerHTML = rows
      .map(function (p) {
        return (
          '<tr data-parameter-id="' +
          p.id +
          '">' +
          "<td>" +
          (p.name || "") +
          "</td>" +
          "<td>" +
          (p.symbol || "—") +
          "</td>" +
          "<td>" +
          (p.unit || "") +
          "</td>" +
          "<td>" +
          actionRow("parameter", p.id) +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  // ----------------------------------------------------------------
  // Standard edit modal
  // ----------------------------------------------------------------

  function StandardEditModal() {
    this.modal = null;
    this.form = null;
    this.parametersContainer = null;
    this.parameterIndex = 0;
    this.init();
  }

  StandardEditModal.prototype.init = function () {
    this.modal = document.getElementById("standardEditModal");
    this.form = document.getElementById("standardEditForm");
    this.parametersContainer = document.getElementById(
      "modal-parameters-container",
    );
    if (!this.modal || !this.form) return;
    this._attachListeners();
  };

  StandardEditModal.prototype._attachListeners = function () {
    var self = this;

    var addBtn = document.getElementById("modal-add-parameter");
    if (addBtn)
      addBtn.addEventListener("click", function () {
        self.addParameterRow();
      });

    var saveBtn = document.getElementById("modal-save-btn");
    if (saveBtn)
      saveBtn.addEventListener("click", function () {
        self.saveStandard();
      });

    this.parametersContainer.addEventListener("click", function (e) {
      var btn = e.target.closest(".remove-parameter");
      if (btn) self.removeParameterRow(btn.closest(".parameter-row"));
    });

    var calDateInput = document.getElementById("modal_calibration_date");
    if (calDateInput) {
      calDateInput.addEventListener("change", function () {
        self.autoCalculateDueDate();
      });
    }
  };

  StandardEditModal.prototype.open = function (standardId) {
    var self = this;
    var modalInstance = new bootstrap.Modal(this.modal);
    var loading = document.getElementById("modal-loading");
    var alerts = document.getElementById("modal-alerts");

    loading.style.display = "block";
    loading.innerHTML =
      '<div class="text-center py-5"><div class="spinner-border text-primary" style="width:3rem;height:3rem;" role="status"><span class="visually-hidden">Loading…</span></div><p class="mt-3 text-muted fw-bold">Loading standard data…</p></div>';
    this.form.style.display = "none";
    alerts.innerHTML = "";
    modalInstance.show();

    Promise.all([
      fetch("/calibration/standards/" + standardId + "/edit/", {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      }),
      fetch("/calibration/api/standard/" + standardId + "/parameters/", {
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
        },
      }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      }),
    ])
      .then(function (results) {
        var doc = new DOMParser().parseFromString(results[0], "text/html");
        self._populateForm(doc, standardId, results[1]);
        loading.style.display = "none";
        self.form.style.display = "block";
      })
      .catch(function (err) {
        console.error("StandardEditModal.open:", err);
        loading.style.display = "none";
        self.showAlert(
          "Failed to load standard data. Please try again.",
          "danger",
        );
      });
  };

  StandardEditModal.prototype._populateForm = function (
    doc,
    standardId,
    apiData,
  ) {
    document.getElementById("standard_id").value = standardId;

    var csrfInput = doc.querySelector("[name=csrfmiddlewaretoken]");
    if (csrfInput) {
      this.form.querySelector("[name=csrfmiddlewaretoken]").value =
        csrfInput.value;
    }

    var fields = [
      "name",
      "model_number",
      "serial_number",
      "manufacturer",
      "certificate_number",
      "calibration_date",
      "calibration_due_date",
      "calibration_agency",
    ];
    fields.forEach(function (field) {
      var src = doc.querySelector('[name="' + field + '"]');
      var tgt = document.getElementById("modal_" + field);
      if (src && tgt) tgt.value = src.value || "";
    });

    // Build parameter_id → StandardParameter ID map from API
    var idMap = {};
    if (apiData && apiData.success && apiData.parameters) {
      apiData.parameters.forEach(function (p) {
        idMap[p.parameter_id] = p.id;
      });
    }

    this.parametersContainer.innerHTML = "";
    this.parameterIndex = 0;

    var totalInput = doc.querySelector('[name="parameters-TOTAL_FORMS"]');
    var total = totalInput ? parseInt(totalInput.value, 10) : 0;

    for (var i = 0; i < total; i++) {
      var paramSel = doc.querySelector(
        '[name="parameters-' + i + '-parameter"]',
      );
      var uncInput = doc.querySelector(
        '[name="parameters-' + i + '-uncertainty"]',
      );
      var idInput = doc.querySelector('[name="parameters-' + i + '-id"]');
      var deleteChk = doc.querySelector('[name="parameters-' + i + '-DELETE"]');

      if (!paramSel || !paramSel.value || !uncInput || !uncInput.value)
        continue;
      if (deleteChk && deleteChk.checked) continue;

      var paramValue = paramSel.value;
      var idValue =
        idInput && idInput.value ? idInput.value : idMap[paramValue] || "";

      this.addParameterRow({
        parameter: paramValue,
        uncertainty: uncInput.value,
        id: idValue,
      });
    }

    document.getElementById("modal-TOTAL_FORMS").value = this.parameterIndex;
    document.getElementById("modal-INITIAL_FORMS").value = this.parameterIndex;
  };

  StandardEditModal.prototype.addParameterRow = function (data) {
    data = data || {};
    var template = document.getElementById("parameter-row-template");
    var clone = template.content.cloneNode(true);
    var row = clone.querySelector(".parameter-row");
    var idx = this.parameterIndex;

    row.setAttribute("data-index", idx);
    row.querySelectorAll('[name*="__INDEX__"]').forEach(function (el) {
      el.name = el.name.replace("__INDEX__", idx);
    });

    if (data.parameter) {
      var sel = row.querySelector('[name*="-parameter"]');
      if (sel) sel.value = data.parameter;
    }
    if (data.uncertainty) {
      var unc = row.querySelector('[name*="-uncertainty"]');
      if (unc) unc.value = data.uncertainty;
    }
    if (data.id) {
      var hidden = row.querySelector('[name*="-id"]');
      if (hidden) hidden.value = data.id;
    }

    this.parametersContainer.appendChild(row);
    this.parameterIndex++;
    document.getElementById("modal-TOTAL_FORMS").value = this.parameterIndex;
  };

  StandardEditModal.prototype.removeParameterRow = function (row) {
    var idInput = row.querySelector('[name*="-id"]');
    if (idInput && idInput.value) {
      var del = row.querySelector('[name*="-DELETE"]');
      if (del) del.value = "on";
      row.style.display = "none";
    } else {
      row.remove();
      this.parameterIndex = Math.max(0, this.parameterIndex - 1);
      document.getElementById("modal-TOTAL_FORMS").value = this.parameterIndex;
    }
  };

  StandardEditModal.prototype.autoCalculateDueDate = function () {
    var val = document.getElementById("modal_calibration_date").value;
    if (val) {
      var d = new Date(val);
      d.setFullYear(d.getFullYear() + 1);
      document.getElementById("modal_calibration_due_date").value = d
        .toISOString()
        .split("T")[0];
    }
  };

  StandardEditModal.prototype.saveStandard = function () {
    var self = this;
    var saveBtn = document.getElementById("modal-save-btn");
    var spinner = saveBtn.querySelector(".spinner-border");
    var btnText = saveBtn.querySelector(".btn-text");

    this.clearErrors();

    if (!this.form.checkValidity()) {
      this.form.classList.add("was-validated");
      this.showAlert("Please fill in all required fields.", "danger");
      return;
    }

    saveBtn.disabled = true;
    spinner.classList.remove("d-none");
    btnText.textContent = "Updating…";
    this._showOverlay("saving");

    var standardId = document.getElementById("standard_id").value;

    fetch("/calibration/standards/" + standardId + "/edit/", {
      method: "POST",
      body: new FormData(this.form),
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) {
        return r.text().then(function (text) {
          try {
            return { ok: r.ok, data: JSON.parse(text) };
          } catch (e) {
            throw { data: { error: "Server returned non-JSON response." } };
          }
        });
      })
      .then(function (result) {
        if (result.ok && result.data.success) {
          self._showOverlay("success");
          self._updateStandardRow(result.data.standard);
          setTimeout(function () {
            self._hideOverlay();
            bootstrap.Modal.getInstance(self.modal).hide();
          }, 1500);
        } else {
          self._hideOverlay();
          if (result.data.errors) self.displayErrors(result.data.errors);
          self.showAlert(
            result.data.message ||
              result.data.error ||
              "Please correct the errors.",
            "danger",
          );
        }
      })
      .catch(function (err) {
        console.error("saveStandard:", err);
        self._hideOverlay();
        self.showAlert(
          (err.data && err.data.error) || "An error occurred while saving.",
          "danger",
        );
      })
      .finally(function () {
        saveBtn.disabled = false;
        spinner.classList.add("d-none");
        btnText.textContent = "Save Changes";
      });
  };

  StandardEditModal.prototype._updateStandardRow = function (s) {
    var row = document.querySelector('[data-standard-id="' + s.id + '"]');
    if (!row) return;
    row.innerHTML =
      "<td>" +
      (s.name || "") +
      "</td>" +
      "<td>" +
      (s.model_number || "") +
      "</td>" +
      "<td>" +
      (s.serial_number || "") +
      "</td>" +
      "<td>" +
      (s.manufacturer || "") +
      "</td>" +
      "<td>" +
      (s.calibration_date || "") +
      "</td>" +
      "<td>" +
      (s.calibration_due_date || "") +
      "</td>" +
      "<td>" +
      actionRow("standard", s.id) +
      "</td>";
    flashRow(row);
  };

  StandardEditModal.prototype._showOverlay = function (state) {
    var existing = document.getElementById("sp-overlay");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "sp-overlay";
    overlay.style.cssText =
      "position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:9999;display:flex;align-items:center;justify-content:center;";

    var box = document.createElement("div");
    box.style.cssText =
      "background:#fff;border-radius:15px;padding:40px 60px;text-align:center;min-width:360px;box-shadow:0 10px 40px rgba(0,0,0,.3);";

    if (state === "saving") {
      box.innerHTML =
        '<div class="spinner-border text-primary" style="width:4rem;height:4rem;" role="status"></div>' +
        '<h4 class="mt-4 mb-2 text-primary fw-bold">Updating Standard</h4>' +
        '<p class="text-muted mb-0">Please wait…</p>';
    } else {
      box.innerHTML =
        '<div style="width:4rem;height:4rem;margin:0 auto;background:#198754;border-radius:50%;display:flex;align-items:center;justify-content:center;">' +
        '<i class="fas fa-check" style="font-size:2rem;color:#fff;"></i>' +
        "</div>" +
        '<h4 class="mt-4 mb-2 text-success fw-bold">Success!</h4>' +
        '<p class="text-muted mb-0">Standard updated successfully</p>';
    }

    overlay.appendChild(box);
    document.body.appendChild(overlay);
  };

  StandardEditModal.prototype._hideOverlay = function () {
    var el = document.getElementById("sp-overlay");
    if (el) el.remove();
  };

  StandardEditModal.prototype.clearErrors = function () {
    this.form.querySelectorAll(".is-invalid").forEach(function (el) {
      el.classList.remove("is-invalid");
    });
    this.form.querySelectorAll(".invalid-feedback").forEach(function (el) {
      el.textContent = "";
    });
    this.form.classList.remove("was-validated");
  };

  StandardEditModal.prototype.displayErrors = function (errors) {
    var self = this;
    if (errors.form) {
      Object.keys(errors.form).forEach(function (field) {
        var input = document.getElementById("modal_" + field);
        if (!input) return;
        input.classList.add("is-invalid");
        var fb = input.nextElementSibling;
        if (fb && fb.classList.contains("invalid-feedback")) {
          fb.textContent = (errors.form[field] || []).join(", ");
        }
      });
    }
    if (errors.formset) {
      errors.formset.forEach(function (errSet) {
        var row = self.parametersContainer.querySelector(
          '[data-index="' + errSet.index + '"]',
        );
        if (!row) return;
        Object.keys(errSet.errors).forEach(function (field) {
          var input = row.querySelector('[name*="-' + field + '"]');
          if (!input) return;
          input.classList.add("is-invalid");
          var col = input.closest(".col-md-5") || input.closest(".col-md-4");
          if (col) {
            var fb = col.querySelector(".invalid-feedback");
            if (fb) fb.textContent = (errSet.errors[field] || []).join(", ");
          }
        });
      });
    }
  };

  StandardEditModal.prototype.showAlert = function (message, type, autoHide) {
    var alerts = document.getElementById("modal-alerts");
    var div = document.createElement("div");
    div.className = "alert alert-" + type + " alert-dismissible fade show";
    var icon = type === "success" ? "check-circle" : "exclamation-circle";
    div.innerHTML =
      '<i class="fas fa-' +
      icon +
      ' me-2"></i>' +
      message +
      '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
    alerts.appendChild(div);
    if (autoHide)
      setTimeout(function () {
        div.remove();
      }, autoHide);
  };

  // ----------------------------------------------------------------
  // Parameter edit modal
  // ----------------------------------------------------------------

  function ParameterEditManager() {
    this.init();
  }

  ParameterEditManager.prototype.init = function () {
    var self = this;
    var form = document.getElementById("parameterEditForm");
    if (form)
      form.addEventListener("submit", function (e) {
        self.handleSubmit(e);
      });
  };

  ParameterEditManager.prototype.handleSubmit = function (e) {
    e.preventDefault();
    var self = this;
    var form = e.target;
    var submitBtn = document.getElementById("paramEditSubmitBtn");
    var icon = submitBtn ? submitBtn.querySelector("i") : null;

    if (submitBtn) {
      submitBtn.disabled = true;
      if (icon) icon.className = "fas fa-spinner fa-spin me-2";
    }

    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (data.success) {
          self._updateParameterRow(data.parameter);
          showGlobalAlert(
            'Parameter "' + data.parameter.name + '" updated successfully.',
            "success",
          );
          bootstrap.Modal.getInstance(
            document.getElementById("parameterEditModal"),
          ).hide();
        } else {
          showGlobalAlert(
            "Error: " + (data.errors || "Failed to update parameter."),
            "danger",
          );
        }
      })
      .catch(function (err) {
        console.error("ParameterEditManager.handleSubmit:", err);
        showGlobalAlert(
          "Failed to update parameter. Please try again.",
          "danger",
        );
      })
      .finally(function () {
        if (submitBtn) {
          submitBtn.disabled = false;
          if (icon) icon.className = "fas fa-save me-2";
        }
      });
  };

  ParameterEditManager.prototype._updateParameterRow = function (p) {
    var row = document.querySelector('[data-parameter-id="' + p.id + '"]');
    if (!row) return;
    row.innerHTML =
      "<td>" +
      (p.name || "") +
      "</td>" +
      "<td>" +
      (p.symbol || "—") +
      "</td>" +
      "<td>" +
      (p.unit || "") +
      "</td>" +
      "<td>" +
      actionRow("parameter", p.id) +
      "</td>";
    flashRow(row);
  };

  // ----------------------------------------------------------------
  // Delete manager
  // ----------------------------------------------------------------

  function DeleteManager() {
    this.ctx = { type: null, id: null };
    this.init();
  }

  DeleteManager.prototype.init = function () {
    var self = this;

    document.addEventListener("click", function (e) {
      var btn = e.target.closest(
        '[data-action="delete-standard"],[data-action="delete-parameter"]',
      );
      if (!btn) return;
      var type = btn.dataset.action.includes("standard")
        ? "standard"
        : "parameter";
      self.ctx = { type: type, id: btn.dataset.id };
      document.getElementById("delete-item-type").textContent = type;
      new bootstrap.Modal(document.getElementById("deleteConfirmModal")).show();
    });

    var confirmBtn = document.getElementById("confirmDeleteBtn");
    if (confirmBtn)
      confirmBtn.addEventListener("click", function () {
        self.deleteItem();
      });
  };

  DeleteManager.prototype.deleteItem = function () {
    var self = this;
    var type = this.ctx.type;
    var id = this.ctx.id;
    var modal = bootstrap.Modal.getInstance(
      document.getElementById("deleteConfirmModal"),
    );
    var btn = document.getElementById("confirmDeleteBtn");
    var icon = btn.querySelector(".fa-trash");

    btn.disabled = true;
    if (icon) icon.className = "fas fa-spinner fa-spin me-2";

    var url =
      type === "standard"
        ? "/calibration/standards/" + id + "/delete/"
        : "/calibration/parameters/" + id + "/delete/";

    fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCsrf(),
        "X-Requested-With": "XMLHttpRequest",
      },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (data.success) {
          var selector =
            type === "standard"
              ? '[data-standard-id="' + id + '"]'
              : '[data-parameter-id="' + id + '"]';
          var row = document.querySelector(selector);
          if (row) {
            row.style.transition = "opacity 0.3s ease";
            row.style.opacity = "0";
            setTimeout(function () {
              row.remove();
            }, 300);
          }
          showGlobalAlert(
            type.charAt(0).toUpperCase() +
              type.slice(1) +
              " deleted successfully.",
            "success",
          );
        } else {
          showGlobalAlert(
            "Error: " + (data.error || "Failed to delete."),
            "danger",
          );
        }
      })
      .catch(function (err) {
        console.error("DeleteManager.deleteItem:", err);
        showGlobalAlert("Failed to delete " + type + ".", "danger");
      })
      .finally(function () {
        btn.disabled = false;
        if (icon) icon.className = "fas fa-trash me-2";
        modal.hide();
      });
  };

  // ----------------------------------------------------------------
  // Open parameter edit modal
  // ----------------------------------------------------------------

  function openParameterEditModal(parameterId) {
    fetch("/calibration/parameters/" + parameterId + "/edit/", {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        document.getElementById("param_name").value = data.name;
        document.getElementById("param_symbol").value = data.symbol || "";
        document.getElementById("param_unit").value = data.unit;
        document.getElementById("parameterEditForm").action =
          "/calibration/parameters/" + parameterId + "/edit/";
        new bootstrap.Modal(
          document.getElementById("parameterEditModal"),
        ).show();
      })
      .catch(function (err) {
        console.error("openParameterEditModal:", err);
        showGlobalAlert("Failed to load parameter data.", "danger");
      });
  }

  // ----------------------------------------------------------------
  // Boot
  // ----------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    loadTables();

    var standardEditModal = new StandardEditModal();
    var parameterEditManager = new ParameterEditManager();
    var deleteManager = new DeleteManager();

    document.addEventListener("click", function (e) {
      var editStdBtn = e.target.closest('[data-action="edit-standard"]');
      var editParamBtn = e.target.closest('[data-action="edit-parameter"]');

      if (editStdBtn) standardEditModal.open(editStdBtn.dataset.id);
      if (editParamBtn) openParameterEditModal(editParamBtn.dataset.id);
    });
  });
})();
