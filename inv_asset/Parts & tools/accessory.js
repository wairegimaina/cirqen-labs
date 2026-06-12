/**
 * Accessories & Tools Management System
 * Refactored JavaScript with proper toggle functionality
 */

class AccessoriesManager {
  constructor() {
    this.init();
  }

  init() {
    this.bindEvents();
    this.initializeComponents();
    this.setupFormValidation();
    this.autoHideAlerts();
  }

  /**
   * Bind all event listeners
   */
  bindEvents() {
    this.bindModalCloseEvents();
    this.bindFormEvents();
    this.bindOutsideClickClose();
    this.bindKeyboardEvents();
  }

  /**
   * Enhanced toggle function with proper state management
   * @param {string} groupId - ID of the input group to show/hide
   * @param {string} selectId - ID of the select element
   * @param {string} btnId - ID of the toggle button
   * @param {string} inputId - ID of the input field
   */
  toggleInput(groupId, selectId, btnId, inputId) {
    const group = document.getElementById(groupId);
    const select = document.getElementById(selectId);
    const btn = document.getElementById(btnId);
    const input = document.getElementById(inputId);

    if (!group || !select || !btn) {
      console.warn(`Missing elements: ${groupId}, ${selectId}, ${btnId}`);
      return;
    }

    const isHidden = group.style.display === 'none' || group.style.display === '';

    if (isHidden) {
      // Show input section - disable select
      this.showNewItemInput(group, select, btn, input);
    } else {
      // Hide input section - enable select
      this.hideNewItemInput(group, select, btn, input);
    }
  }

  /**
   * Handle select change events - should NOT trigger toggle
   * This prevents the toggle from firing when user selects an option
   */
  handleSelectChange(selectId) {
    const select = document.getElementById(selectId);
    if (!select) return;

    // Just ensure the new item input is hidden when something is selected
    if (select.value) {
      const inputId =
        'new-' + selectId.replace('accessory-', '').replace('tool-', '').replace('request-', '');
      this.ensureNewItemInputHidden(inputId);
    }
  }

  /**
   * Ensure new item input is hidden when select has a value
   */
  ensureNewItemInputHidden(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const group = input.closest('[id$="-group"]');
    if (group && group.style.display !== 'none') {
      // Find corresponding elements and hide
      const selectId = inputId.replace('new-', '');
      const btnId = inputId + '-btn';

      const select = document.getElementById(selectId);
      const btn = document.getElementById(btnId);

      if (group && select && btn) {
        this.hideNewItemInput(group, select, btn, input);
      }
    }
  }

  /**
   * Show the new item input and disable the select
   */
  showNewItemInput(group, select, btn, input) {
    // Show the input group
    group.style.display = 'block';

    // Clear and disable the select
    select.value = '';
    select.disabled = true;
    select.classList.add('disabled');

    // Update button appearance
    btn.innerHTML = '<i class="fas fa-arrow-left"></i> Cancel';
    btn.classList.remove('btn-outline-secondary');
    btn.classList.add('btn-outline-danger');

    // Focus on input if it exists
    if (input) {
      setTimeout(() => input.focus(), 100);
    }

    // Add visual feedback
    this.addInputAnimation(group);
  }

  /**
   * Hide the new item input and enable the select
   */
  hideNewItemInput(group, select, btn, input) {
    // Hide the input group with animation
    group.style.animation = 'fadeOut 0.3s ease-out';

    setTimeout(() => {
      group.style.display = 'none';
      group.style.animation = '';
    }, 300);

    // Re-enable and focus the select
    select.disabled = false;
    select.classList.remove('disabled');

    // Reset button appearance
    btn.innerHTML = '<i class="bx bx-plus"></i> New';
    btn.classList.remove('btn-outline-danger');
    btn.classList.add('btn-outline-secondary');

    // Clear input value
    if (input) {
      input.value = '';
      input.classList.remove('field-error');
      this.clearFieldError(input);
    }

    // Focus back to select
    setTimeout(() => select.focus(), 100);
  }

  /**
   * Add smooth animation to input appearance
   */
  addInputAnimation(group) {
    group.style.animation = 'slideDown 0.3s ease-out';
    setTimeout(() => {
      group.style.animation = '';
    }, 300);
  }

  async submitNewItem(input, select) {
    const newValue = input.value.trim();
    if (!newValue) return;

    // Determine which endpoint to call based on the select/input
    let endpoint = null;
    if (select.id.includes('name')) {
      endpoint = '/ajax/add-accessory-name/';
    } else if (select.id.includes('manufacturer')) {
      endpoint = '/ajax/add-manufacturer/';
    }

    if (!endpoint) {
      console.error('Unknown field type for submitNewItem()');
      this.showNotification('Could not determine field type', 'error');
      return;
    }

    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: new URLSearchParams({ name: newValue }),
      });

      const data = await response.json();

      if (response.ok && data.id) {
        // ✅ Use real database ID
        const newOption = document.createElement('option');
        newOption.value = data.id;
        newOption.textContent = data.name;
        newOption.selected = true;
        select.appendChild(newOption);

        this.showNotification(`Added "${data.name}" successfully!`, 'success');
      } else {
        this.showNotification(data.error || 'Failed to add item', 'error');
      }
    } catch (error) {
      console.error('Error adding new item:', error);
      this.showNotification('Server error while adding item', 'error');
    }

    input.value = '';
    input.classList.add('hidden');
  }

  /**
   * Hide new item input by input ID
   */
  hideNewItemInputById(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;

    // Find the corresponding elements
    const group = input.closest('[id$="-group"]');
    const modal = input.closest('.popup-content');

    if (!group || !modal) return;

    // Find the select and button
    const selectId = inputId.replace('new-', '');
    const btnId = inputId.replace('new-', 'new-') + '-btn';

    const select = document.getElementById(selectId);
    const btn = document.getElementById(btnId);

    if (select && btn) {
      this.hideNewItemInput(group, select, btn, input);
    }
  }

  /**
   * Show field error with better UX
   */
  showFieldError(field, message) {
    // Clear existing error
    this.clearFieldError(field);

    // Add error styling
    field.classList.add('field-error');

    // Create error message
    const errorMsg = document.createElement('div');
    errorMsg.className = 'error-message';
    errorMsg.textContent = message;
    errorMsg.style.animation = 'slideDown 0.3s ease-out';

    // Insert error message
    field.parentNode.insertBefore(errorMsg, field.nextSibling);

    // Auto-clear error on input
    const clearError = () => {
      this.clearFieldError(field);
      field.removeEventListener('input', clearError);
    };
    field.addEventListener('input', clearError);
  }

  /**
   * Modal Management with improved UX
   */
  openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    // Reset any toggle states when opening modal
    this.resetModalToggles(modal);

    modal.style.display = 'flex';
    modal.classList.add('show');

    // Focus trap
    this.trapFocus(modal);

    // Prevent body scroll
    document.body.style.overflow = 'hidden';

    // Animation
    requestAnimationFrame(() => {
      modal.style.opacity = '1';
    });
  }

  closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    // Reset toggle states before closing
    this.resetModalToggles(modal);

    modal.style.opacity = '0';
    modal.classList.remove('show');

    setTimeout(() => {
      modal.style.display = 'none';
      document.body.style.overflow = '';
    }, 300);
  }

  /**
   * Reset all toggle states in a modal
   */
  resetModalToggles(modal) {
    // Hide all new item groups
    const newItemGroups = modal.querySelectorAll('[id$="-group"]');
    newItemGroups.forEach(group => {
      group.style.display = 'none';
      group.style.animation = '';
    });

    // Reset all select elements
    const selects = modal.querySelectorAll('select');
    selects.forEach(select => {
      select.disabled = false;
      select.classList.remove('disabled');
      select.style.animation = '';
    });

    // Reset all "New" buttons
    const newButtons = modal.querySelectorAll('button[id$="-btn"]');
    newButtons.forEach(btn => {
      btn.innerHTML = '<i class="bx bx-plus"></i> New';
      btn.classList.remove('btn-outline-danger');
      btn.classList.add('btn-outline-secondary');
    });

    // Clear all input fields and errors
    const inputs = modal.querySelectorAll('input[id^="new-"]');
    inputs.forEach(input => {
      input.value = '';
      input.disabled = false;
      input.classList.remove('disabled', 'field-error');
      this.clearFieldError(input);
    });
  }

  /**
   * Specific modal open functions
   */
  openAddToolModal(isEdit = false) {
    if (!isEdit) {
      this.resetAddForms();
    }
    this.openModal('add-tool-modal');
  }

  openAddAccessoryModal() {
    this.resetAddForms();
    this.openModal('add-accessory-modal');
  }

  openRequestAccessoryModal() {
    this.openModal('request-accessory-modal');
  }

  openResponseModal(requestId, status) {
    document.getElementById('response-request-id').value = requestId;
    document.getElementById('response-status').value = status;

    const modal = document.querySelector('#response-modal');
    const title = modal?.querySelector('h3');

    if (title) {
      const statusText = status === 'Approved' ? 'Approve' : 'Decline';
      const icon = status === 'Approved' ? 'fas fa-check-circle' : 'fas fa-times-circle';
      title.innerHTML = `<i class="bx ${icon}"></i> ${statusText} Request`;
    }

    this.openModal('response-modal');
  }

  /**
   * Reset forms to add mode
   */
  resetAddForms() {
    const forms = ['#add-tool-modal form', '#add-accessory-modal form'];

    forms.forEach(formSelector => {
      const form = document.querySelector(formSelector);
      if (form) {
        form.reset();

        // Reset modal title
        const modal = form.closest('.popup-overlay');
        const title = modal?.querySelector('h3');
        if (title && modal.id === 'add-tool-modal') {
          title.innerHTML = '<i class="as fa-tools"></i> Add Tool';
        } else if (title && modal.id === 'add-accessory-modal') {
          title.innerHTML = '<i class="fas fa-wrench"></i> Add Accessory';
        }
      }
    });
  }

  showTab(tabName) {
    const tabTrigger = document.querySelector(`#${tabName}-tab`);
    if (!tabTrigger) return;

    // Remove active class from all tabs
    document.querySelectorAll('.nav-link').forEach(tab => {
      tab.classList.remove('active');
    });

    // Hide all tab contents
    document.querySelectorAll('.tab-pane').forEach(pane => {
      pane.classList.remove('show', 'active');
    });

    // Show selected tab
    tabTrigger.classList.add('active');
    const targetPane = document.querySelector(tabTrigger.dataset.bsTarget);
    if (targetPane) {
      targetPane.classList.add('show', 'active');
    }
  }

  /**
   * Workshop Filter
   */
  filterByWorkshop() {
    const select = document.getElementById('workshopFilter');
    if (!select) return;

    const workshopId = select.value;
    const url = new URL(window.location.href);

    if (workshopId) {
      url.searchParams.set('workshop_id', workshopId);
    } else {
      url.searchParams.delete('workshop_id');
    }

    select.disabled = true;
    this.showLoading(select);

    window.location.href = url.toString();
  }

  /**
   * Request Management
   */
  filterRequests(status) {
    const rows = document.querySelectorAll('#requests-table tbody tr[data-status]');
    const buttons = document.querySelectorAll('.section-actions button[onclick*="filterRequests"]');

    buttons.forEach(btn => {
      btn.classList.remove('active');
      if (btn.onclick.toString().includes(`'${status}'`)) {
        btn.classList.add('active');
      }
    });

    rows.forEach(row => {
      const shouldShow = status === 'all' || row.dataset.status === status;
      row.style.display = shouldShow ? '' : 'none';

      if (shouldShow) {
        row.style.animation = 'fadeIn 0.3s ease-out';
      }
    });

    const visibleRows = Array.from(rows).filter(row => row.style.display !== 'none');
    this.updateResultsCounter(visibleRows.length, rows.length);
  }

  async editAccessory(id) {
    try {
      const url = urls.editAccessory.replace('0', id);
      const response = await fetch(url, {
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          Accept: 'application/json',
        },
      });

      if (!response.ok) throw new Error('Failed to fetch accessory data');

      const data = await response.json();
      this.populateAccessoryForm(data, id);
      this.openModal('add-accessory-modal');
    } catch (error) {
      console.error('Error fetching accessory data:', error);
      this.showNotification('Error loading accessory data', 'error');
    }
  }

  async editTool(id) {
    try {
      const url = urls.editTool.replace('0', id);
      const response = await fetch(url, {
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          Accept: 'application/json',
        },
      });

      if (!response.ok) throw new Error('Failed to fetch tool data');

      const data = await response.json();
      this.populateToolForm(data, id);
      this.openAddToolModal(true);
    } catch (error) {
      console.error('Error fetching tool data:', error);
      this.showNotification('Error loading tool data', 'error');
    }
  }

  /**
   * Populate forms with data
   */
  populateToolForm(data, id) {
    const fields = {
      'tool-name': data.name || '',
      'tool-manufacturer': data.manufacturer || '',
      'tool-model': data.model || '',
      'tool-serial': data.serial_number || '',
    };

    Object.entries(fields).forEach(([fieldId, value]) => {
      const field = document.getElementById(fieldId);
      if (field) field.value = value;
    });

    const form = document.querySelector('#add-tool-modal form');
    const title = document.querySelector('#add-tool-modal h3');

    if (form) form.action = `/accessories/tools/edit/${id}/`;
    if (title) title.innerHTML = '<i class="bx bx-edit"></i> Edit Tool';
  }

  populateAccessoryForm(data, id) {
    const fields = {
      'accessory-name': data.name || '',
      'accessory-equipment': data.equipment_description || '',
      'accessory-manufacturer': data.manufacturer || '',
      'accessory-note': data.note || '',
      'accessory-stock': data.stock_count || 0,
    };

    Object.entries(fields).forEach(([fieldId, value]) => {
      const field = document.getElementById(fieldId);
      if (field) field.value = value;
    });

    const form = document.querySelector('#add-accessory-modal form');
    const title = document.querySelector('#add-accessory-modal h3');

    if (form) form.action = `/accessories/edit/${id}/`;
    if (title) title.innerHTML = '<i class="bx bx-edit"></i> Edit Accessory';
  }

  /**
   * Specific Toggle Functions - Now properly implemented
   */
  toggleNewToolNameInput() {
    this.toggleInput('new-tool-name-group', 'tool-name', 'new-tool-name-btn', 'new-tool-name');
  }

  toggleNewToolManufacturerInput() {
    this.toggleInput(
      'new-tool-manufacturer-group',
      'tool-manufacturer',
      'new-tool-manufacturer-btn',
      'new-tool-manufacturer',
    );
  }

  toggleNewAccessoryNameInput() {
    this.toggleInput(
      'new-accessory-name-group',
      'accessory-name',
      'new-accessory-name-btn',
      'new-accessory-name',
    );
  }

  toggleNewAccessoryManufacturerInput() {
    this.toggleInput(
      'new-accessory-manufacturer-group',
      'accessory-manufacturer',
      'new-accessory-manufacturer-btn',
      'new-accessory-manufacturer',
    );
  }

  toggleNewRequestAccessoryNameInput() {
    this.toggleInput(
      'new-request-accessory-name-group',
      'request-accessory-name',
      'new-request-accessory-name-btn',
      'new-request-accessory-name',
    );
  }

  /**
   * Submit Functions - Enhanced
   */
  submitNewToolName() {
    this.submitNewItem('new-tool-name', 'tool-name', 'tool name');
  }

  submitNewToolManufacturer() {
    this.submitNewItem('new-tool-manufacturer', 'tool-manufacturer', 'manufacturer');
  }

  submitNewAccessoryName() {
    this.submitNewItem('new-accessory-name', 'accessory-name', 'accessory name');
  }

  submitNewAccessoryManufacturer() {
    this.submitNewItem('new-accessory-manufacturer', 'accessory-manufacturer', 'manufacturer');
  }

  submitNewRequestAccessoryName() {
    this.submitNewItem('new-request-accessory-name', 'request-accessory-name', 'accessory name');
  }

  /**
   * Form Validation
   */
  setupFormValidation() {
    this.addFormValidation();
  }

  addFormValidation() {
    const forms = document.querySelectorAll('form');

    forms.forEach(form => {
      const inputs = form.querySelectorAll('input[required], select[required]');

      inputs.forEach(input => {
        input.addEventListener('blur', () => this.validateField(input));
        input.addEventListener('input', () => this.clearFieldError(input));
      });

      form.addEventListener('submit', e => this.handleFormSubmit(e, form));
    });
  }

  validateField(field) {
    const isValid = field.checkValidity();
    const errorClass = 'field-error';

    field.classList.toggle(errorClass, !isValid);

    let errorMsg = field.nextElementSibling;
    if (errorMsg && errorMsg.classList.contains('error-message')) {
      errorMsg.remove();
    }

    if (!isValid) {
      this.showFieldError(field, field.validationMessage);
    }

    return isValid;
  }

  clearFieldError(field) {
    field.classList.remove('field-error');
    const errorMsg = field.nextElementSibling;
    if (errorMsg && errorMsg.classList.contains('error-message')) {
      errorMsg.style.animation = 'fadeOut 0.3s ease-out';
      setTimeout(() => {
        if (errorMsg.parentNode) {
          errorMsg.remove();
        }
      }, 300);
    }
  }

  handleFormSubmit(event, form) {
    const requiredFields = form.querySelectorAll('input[required], select[required]');
    let isValid = true;

    requiredFields.forEach(field => {
      if (!this.validateField(field)) {
        isValid = false;
      }
    });

    if (!isValid) {
      event.preventDefault();
      this.showNotification('Please fill in all required fields', 'error');

      const firstInvalid = form.querySelector('.field-error');
      if (firstInvalid) {
        firstInvalid.focus();
      }
    } else {
      const submitBtn = form.querySelector('button[type="submit"]');
      if (submitBtn) {
        this.showLoading(submitBtn);
      }
    }
  }

  /**
   * Event Binding
   */
  bindModalCloseEvents() {
    document.querySelectorAll('.close-popup').forEach(closeBtn => {
      closeBtn.addEventListener('click', e => {
        const modal = e.target.closest('.popup-overlay');
        if (modal) this.closeModal(modal.id);
      });
    });
  }

  bindFormEvents() {
    const buttonMappings = [
      { selector: 'button[onclick*="openAddToolModal"]', handler: () => this.openAddToolModal() },
      {
        selector: 'button[onclick*="openAddAccessoryModal"]',
        handler: () => this.openAddAccessoryModal(),
      },
      {
        selector: 'button[onclick*="openRequestAccessoryModal"]',
        handler: () => this.openRequestAccessoryModal(),
      },
    ];

    buttonMappings.forEach(({ selector, handler }) => {
      const btn = document.querySelector(selector);
      if (btn) {
        btn.onclick = e => {
          e.preventDefault();
          handler();
        };
      }
    });
  }

  bindOutsideClickClose() {
    document.addEventListener('click', e => {
      if (e.target.classList.contains('popup-overlay')) {
        this.closeModal(e.target.id);
      }
    });
  }

  bindKeyboardEvents() {
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        const activeModal = document.querySelector('.popup-overlay.show');
        if (activeModal) {
          this.closeModal(activeModal.id);
        }
      }
    });
  }

  /**
   * Utility Functions
   */
  showLoading(element) {
    if (!element) return;

    element.classList.add('loading');
    element.disabled = true;

    const originalText = element.textContent;
    element.dataset.originalText = originalText;
    element.innerHTML = '<i class="bx bx-loader-alt bx-spin"></i> Loading...';
  }

  hideLoading(element) {
    if (!element) return;

    element.classList.remove('loading');
    element.disabled = false;

    if (element.dataset.originalText) {
      element.innerHTML = element.dataset.originalText;
    }
  }

  showPageLoading() {
    const overlay = document.createElement('div');
    overlay.id = 'page-loading';
    overlay.innerHTML = `
      <div class="loading-spinner">
        <div class="spinner"></div>
        <p>Processing...</p>
      </div>
    `;
    overlay.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(0, 0, 0, 0.8);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 9999;
    `;

    document.body.appendChild(overlay);
  }

  showNotification(message, type = 'info', duration = 5000) {
    const notification = document.createElement('div');
    notification.className = `alert alert-${type} alert-notification`;
    notification.innerHTML = `
      ${message}
      <button type="button" class="btn-close" aria-label="Close"></button>
    `;

    notification.style.cssText = `
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: 9999;
      min-width: 300px;
      animation: slideInRight 0.3s ease-out;
    `;

    document.body.appendChild(notification);

    setTimeout(() => {
      if (notification.parentNode) {
        notification.style.animation = 'slideOutRight 0.3s ease-out';
        setTimeout(() => notification.remove(), 300);
      }
    }, duration);

    notification.querySelector('.btn-close').addEventListener('click', () => {
      notification.style.animation = 'slideOutRight 0.3s ease-out';
      setTimeout(() => notification.remove(), 300);
    });
  }

  updateResultsCounter(visible, total) {
    let counter = document.getElementById('results-counter');
    if (!counter) {
      counter = document.createElement('div');
      counter.id = 'results-counter';
      counter.className = 'results-counter';

      const tableContainer = document.querySelector('.table-responsive');
      if (tableContainer) {
        tableContainer.parentNode.insertBefore(counter, tableContainer);
      }
    }

    counter.textContent = `Showing ${visible} of ${total} results`;
  }

  trapFocus(modal) {
    const focusableElements = modal.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );

    const firstElement = focusableElements[0];
    const lastElement = focusableElements[focusableElements.length - 1];

    modal.addEventListener('keydown', e => {
      if (e.key === 'Tab') {
        if (e.shiftKey) {
          if (document.activeElement === firstElement) {
            lastElement.focus();
            e.preventDefault();
          }
        } else {
          if (document.activeElement === lastElement) {
            firstElement.focus();
            e.preventDefault();
          }
        }
      }
    });

    if (firstElement) {
      firstElement.focus();
    }
  }

  /**
   * Initialize components
   */
  initializeComponents() {
    if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
      const tooltipTriggerList = [].slice.call(
        document.querySelectorAll('[data-bs-toggle="tooltip"]'),
      );
      tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
      });
    }
  }

  /**
   * Auto-hide alerts
   */
  autoHideAlerts() {
    const alerts = document.querySelectorAll('.alert-notification');

    alerts.forEach(alert => {
      const closeBtn = alert.querySelector('.btn-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', () => {
          this.removeAlert(alert);
        });
      }

      setTimeout(() => {
        this.removeAlert(alert);
      }, 5000);
    });
  }

  removeAlert(alert) {
    if (!alert.parentNode) return;

    alert.style.animation = 'slideOutRight 0.3s ease-out forwards';
    setTimeout(() => {
      if (alert.parentNode) {
        alert.remove();
      }
    }, 300);
  }
}

/**
 * Enhanced CSS Animations
 */
const additionalCSS = `
@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}

@keyframes fadeOut {
    from { opacity: 1; }
    to { opacity: 0; }
}

@keyframes slideDown {
    from {
        opacity: 0;
        max-height: 0;
        transform: translateY(-10px);
    }
    to {
        opacity: 1;
        max-height: 200px;
        transform: translateY(0);
    }
}

@keyframes slideOutRight {
    from {
        opacity: 1;
        transform: translateX(0);
    }
    to {
        opacity: 0;
        transform: translateX(100%);
    }
}

@keyframes slideInRight {
    from {
        opacity: 0;
        transform: translateX(100%);
    }
    to {
        opacity: 1;
        transform: translateX(0);
    }
}

@keyframes pulse {
    0% { transform: scale(1); }
    50% { transform: scale(1.02); }
    100% { transform: scale(1); }
}

@keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
}

.field-error {
    border-color: var(--bs-danger, #dc3545) !important;
    box-shadow: 0 0 0 3px rgba(220, 53, 69, 0.1) !important;
}

.error-message {
    color: var(--bs-danger, #dc3545);
    font-size: 0.75rem;
    margin-top: 0.25rem;
    display: block;
}

.disabled {
    opacity: 0.6;
    pointer-events: none;
}

.btn-outline-danger {
    color: #dc3545;
    border-color: #dc3545;
}

.btn-outline-danger:hover {
    color: #fff;
    background-color: #dc3545;
    border-color: #dc3545;
}

.loading {
    position: relative;
    pointer-events: none;
}

.bx-spin {
    animation: spin 1s linear infinite;
}

.alert-notification {
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    border: none;
}

.btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

.popup-overlay {
    backdrop-filter: blur(5px);
    transition: opacity 0.3s ease;
}

.popup-content {
    animation: modalSlideIn 0.3s ease-out;
}

@keyframes modalSlideIn {
    from {
        opacity: 0;
        transform: scale(0.9) translateY(-50px);
    }
    to {
        opacity: 1;
        transform: scale(1) translateY(0);
    }
}

.form-select:focus,
.form-control:focus {
    border-color: var(--bs-primary, #0d6efd);
    box-shadow: 0 0 0 3px rgba(13, 110, 253, 0.1);
}

.btn.loading {
    color: transparent;
}

.btn.loading::after {
    content: "";
    position: absolute;
    width: 16px;
    height: 16px;
    top: 50%;
    left: 50%;
    margin-left: -8px;
    margin-top: -8px;
    border: 2px solid transparent;
    border-top-color: currentColor;
    border-radius: 50%;
    animation: spin 1s linear infinite;
}

#page-loading {
    backdrop-filter: blur(10px);
}

.loading-spinner {
    text-align: center;
    color: white;
}

.spinner {
    width: 40px;
    height: 40px;
    border: 4px solid rgba(255, 255, 255, 0.3);
    border-top: 4px solid white;
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin: 0 auto 1rem;
}

.results-counter {
    font-size: 0.875rem;
    color: var(--bs-secondary, #6c757d);
    margin-bottom: 0.5rem;
    text-align: right;
}
`;

// Add CSS to document
if (typeof document !== 'undefined') {
  const style = document.createElement('style');
  style.textContent = additionalCSS;
  document.head.appendChild(style);
}

/**
 * Global functions for backward compatibility
 */
let accessoriesManager;

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  accessoriesManager = new AccessoriesManager();
});

// Global function wrappers for inline event handlers
function openAddToolModal() {
  accessoriesManager?.openAddToolModal();
}

function openAddAccessoryModal() {
  accessoriesManager?.openAddAccessoryModal();
}

function openRequestAccessoryModal() {
  accessoriesManager?.openRequestAccessoryModal();
}

function openResponseModal(requestId, status) {
  accessoriesManager?.openResponseModal(requestId, status);
}

function closeModal(modalId) {
  accessoriesManager?.closeModal(modalId);
}

function showTab(tabName) {
  accessoriesManager?.showTab(tabName);
}

function filterByWorkshop() {
  accessoriesManager?.filterByWorkshop();
}

function filterRequests(status) {
  accessoriesManager?.filterRequests(status);
}

function confirmDelete(type, id, name) {
  accessoriesManager?.confirmDelete(type, id, name);
}

function editTool(id) {
  accessoriesManager?.editTool(id);
}

function editAccessory(id) {
  accessoriesManager?.editAccessory(id);
}

// Toggle functions - These are the key functions for the form toggles
function toggleNewToolNameInput() {
  accessoriesManager?.toggleNewToolNameInput();
}

function toggleNewToolManufacturerInput() {
  accessoriesManager?.toggleNewToolManufacturerInput();
}

function toggleNewAccessoryNameInput() {
  accessoriesManager?.toggleNewAccessoryNameInput();
}

function toggleNewAccessoryManufacturerInput() {
  accessoriesManager?.toggleNewAccessoryManufacturerInput();
}

function toggleNewRequestAccessoryNameInput() {
  accessoriesManager?.toggleNewRequestAccessoryNameInput();
}

// Submit functions - These handle adding new items
function submitNewToolName() {
  accessoriesManager?.submitNewToolName();
}

function submitNewToolManufacturer() {
  accessoriesManager?.submitNewToolManufacturer();
}

function submitNewAccessoryName() {
  accessoriesManager?.submitNewAccessoryName();
}

function submitNewAccessoryManufacturer() {
  accessoriesManager?.submitNewAccessoryManufacturer();
}

function submitNewRequestAccessoryName() {
  accessoriesManager?.submitNewRequestAccessoryName();
}

// Backward compatibility functions (maintain old naming for existing HTML)
function toggleNewToolName() {
  // This should NOT be called by onchange - it's for button clicks only
  accessoriesManager?.toggleNewToolNameInput();
}

function toggleNewToolManufacturer() {
  // This should NOT be called by onchange - it's for button clicks only
  accessoriesManager?.toggleNewToolManufacturerInput();
}

function toggleNewAccessoryName() {
  // This should NOT be called by onchange - it's for button clicks only
  accessoriesManager?.toggleNewAccessoryNameInput();
}

function toggleNewAccessoryManufacturer() {
  // This should NOT be called by onchange - it's for button clicks only
  accessoriesManager?.toggleNewAccessoryManufacturerInput();
}

function toggleNewRequestAccessoryName() {
  // This should NOT be called by onchange - it's for button clicks only
  accessoriesManager?.toggleNewRequestAccessoryNameInput();
}

// These functions should be used for onchange events instead
function handleToolNameChange() {
  accessoriesManager?.handleSelectChange('tool-name');
}

function handleToolManufacturerChange() {
  accessoriesManager?.handleSelectChange('tool-manufacturer');
}

function handleAccessoryNameChange() {
  accessoriesManager?.handleSelectChange('accessory-name');
}

function handleAccessoryManufacturerChange() {
  accessoriesManager?.handleSelectChange('accessory-manufacturer');
}

function handleRequestAccessoryNameChange() {
  accessoriesManager?.handleSelectChange('request-accessory-name');
}
