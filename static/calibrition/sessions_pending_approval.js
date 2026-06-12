(function () {
  'use strict';

  // State management
  let currentSessionId = null;
  let currentSessionData = null;

  // Utility: Get CSRF token
  function getCSRFToken() {
    const token = document.querySelector('meta[name="csrf-token"]');
    return token ? token.getAttribute('content') : '';
  }

  // Utility: Show toast notification
  function showToast(message, type = 'success') {
    const toastContainer = document.getElementById('toastContainer');
    const toastId = 'toast-' + Date.now();

    const toastHTML = `
            <div id="${toastId}" class="toast ${type}" role="alert" aria-live="assertive" aria-atomic="true">
                <div class="toast-header">
                    <i class="fas fa-${type === 'success' ? 'check-circle' : 'exclamation-circle'} me-2"></i>
                    <strong class="me-auto">${type === 'success' ? 'Success' : 'Error'}</strong>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
                </div>
                <div class="toast-body">
                    ${message}
                </div>
            </div>
        `;

    toastContainer.insertAdjacentHTML('beforeend', toastHTML);

    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, { delay: 5000 });
    toast.show();

    toastElement.addEventListener('hidden.bs.toast', () => {
      toastElement.remove();
    });
  }

  // API: Fetch session details
  async function fetchSessionDetails(sessionId) {
    try {
      const response = await fetch(`/calibration/sessions/${sessionId}/details/`, {
        headers: {
          Accept: 'application/json',
        },
      });
      if (!response.ok) {
        throw new Error('Failed to fetch session details');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching session details:', error);
      throw error;
    }
  }

  // API: Approve session
  async function approveSession(sessionId, formData) {
    try {
      const response = await fetch(`/calibration/sessions/${sessionId}/approve/`, {
        method: 'POST',
        headers: {
          'X-CSRFToken': getCSRFToken(),
          Accept: 'application/json',
        },
        body: formData,
      });
      if (!response.ok) {
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          const errorData = await response.json();
          throw new Error(errorData.error || 'Failed to approve session');
        }
        throw new Error(`Server error ${response.status}: Failed to approve session`);
      }
      return await response.json();
    } catch (error) {
      console.error('Error approving session:', error);
      throw error;
    }
  }

  // API: Reject session
  async function rejectSession(sessionId, formData) {
    try {
      const response = await fetch(`/calibration/sessions/${sessionId}/reject/`, {
        method: 'POST',
        headers: {
          'X-CSRFToken': getCSRFToken(),
          Accept: 'application/json',
        },
        body: formData,
      });
      if (!response.ok) {
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          const errorData = await response.json();
          throw new Error(errorData.error || 'Failed to reject session');
        }
        throw new Error(`Server error ${response.status}: Failed to reject session`);
      }
      return await response.json();
    } catch (error) {
      console.error('Error rejecting session:', error);
      throw error;
    }
  }

  // API: Restore session
  async function restoreSession(sessionId) {
    if (!confirm('Are you sure you want to restore this session for re-calibration?')) {
      return;
    }

    const restoreButton = event.target.closest('[data-action="restore-session"]');
    const originalHTML = restoreButton.innerHTML;
    restoreButton.disabled = true;
    restoreButton.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

    try {
      const response = await fetch(`/calibration/sessions/${sessionId}/restore/`, {
        method: 'POST',
        headers: {
          'X-CSRFToken': getCSRFToken(),
          Accept: 'application/json',
        },
      });
      if (!response.ok) {
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          const errorData = await response.json();
          throw new Error(errorData.error || 'Failed to restore session');
        }
        throw new Error(`Server error ${response.status}: Failed to restore session`);
      }
      const result = await response.json();
      if (result.success) {
        showToast(result.message, 'success');
        setTimeout(() => location.reload(), 1000);
      } else {
        throw new Error(result.error);
      }
    } catch (error) {
      showToast('Error restoring session: ' + error.message, 'error');
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
    const safeValue = (val) => val || 'N/A';
    const safeNestedValue = (obj, key) => (obj && obj[key] ? obj[key] : 'N/A');

    let html =
      '<h6 class="border-bottom pb-2 mb-3"><i class="fas fa-info-circle text-info me-2"></i>Session Information</h6>' +
      '<div class="row mb-4">' +
      '<div class="col-md-6">' +
      '<table class="table table-sm table-bordered">' +
      '<tr><th width="40%">Procedure</th><td>' +
      safeValue(data.procedure) +
      '</td></tr>' +
      '<tr><th>Performed By</th><td>' +
      safeValue(data.performed_by) +
      '</td></tr>' +
      '<tr><th>Date</th><td>' +
      safeValue(data.timestamp) +
      '</td></tr>' +
      '</table>' +
      '</div>' +
      '<div class="col-md-6">' +
      '<table class="table table-sm table-bordered">' +
      '<tr><th width="40%">Status</th><td><span class="badge bg-warning">' +
      safeValue(data.status) +
      '</span></td></tr>' +
      '<tr><th>Overall Result</th><td>' +
      (data.overall_pass
        ? '<span class="badge bg-success">PASS</span>'
        : '<span class="badge bg-danger">FAIL</span>') +
      '</td></tr>' +
      '<tr><th>Priority</th><td>' +
      safeValue(data.priority) +
      '</td></tr>' +
      '</table>' +
      '</div>' +
      '</div>' +
      '<h6 class="border-bottom pb-2 mb-3"><i class="fas fa-cog text-primary me-2"></i>Device Information</h6>' +
      '<table class="table table-sm table-bordered mb-4">' +
      '<tr><th width="20%">Description</th><td>' +
      safeNestedValue(data.device, 'description') +
      '</td></tr>' +
      '<tr><th>Model</th><td>' +
      safeNestedValue(data.device, 'model') +
      '</td></tr>' +
      '<tr><th>Serial Number</th><td><span class="badge bg-secondary">' +
      safeNestedValue(data.device, 'serial') +
      '</span></td></tr>' +
      '<tr><th>Manufacturer</th><td>' +
      safeNestedValue(data.device, 'manufacturer') +
      '</td></tr>' +
      '</table>' +
      '<h6 class="border-bottom pb-2 mb-3"><i class="fas fa-temperature-high text-warning me-2"></i>Environmental Conditions</h6>' +
      '<div class="row mb-4">' +
      '<div class="col-md-4">' +
      '<div class="card text-center">' +
      '<div class="card-body">' +
      '<i class="fas fa-thermometer-half fa-2x text-danger mb-2"></i>' +
      '<h5>' +
      safeNestedValue(data.environment, 'temperature') +
      ' °C</h5>' +
      '<p class="text-muted mb-0">Temperature</p>' +
      '</div>' +
      '</div>' +
      '</div>' +
      '<div class="col-md-4">' +
      '<div class="card text-center">' +
      '<div class="card-body">' +
      '<i class="fas fa-tint fa-2x text-info mb-2"></i>' +
      '<h5>' +
      safeNestedValue(data.environment, 'humidity') +
      ' %</h5>' +
      '<p class="text-muted mb-0">Humidity</p>' +
      '</div>' +
      '</div>' +
      '</div>' +
      '<div class="col-md-4">' +
      '<div class="card text-center">' +
      '<div class="card-body">' +
      '<i class="fas fa-wind fa-2x text-primary mb-2"></i>' +
      '<h5>' +
      safeNestedValue(data.environment, 'pressure') +
      ' kPa</h5>' +
      '<p class="text-muted mb-0">Pressure</p>' +
      '</div>' +
      '</div>' +
      '</div>' +
      '</div>';

    if (data.readings && data.readings.length > 0) {
      html +=
        '<h6 class="border-bottom pb-2 mb-3"><i class="fas fa-chart-line text-success me-2"></i>Calibration Results</h6>' +
        '<div class="table-responsive">' +
        '<table class="table table-striped table-sm table-hover">' +
        '<thead class="table-light">' +
        '<tr>' +
        '<th>Parameter</th>' +
        '<th>Sub-Parameter</th>' +
        '<th>Set Value</th>' +
        '<th>Readings</th>' +
        '<th>Mean</th>' +
        '<th>Std Dev</th>' +
        '<th>Error</th>' +
        '<th>Expanded Unc.</th>' +
        '<th>Status</th>' +
        '</tr>' +
        '</thead>' +
        '<tbody>';

      data.readings.forEach(function (reading) {
        html +=
          '<tr>' +
          '<td><strong>' +
          safeValue(reading.parameter) +
          '</strong></td>' +
          '<td>' +
          safeValue(reading.sub_parameter) +
          '</td>' +
          '<td><span class="badge bg-info">' +
          safeValue(reading.set_value) +
          ' ' +
          safeValue(reading.unit) +
          '</span></td>' +
          '<td><small>' +
          (reading.readings ? reading.readings.join(', ') : 'N/A') +
          '</small></td>' +
          '<td>' +
          (reading.mean != null ? reading.mean.toFixed(3) : 'N/A') +
          '</td>' +
          '<td>' +
          (reading.std_dev != null ? reading.std_dev.toFixed(4) : 'N/A') +
          '</td>' +
          '<td>' +
          (reading.error != null ? reading.error.toFixed(3) : 'N/A') +
          '</td>' +
          '<td>' +
          (reading.expanded_unc != null ? reading.expanded_unc.toFixed(4) : 'N/A') +
          '</td>' +
          '<td>' +
          (reading.passes_tolerance
            ? '<span class="badge bg-success"><i class="fas fa-check"></i> PASS</span>'
            : '<span class="badge bg-danger"><i class="fas fa-times"></i> FAIL</span>') +
          '</td>' +
          '</tr>';
      });

      html += '</tbody></table></div>';
    }

    return html;
  }

  // UI: Build success modal content
  function buildSuccessModalContent(sessionData, certificateNumber) {
    const safeValue = (val) => val || 'N/A';

    return `
            <div class="info-row">
                <span class="info-label"><i class="fas fa-certificate me-2"></i>Certificate Number:</span>
                <span class="info-value"><strong>${certificateNumber || 'Pending'}</strong></span>
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

    const reviewBody = document.getElementById('reviewSessionBody');
    reviewBody.innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="text-muted mt-2">Loading session details...</p>
            </div>
        `;

    const reviewModal = new bootstrap.Modal(document.getElementById('reviewModal'));
    reviewModal.show();

    try {
      const data = await fetchSessionDetails(sessionId);
      currentSessionData = data;
      reviewBody.innerHTML = buildSessionDetailsHTML(data);
    } catch (error) {
      reviewBody.innerHTML =
        '<div class="alert alert-danger"><i class="fas fa-exclamation-triangle me-2"></i>Error loading session details. Please try again.</div>';
      showToast('Failed to load session details', 'error');
    }
  }

  // Handler: Show approval modal
  function handleShowApprovalModal() {
    if (!currentSessionId) {
      showToast('No session selected. Please review a session first.', 'error');
      return;
    }

    const reviewModal = bootstrap.Modal.getInstance(document.getElementById('reviewModal'));
    if (reviewModal) {
      reviewModal.hide();
    }

    setTimeout(() => {
      const approvalModal = new bootstrap.Modal(document.getElementById('approvalModal'));
      approvalModal.show();
    }, 300);
  }

  // Handler: Show rejection modal
  function handleShowRejectionModal() {
    if (!currentSessionId) {
      showToast('No session selected. Please review a session first.', 'error');
      return;
    }

    const reviewModal = bootstrap.Modal.getInstance(document.getElementById('reviewModal'));
    if (reviewModal) {
      reviewModal.hide();
    }

    setTimeout(() => {
      const rejectionModal = new bootstrap.Modal(document.getElementById('rejectionModal'));
      rejectionModal.show();
    }, 300);
  }

  // Handler: Confirm approval
  async function handleConfirmApproval() {
    if (!currentSessionId) {
      showToast('No session selected', 'error');
      return;
    }

    const confirmButton = document.querySelector('[data-action="confirm-approval"]');
    const originalButtonText = confirmButton.innerHTML;
    confirmButton.disabled = true;
    confirmButton.innerHTML =
      '<span class="spinner-border spinner-border-sm me-2"></span>Approving...';

    const form = document.getElementById('approvalForm');
    const formData = new FormData(form);

    try {
      const result = await approveSession(currentSessionId, formData);

      if (result.success) {
        const approvalModal = bootstrap.Modal.getInstance(document.getElementById('approvalModal'));
        if (approvalModal) {
          approvalModal.hide();
        }

        const successModalElement = document.getElementById('successModal');
        const successModal = new bootstrap.Modal(successModalElement);

        const successInfoContainer = document.getElementById('successSessionInfo');
        successInfoContainer.innerHTML = buildSuccessModalContent(
          currentSessionData,
          result.certificate_number,
        );

        successModal.show();
      } else {
        throw new Error(result.error || 'Unknown error occurred');
      }
    } catch (error) {
      showToast(`Error approving session: ${error.message}`, 'error');
      confirmButton.disabled = false;
      confirmButton.innerHTML = originalButtonText;
    }
  }

  // Handler: Confirm rejection
  async function handleConfirmRejection() {
    if (!currentSessionId) {
      showToast('No session selected', 'error');
      return;
    }

    const form = document.getElementById('rejectionForm');
    const formData = new FormData(form);

    const rejectionReason = formData.get('rejection_reason');
    const rejectionComments = formData.get('rejection_comments');

    if (!rejectionReason || !rejectionComments?.trim()) {
      showToast('Please provide both a reason and detailed comments for rejection', 'error');
      return;
    }

    const confirmButton = document.querySelector('[data-action="confirm-rejection"]');
    const originalButtonText = confirmButton.innerHTML;
    confirmButton.disabled = true;
    confirmButton.innerHTML =
      '<span class="spinner-border spinner-border-sm me-2"></span>Rejecting...';

    try {
      const result = await rejectSession(currentSessionId, formData);

      if (result.success) {
        const rejectionModal = bootstrap.Modal.getInstance(
          document.getElementById('rejectionModal'),
        );
        if (rejectionModal) {
          rejectionModal.hide();
        }

        showToast('Session rejected successfully. The technician will be notified.', 'success');

        setTimeout(() => {
          location.reload();
        }, 2000);
      } else {
        throw new Error(result.error || 'Unknown error occurred');
      }
    } catch (error) {
      showToast(`Error rejecting session: ${error.message}`, 'error');
      confirmButton.disabled = false;
      confirmButton.innerHTML = originalButtonText;
    }
  }

  // Handler: Close success modal and reload
  function handleCloseSuccessModal() {
    const successModal = bootstrap.Modal.getInstance(document.getElementById('successModal'));
    if (successModal) {
      successModal.hide();
    }
    location.reload();
  }

  // Handler: Refresh table
  function handleRefreshTable() {
    location.reload();
  }

  // Event delegation for all actions
  function handleActionClick(event) {
    const target = event.target.closest('[data-action]');
    if (!target) return;

    const action = target.dataset.action;
    const sessionId = target.dataset.sessionId;

    event.preventDefault();

    switch (action) {
      case 'review-session':
        if (sessionId) {
          handleReviewSession(sessionId);
        }
        break;
      case 'show-approval-modal':
        handleShowApprovalModal();
        break;
      case 'show-rejection-modal':
        handleShowRejectionModal();
        break;
      case 'confirm-approval':
        handleConfirmApproval();
        break;
      case 'confirm-rejection':
        handleConfirmRejection();
        break;
      case 'close-success-modal':
        handleCloseSuccessModal();
        break;
      case 'refresh-table':
        handleRefreshTable();
        break;
      case 'download-declined-certificate':
        downloadDeclinedCertificate(sessionId);
        break;
      case 'restore-session':
        window.event = event;
        restoreSession(sessionId);
        break;
      default:
        console.warn('Unknown action:', action);
    }
  }

  // Initialize event listeners
  function init() {
    document.addEventListener('click', handleActionClick);

    const approvalModal = document.getElementById('approvalModal');
    if (approvalModal) {
      approvalModal.addEventListener('hidden.bs.modal', () => {
        document.getElementById('approvalForm').reset();
      });
    }

    const rejectionModal = document.getElementById('rejectionModal');
    if (rejectionModal) {
      rejectionModal.addEventListener('hidden.bs.modal', () => {
        document.getElementById('rejectionForm').reset();
      });
    }

    const reviewModal = document.getElementById('reviewModal');
    if (reviewModal) {
      reviewModal.addEventListener('hidden.bs.modal', () => {
        setTimeout(() => {
          const openModals = document.querySelectorAll('.modal.show');
          if (openModals.length === 0) {
            currentSessionId = null;
            currentSessionData = null;
          }
        }, 300);
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
