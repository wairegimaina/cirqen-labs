(function () {
  "use strict";

  // State management
  let currentSessionId = null;
  let currentSessionData = null;

  // Utility: Get CSRF token
  // Django renders {% csrf_token %} as a hidden <input name="csrfmiddlewaretoken">.
  // The old code looked for <meta name="csrf-token"> which doesn't exist by default,
  // so every POST returned 403 Forbidden and appeared to silently do nothing.
  function getCSRFToken() {
    const input = document.querySelector("[name=csrfmiddlewaretoken]");
    if (input) return input.value;
    // Cookie fallback (requires CSRF_COOKIE_HTTPONLY = False in settings, Django default)
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  // Utility: Show toast notification
  function showToast(message, type = "success") {
    const toastContainer = document.getElementById("toastContainer");
    const toastId = "toast-" + Date.now();

    const toastHTML = `
            <div id="${toastId}" class="toast ${type}" role="alert" aria-live="assertive" aria-atomic="true">
                <div class="toast-header">
                    <i class="fas fa-${type === "success" ? "check-circle" : "exclamation-circle"} me-2"></i>
                    <strong class="me-auto">${type === "success" ? "Success" : "Error"}</strong>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
                </div>
                <div class="toast-body">
                    ${message}
                </div>
            </div>
        `;

    toastContainer.insertAdjacentHTML("beforeend", toastHTML);

    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, { delay: 5000 });
    toast.show();

    toastElement.addEventListener("hidden.bs.toast", () => {
      toastElement.remove();
    });
  }

  // API: Fetch session details
  async function fetchSessionDetails(sessionId) {
    try {
      const response = await fetch(
        `/calibration/sessions/${sessionId}/details/`,
        {
          headers: {
            Accept: "application/json",
          },
        },
      );
      if (!response.ok) {
        throw new Error("Failed to fetch session details");
      }
      return await response.json();
    } catch (error) {
      console.error("Error fetching session details:", error);
      throw error;
    }
  }

  // API: Approve session
  async function approveSession(sessionId, formData) {
    try {
      const response = await fetch(
        `/calibration/sessions/${sessionId}/approve/`,
        {
          method: "POST",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            Accept: "application/json",
          },
          body: formData,
        },
      );
      if (!response.ok) {
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
          const errorData = await response.json();
          throw new Error(errorData.error || "Failed to approve session");
        }
        throw new Error(
          `Server error ${response.status}: Failed to approve session`,
        );
      }
      return await response.json();
    } catch (error) {
      console.error("Error approving session:", error);
      throw error;
    }
  }

  // API: Reject session
  async function rejectSession(sessionId, formData) {
    try {
      const response = await fetch(
        `/calibration/sessions/${sessionId}/reject/`,
        {
          method: "POST",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            Accept: "application/json",
          },
          body: formData,
        },
      );
      if (!response.ok) {
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
          const errorData = await response.json();
          throw new Error(errorData.error || "Failed to reject session");
        }
        throw new Error(
          `Server error ${response.status}: Failed to reject session`,
        );
      }
      return await response.json();
    } catch (error) {
      console.error("Error rejecting session:", error);
      throw error;
    }
  }

  // API: Restore session
  async function restoreSession(sessionId, event) {
    if (
      !confirm(
        "Are you sure you want to restore this session for re-calibration?",
      )
    ) {
      return;
    }

    const restoreButton = event.target.closest(
      '[data-action="restore-session"]',
    );
    const originalHTML = restoreButton.innerHTML;
    restoreButton.disabled = true;
    restoreButton.innerHTML =
      '<span class="spinner-border spinner-border-sm"></span>';

    try {
      const response = await fetch(
        `/calibration/sessions/${sessionId}/restore/`,
        {
          method: "POST",
          headers: {
            "X-CSRFToken": getCSRFToken(),
            Accept: "application/json",
          },
        },
      );
      if (!response.ok) {
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
          const errorData = await response.json();
          throw new Error(errorData.error || "Failed to restore session");
        }
        throw new Error(
          `Server error ${response.status}: Failed to restore session`,
        );
      }
      const result = await response.json();
      if (result.success) {
        showToast(result.message, "success");
        setTimeout(() => location.reload(), 1000);
      } else {
        throw new Error(result.error);
      }
    } catch (error) {
      showToast("Error restoring session: " + error.message, "error");
      restoreButton.disabled = false;
      restoreButton.innerHTML = originalHTML;
    }
  }

  // API: Download declined certificate
  async function downloadDeclinedCertificate(sessionId) {
    window.location.href = `/calibration/sessions/${sessionId}/declined-certificate/`;
  }

  // UI: Build session details HTML
  function buildSessionDetailsHTML(data) {
    const safeValue = (val) => val || "N/A";
    const safeNestedValue = (obj, key) => (obj && obj[key] ? obj[key] : "N/A");

    let html =
      '<h6 class="border-bottom pb-2 mb-3"><i class="fas fa-info-circle text-info me-2"></i>Session Information</h6>' +
      '<div class="row mb-4">' +
      '<div class="col-md-6">' +
      '<table class="table table-sm table-bordered">' +
      '<tr><th width="40%">Procedure</th><td>' +
      safeValue(data.procedure) +
      "</td></tr>" +
      "<tr><th>Performed By</th><td>" +
      safeValue(data.performed_by) +
      "</td></tr>" +
      "<tr><th>Date</th><td>" +
      safeValue(data.timestamp) +
      "</td></tr>" +
      (data.approved_by
        ? "<tr><th>Approved By</th><td>" +
          '<i class="fas fa-user-check text-success me-1"></i>' +
          safeValue(data.approved_by) +
          "</td></tr>" +
          "<tr><th>Approved At</th><td>" +
          safeValue(data.approved_at) +
          "</td></tr>"
        : "") +
      "</table>" +
      "</div>" +
      '<div class="col-md-6">' +
      '<table class="table table-sm table-bordered">' +
      '<tr><th width="40%">Status</th><td><span class="badge bg-warning">' +
      safeValue(data.status) +
      "</span></td></tr>" +
      "<tr><th>Overall Result</th><td>" +
      (data.overall_pass
        ? '<span class="badge bg-success">PASS</span>'
        : '<span class="badge bg-danger">FAIL</span>') +
      "</td></tr>" +
      "<tr><th>Priority</th><td>" +
      safeValue(data.priority) +
      "</td></tr>" +
      "</table>" +
      "</div>" +
      "</div>" +
      '<h6 class="border-bottom pb-2 mb-3"><i class="fas fa-cog text-primary me-2"></i>Device Information</h6>' +
      '<table class="table table-sm table-bordered mb-4">' +
      '<tr><th width="20%">Description</th><td>' +
      safeNestedValue(data.device, "description") +
      "</td></tr>" +
      "<tr><th>Model</th><td>" +
      safeNestedValue(data.device, "model") +
      "</td></tr>" +
      '<tr><th>Serial Number</th><td><span class="badge bg-secondary">' +
      safeNestedValue(data.device, "serial") +
      "</span></td></tr>" +
      "<tr><th>Manufacturer</th><td>" +
      safeNestedValue(data.device, "manufacturer") +
      "</td></tr>" +
      "</table>" +
      '<h6 class="border-bottom pb-2 mb-3"><i class="fas fa-temperature-high text-warning me-2"></i>Environmental Conditions</h6>' +
      '<div class="row mb-4">' +
      '<div class="col-md-4">' +
      '<div class="card text-center">' +
      '<div class="card-body">' +
      '<i class="fas fa-thermometer-half fa-2x text-danger mb-2"></i>' +
      "<h5>" +
      safeNestedValue(data.environment, "temperature") +
      " °C</h5>" +
      '<p class="text-muted mb-0">Temperature</p>' +
      "</div>" +
      "</div>" +
      "</div>" +
      '<div class="col-md-4">' +
      '<div class="card text-center">' +
      '<div class="card-body">' +
      '<i class="fas fa-tint fa-2x text-info mb-2"></i>' +
      "<h5>" +
      safeNestedValue(data.environment, "humidity") +
      " %</h5>" +
      '<p class="text-muted mb-0">Humidity</p>' +
      "</div>" +
      "</div>" +
      "</div>" +
      '<div class="col-md-4">' +
      '<div class="card text-center">' +
      '<div class="card-body">' +
      '<i class="fas fa-wind fa-2x text-primary mb-2"></i>' +
      "<h5>" +
      safeNestedValue(data.environment, "pressure") +
      " kPa</h5>" +
      '<p class="text-muted mb-0">Pressure</p>' +
      "</div>" +
      "</div>" +
      "</div>" +
      "</div>";

    if (data.readings && data.readings.length > 0) {
      html +=
        '<h6 class="border-bottom pb-2 mb-3"><i class="fas fa-chart-line text-success me-2"></i>Calibration Results</h6>' +
        '<div class="table-responsive">' +
        '<table class="table table-striped table-sm table-hover">' +
        '<thead class="table-light">' +
        "<tr>" +
        "<th>Parameter</th>" +
        "<th>Sub-Parameter</th>" +
        "<th>Set Value</th>" +
        "<th>Readings</th>" +
        "<th>Mean</th>" +
        "<th>Std Dev</th>" +
        "<th>Error</th>" +
        "<th>Expanded Unc.</th>" +
        "<th>Status</th>" +
        "</tr>" +
        "</thead>" +
        "<tbody>";

      data.readings.forEach(function (reading) {
        html +=
          "<tr>" +
          "<td><strong>" +
          safeValue(reading.parameter) +
          "</strong></td>" +
          "<td>" +
          safeValue(reading.sub_parameter) +
          "</td>" +
          '<td><span class="badge bg-info">' +
          safeValue(reading.set_value) +
          " " +
          safeValue(reading.unit) +
          "</span></td>" +
          "<td><small>" +
          (reading.readings ? reading.readings.join(", ") : "N/A") +
          "</small></td>" +
          "<td>" +
          (reading.mean != null ? reading.mean.toFixed(3) : "N/A") +
          "</td>" +
          "<td>" +
          (reading.std_dev != null ? reading.std_dev.toFixed(4) : "N/A") +
          "</td>" +
          "<td>" +
          (reading.error != null ? reading.error.toFixed(3) : "N/A") +
          "</td>" +
          "<td>" +
          (reading.expanded_unc != null
            ? reading.expanded_unc.toFixed(4)
            : "N/A") +
          "</td>" +
          "<td>" +
          (reading.passes_tolerance
            ? '<span class="badge bg-success"><i class="fas fa-check"></i> PASS</span>'
            : '<span class="badge bg-danger"><i class="fas fa-times"></i> FAIL</span>') +
          "</td>" +
          "</tr>";
      });

      html += "</tbody></table></div>";
    }

    return html;
  }

  // UI: Build success modal content
  function buildSuccessModalContent(sessionData, certificateNumber) {
    const safeValue = (val) => val || "N/A";

    return `
            <div class="info-row">
                <span class="info-label"><i class="fas fa-certificate me-2"></i>Certificate Number:</span>
                <span class="info-value"><strong>${certificateNumber || "Pending"}</strong></span>
            </div>
            <div class="info-row">
                <span class="info-label"><i class="fas fa-hashtag me-2"></i>Session ID:</span>
                <span class="info-value">${currentSessionId}</span>
            </div>
            <div class="info-row">
                <span class="info-label"><i class="fas fa-cog me-2"></i>Equipment:</span>
                <span class="info-value">${safeValue(sessionData.device?.description)}</span>
            </div>
            <div class="info-row">
                <span class="info-label"><i class="fas fa-barcode me-2"></i>Serial Number:</span>
                <span class="info-value">${safeValue(sessionData.device?.serial)}</span>
            </div>
            <div class="info-row">
                <span class="info-label"><i class="fas fa-user me-2"></i>Performed By:</span>
                <span class="info-value">${safeValue(sessionData.performed_by)}</span>
            </div>
            <div class="info-row">
                <span class="info-label"><i class="fas fa-calendar me-2"></i>Date:</span>
                <span class="info-value">${safeValue(sessionData.timestamp)}</span>
            </div>
        `;
  }

  // Handler: Review session
  async function handleReviewSession(sessionId) {
    currentSessionId = sessionId;

    const reviewBody = document.getElementById("reviewSessionBody");
    reviewBody.innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="text-muted mt-2">Loading session details...</p>
            </div>
        `;

    const reviewModal = bootstrap.Modal.getOrCreateInstance(
      document.getElementById("reviewModal"),
    );
    reviewModal.show();

    try {
      const data = await fetchSessionDetails(sessionId);
      currentSessionData = data;
      reviewBody.innerHTML = buildSessionDetailsHTML(data);

      // Hide Approve / Reject buttons for already-approved sessions
      const isAwaitingCert = data.status === "approved_pending_certificate";
      const reviewModalEl = document.getElementById("reviewModal");
      const approveBtn = reviewModalEl.querySelector(
        '[data-action="show-approval-modal"]',
      );
      const rejectBtn = reviewModalEl.querySelector(
        '[data-action="show-rejection-modal"]',
      );
      if (approveBtn) approveBtn.style.display = isAwaitingCert ? "none" : "";
      if (rejectBtn) rejectBtn.style.display = isAwaitingCert ? "none" : "";
    } catch (error) {
      reviewBody.innerHTML =
        '<div class="alert alert-danger"><i class="fas fa-exclamation-triangle me-2"></i>Error loading session details. Please try again.</div>';
      showToast("Failed to load session details", "error");
    }
  }

  // Handler: Show approval modal
  function handleShowApprovalModal() {
    if (!currentSessionId) {
      showToast("No session selected. Please review a session first.", "error");
      return;
    }

    const reviewModalEl = document.getElementById("reviewModal");
    const approvalModalEl = document.getElementById("approvalModal");

    const reviewModal = bootstrap.Modal.getInstance(reviewModalEl);
    if (reviewModal) {
      // Wait for review modal to fully close before opening approval modal,
      // otherwise Bootstrap ignores the second show() call.
      reviewModalEl.addEventListener("hidden.bs.modal", function onHidden() {
        reviewModalEl.removeEventListener("hidden.bs.modal", onHidden);
        bootstrap.Modal.getOrCreateInstance(approvalModalEl).show();
      });
      reviewModal.hide();
    } else {
      bootstrap.Modal.getOrCreateInstance(approvalModalEl).show();
    }
  }

  // Handler: Show rejection modal
  function handleShowRejectionModal() {
    if (!currentSessionId) {
      showToast("No session selected. Please review a session first.", "error");
      return;
    }

    const reviewModalEl = document.getElementById("reviewModal");
    const rejectionModalEl = document.getElementById("rejectionModal");

    const reviewModal = bootstrap.Modal.getInstance(reviewModalEl);
    if (reviewModal) {
      reviewModalEl.addEventListener("hidden.bs.modal", function onHidden() {
        reviewModalEl.removeEventListener("hidden.bs.modal", onHidden);
        bootstrap.Modal.getOrCreateInstance(rejectionModalEl).show();
      });
      reviewModal.hide();
    } else {
      bootstrap.Modal.getOrCreateInstance(rejectionModalEl).show();
    }
  }

  // Handler: Confirm approval
  async function handleConfirmApproval() {
    if (!currentSessionId) {
      showToast("No session selected", "error");
      return;
    }

    const approvalModalEl = document.getElementById("approvalModal");
    if (!approvalModalEl) {
      console.error("approvalModal element not found");
      return;
    }

    // Scope ALL lookups to the modal element — avoids any duplicate-ID ambiguity
    // caused by base templates or Django includes rendering a second copy.
    const confirmButton = approvalModalEl.querySelector(
      '[data-action="confirm-approval"]',
    );
    if (!confirmButton) {
      showToast(
        "Approval button not found — please close and reopen the modal.",
        "error",
      );
      return;
    }
    const originalButtonText = confirmButton.innerHTML;
    confirmButton.disabled = true;
    confirmButton.innerHTML =
      '<span class="spinner-border spinner-border-sm me-2"></span>Approving...';

    const form = approvalModalEl.querySelector("#approvalForm, form");
    if (!form) {
      console.error("approvalForm not found inside approvalModal");
      confirmButton.disabled = false;
      confirmButton.innerHTML = originalButtonText;
      return;
    }
    const formData = new FormData(form);

    try {
      const result = await approveSession(currentSessionId, formData);

      if (result.success) {
        const approvalModalEl = document.getElementById("approvalModal");
        const successModalEl = document.getElementById("successModal");

        document.getElementById("successSessionInfo").innerHTML =
          buildSuccessModalContent(
            currentSessionData,
            result.certificate_number,
          );

        const approvalModal = bootstrap.Modal.getInstance(approvalModalEl);
        if (approvalModal) {
          approvalModalEl.addEventListener(
            "hidden.bs.modal",
            function onHidden() {
              approvalModalEl.removeEventListener("hidden.bs.modal", onHidden);
              bootstrap.Modal.getOrCreateInstance(successModalEl).show();
            },
          );
          approvalModal.hide();
        } else {
          bootstrap.Modal.getOrCreateInstance(successModalEl).show();
        }
      } else {
        throw new Error(result.error || "Unknown error occurred");
      }
    } catch (error) {
      showToast(`Error approving session: ${error.message}`, "error");
      confirmButton.disabled = false;
      confirmButton.innerHTML = originalButtonText;
    }
  }

  // Handler: Confirm rejection
  async function handleConfirmRejection() {
    if (!currentSessionId) {
      showToast("No session selected", "error");
      return;
    }

    const form = document.getElementById("rejectionForm");
    if (!form) {
      console.error("rejectionForm not found in DOM");
      return;
    }
    const formData = new FormData(form);

    const rejectionReason = formData.get("rejection_reason");
    const rejectionComments = formData.get("rejection_comments");

    if (!rejectionReason || !rejectionComments?.trim()) {
      showToast(
        "Please provide both a reason and detailed comments for rejection",
        "error",
      );
      return;
    }

    const rejectionModalEl = document.getElementById("rejectionModal");
    const confirmButton = rejectionModalEl.querySelector(
      '[data-action="confirm-rejection"]',
    );
    if (!confirmButton) {
      console.error("confirm-rejection button not found in DOM");
      return;
    }
    const originalButtonText = confirmButton.innerHTML;
    confirmButton.disabled = true;
    confirmButton.innerHTML =
      '<span class="spinner-border spinner-border-sm me-2"></span>Rejecting...';

    try {
      const result = await rejectSession(currentSessionId, formData);

      if (result.success) {
        const rejectionModal = bootstrap.Modal.getInstance(
          document.getElementById("rejectionModal"),
        );
        if (rejectionModal) {
          rejectionModal.hide();
        }

        showToast(
          "Session rejected successfully. The technician will be notified.",
          "success",
        );

        setTimeout(() => {
          location.reload();
        }, 2000);
      } else {
        throw new Error(result.error || "Unknown error occurred");
      }
    } catch (error) {
      showToast(`Error rejecting session: ${error.message}`, "error");
      confirmButton.disabled = false;
      confirmButton.innerHTML = originalButtonText;
    }
  }

  // Handler: Close success modal and reload
  function handleCloseSuccessModal() {
    const successModal = bootstrap.Modal.getInstance(
      document.getElementById("successModal"),
    );
    if (successModal) {
      successModal.hide();
    }
    location.reload();
  }

  // Handler: Refresh table
  function handleRefreshTable() {
    location.reload();
  }

  // Handler: Switch tab via AJAX
  async function handleSwitchTab(tab) {
    // Update active state on nav links
    document.querySelectorAll(".nav-tabs .nav-link").forEach((link) => {
      link.classList.toggle("active", link.dataset.tab === tab);
    });

    // Update filter form hidden input
    const tabInput = document.querySelector('#filterForm input[name="tab"]');
    if (tabInput) tabInput.value = tab;

    // Update table header title
    const headerTitle = document.getElementById("tableHeaderTitle");
    if (headerTitle) {
      const titles = {
        pending: '<i class="fas fa-list"></i> Pending Sessions',
        declined: '<i class="fas fa-times-circle"></i> Declined Sessions',
        awaiting_certificate:
          '<i class="fas fa-certificate"></i> Awaiting Certificate',
      };
      headerTitle.innerHTML = titles[tab] || titles.pending;
    }

    // Show loading spinner in table container
    const tableContainer = document.getElementById("sessionsTableContainer");
    if (tableContainer) {
      tableContainer.innerHTML = `
        <div class="text-center py-5">
          <div class="spinner-border text-primary" role="status">
            <span class="visually-hidden">Loading...</span>
          </div>
          <p class="text-muted mt-2">Loading sessions...</p>
        </div>`;
    }

    // Build URL preserving existing filters, update tab
    const params = new URLSearchParams(window.location.search);
    params.set("tab", tab);
    params.delete("page"); // reset to page 1 on tab switch

    // Update browser URL without reload
    const newUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.pushState({ tab }, "", newUrl);

    try {
      const response = await fetch(newUrl, {
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
        },
      });
      if (!response.ok) throw new Error(`Server error ${response.status}`);
      const data = await response.json();

      if (tableContainer) tableContainer.innerHTML = data.html;
      updateCounts(data);
    } catch (error) {
      showToast("Failed to load tab: " + error.message, "error");
      if (tableContainer) {
        tableContainer.innerHTML =
          '<div class="alert alert-danger m-3"><i class="fas fa-exclamation-triangle me-2"></i>Failed to load sessions. Please try again.</div>';
      }
    }
  }

  // Helper: Update all count badges and stat cards from AJAX response
  function updateCounts(data) {
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el && val !== undefined) el.textContent = val;
    };
    set("pendingCount", data.pending_count);
    set("declinedCount", data.declined_count);
    set("awaitingCertCount", data.awaiting_cert_count);
    set("summaryPending", data.pending_count);
    set("summaryApproved", data.approved_count);
    set("summaryHighPriority", data.high_priority_count);
    set("summaryCanReview", data.can_review_count);
    set("summaryAwaitingCert", data.awaiting_cert_count);
  }

  // Event delegation for all actions
  function handleActionClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target) return;

    const action = target.dataset.action;
    const sessionId = target.dataset.sessionId;

    event.preventDefault();

    switch (action) {
      case "switch-tab":
        if (target.dataset.tab) {
          handleSwitchTab(target.dataset.tab);
        }
        break;
      case "review-session":
        if (sessionId) {
          handleReviewSession(sessionId);
        }
        break;
      case "show-approval-modal":
        handleShowApprovalModal();
        break;
      case "show-rejection-modal":
        handleShowRejectionModal();
        break;
      case "confirm-approval":
        handleConfirmApproval();
        break;
      case "confirm-rejection":
        handleConfirmRejection();
        break;
      case "close-success-modal":
        handleCloseSuccessModal();
        break;
      case "refresh-table":
        handleRefreshTable();
        break;
      case "download-declined-certificate":
        downloadDeclinedCertificate(sessionId);
        break;
      case "restore-session":
        restoreSession(sessionId, event);
        break;
      default:
        console.warn("Unknown action:", action);
    }
  }

  // Initialize event listeners
  function init() {
    document.addEventListener("click", handleActionClick);

    // Handle browser back/forward navigation
    window.addEventListener("popstate", (event) => {
      const params = new URLSearchParams(window.location.search);
      const tab = params.get("tab") || "pending";
      handleSwitchTab(tab);
    });

    const approvalModal = document.getElementById("approvalModal");
    if (approvalModal) {
      approvalModal.addEventListener("hidden.bs.modal", () => {
        const f = approvalModal.querySelector("form");
        if (f) f.reset();
      });
    }

    const rejectionModal = document.getElementById("rejectionModal");
    if (rejectionModal) {
      rejectionModal.addEventListener("hidden.bs.modal", () => {
        const f = rejectionModal.querySelector("form");
        if (f) f.reset();
      });
    }

    const reviewModal = document.getElementById("reviewModal");
    if (reviewModal) {
      reviewModal.addEventListener("hidden.bs.modal", () => {
        // Delay the check to allow the next modal (approval/rejection) to begin
        // its show() transition before we decide whether to clear state.
        // Bootstrap modal transitions are ~300ms; 500ms gives comfortable margin.
        setTimeout(() => {
          const openOrTransitioningModals = document.querySelectorAll(
            ".modal.show, .modal.fade:not(.d-none)",
          );
          if (openOrTransitioningModals.length === 0) {
            currentSessionId = null;
            currentSessionData = null;
          }
        }, 500);
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
