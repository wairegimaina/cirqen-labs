/**
 * Accessories & Tools Management System
 * All API paths resolved via window.API_BASE injected by the Django template.
 */

// ==================== API URL HELPER ====================

/**
 * Prepend the app's URL prefix (set by the template) to any path.
 * e.g.  apiUrl('/api/accessories')  →  '/partstools/api/accessories'
 */
function apiUrl(path) {
  const base = (window.API_BASE || '').replace(/\/$/, '');
  return base + path;
}

// ==================== UTILITIES ====================

const Utils = {
  getCsrfToken() {
    return (
      document.querySelector('meta[name="csrf-token"]')?.content ||
      document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
      ''
    );
  },

  // Delegates to the shared toast system (static/js/notify.js, loaded globally in base.html)
  showNotification(message, type = 'info', duration = 4000) {
    window.notify(message, type, duration);
  },

  toggleLoading(show) {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.style.display = show ? 'flex' : 'none';
  },

  async fetchData(url, options = {}) {
    const defaults = {
      headers: {
        'X-CSRFToken': this.getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest',
        ...(options.headers || {}),
      },
    };
    const merged = {
      ...defaults,
      ...options,
      headers: { ...defaults.headers, ...(options.headers || {}) },
    };

    const response = await fetch(url, merged);
    const contentType = response.headers.get('content-type') || '';

    if (!contentType.includes('application/json')) {
      const text = await response.text();
      console.warn(`Non-JSON response from ${url}:`, text.substring(0, 300));
      throw new Error('API endpoint not available or returned invalid response');
    }

    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  },
};

// ==================== MODAL MANAGER ====================

const ModalManager = {
  stack: [],

  open(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) {
      console.warn(`Modal #${modalId} not found`);
      return;
    }
    modal.classList.add('show');
    modal.style.display = 'flex';
    this.stack.push(modal);
    this._trapFocus(modal);
  },

  close() {
    const modal = this.stack.pop();
    if (!modal) return;
    modal.classList.remove('show');
    setTimeout(() => {
      modal.style.display = 'none';
    }, 300);
  },

  closeAll() {
    while (this.stack.length) this.close();
  },

  _trapFocus(modal) {
    const sel = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
    const els = [...modal.querySelectorAll(sel)];
    if (!els.length) return;
    modal.addEventListener('keydown', function handler(e) {
      if (e.key !== 'Tab') return;
      const first = els[0],
        last = els[els.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        last.focus();
        e.preventDefault();
      } else if (!e.shiftKey && document.activeElement === last) {
        first.focus();
        e.preventDefault();
      }
    });
  },
};

// ==================== FORM MANAGER ====================

const FormManager = {
  validate(form) {
    let valid = true;
    form.querySelectorAll('[required]').forEach((field) => {
      this.clearError(field);
      if (!field.value.trim()) {
        this.showError(field, 'This field is required');
        valid = false;
      }
    });
    return valid;
  },

  showError(field, msg) {
    field.classList.add('is-invalid');
    let div = field.nextElementSibling;
    if (!div?.classList.contains('invalid-feedback')) {
      div = document.createElement('div');
      div.className = 'invalid-feedback';
      field.after(div);
    }
    div.textContent = msg;
  },

  clearError(field) {
    field.classList.remove('is-invalid');
    const div = field.nextElementSibling;
    if (div?.classList.contains('invalid-feedback')) div.remove();
  },

  clearAllErrors(form) {
    form.querySelectorAll('.is-invalid').forEach((f) => this.clearError(f));
  },

  reset(form) {
    form.reset();
    this.clearAllErrors(form);
  },
};

// ==================== TAB MANAGER ====================

const TabManager = {
  init() {
    document.querySelectorAll('[data-bs-toggle="tab"]').forEach((tab) => {
      tab.addEventListener('click', (e) => {
        e.preventDefault();
        this.switch(tab);
      });
    });
  },

  switch(tab) {
    const targetId = tab.getAttribute('data-bs-target');
    const target = document.querySelector(targetId);
    if (!target) return;

    document.querySelectorAll('[data-bs-toggle="tab"]').forEach((t) => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
    document.querySelectorAll('.tab-pane').forEach((p) => p.classList.remove('show', 'active'));

    tab.classList.add('active');
    tab.setAttribute('aria-selected', 'true');
    target.classList.add('show', 'active');
  },

  switchTo(tabId) {
    const tab = document.getElementById(tabId);
    if (tab) this.switch(tab);
  },
};

// ==================== ACCESSORY MANAGER ====================

const AccessoryManager = {
  async load(workshopId = '') {
    Utils.toggleLoading(true);
    try {
      const url = workshopId
        ? apiUrl(`/api/accessories?workshop=${workshopId}`)
        : apiUrl('/api/accessories');
      const data = await Utils.fetchData(url);
      this.renderTable(data.accessories || []);
    } catch (err) {
      console.warn('Accessories API unavailable — using server-rendered data.');
    } finally {
      Utils.toggleLoading(false);
    }
  },

  renderTable(accessories) {
    const tbody = document.querySelector('#accessories-table tbody');
    if (!tbody) return;
    const isHod = document.body.dataset.isHod === 'true';
    const cols = isHod ? 9 : 7;

    if (!accessories.length) {
      tbody.innerHTML = `<tr><td colspan="${cols}" class="text-center text-muted py-4">No accessories found.</td></tr>`;
      return;
    }

    tbody.innerHTML = accessories
      .map(
        (a, i) => `
      <tr>
        <td>${i + 1}</td>
        <td><strong>${a.name || '-'}</strong></td>
        <td>${a.equipment || '-'}</td>
        <td>${a.manufacturer || '-'}</td>
        <td>
          <span class="badge ${a.stock_count < 5 ? 'bg-danger' : a.stock_count < 10 ? 'bg-warning text-dark' : 'bg-success'}">
            ${a.stock_count}
          </span>
        </td>
        <td>${a.unit_cost} KSh</td>
        <td>${a.note || '-'}</td>
        ${isHod ? `<td>${a.workshop || 'Global'}</td>` : ''}
        ${
          isHod
            ? `
          <td>
            <button class="btn btn-outline-primary btn-sm" data-action="edit-accessory"
                    data-id="${a.id}" title="Edit">
              <i class="fas fa-edit"></i>
            </button>
            <button class="btn btn-outline-danger btn-sm" data-action="confirm-delete"
                    data-type="accessory" data-id="${a.id}"
                    data-name="${a.name || 'this accessory'}" title="Delete">
              <i class="fas fa-trash"></i>
            </button>
          </td>`
            : ''
        }
      </tr>`,
      )
      .join('');
  },

  async delete(id, name) {
    Utils.toggleLoading(true);
    try {
      await Utils.fetchData(apiUrl(`/api/accessories/${id}`), { method: 'DELETE' });
      Utils.showNotification(`Accessory "${name}" deleted.`, 'success');
      await this.load();
    } catch (err) {
      console.error('Delete accessory error:', err);
      Utils.showNotification(err.message || 'Failed to delete accessory.', 'danger');
    } finally {
      Utils.toggleLoading(false);
    }
  },
};

// ==================== TOOL MANAGER ====================

const ToolManager = {
  async load(workshopId = '') {
    Utils.toggleLoading(true);
    try {
      const url = workshopId ? apiUrl(`/api/tools?workshop=${workshopId}`) : apiUrl('/api/tools');
      const data = await Utils.fetchData(url);
      this.renderTable(data.tools || []);
    } catch (err) {
      console.warn('Tools API unavailable — using server-rendered data.');
    } finally {
      Utils.toggleLoading(false);
    }
  },

  renderTable(tools) {
    const tbody = document.querySelector('#tools-table tbody');
    if (!tbody) return;
    const isHod = document.body.dataset.isHod === 'true';
    const isTech = document.body.dataset.isTech === 'tech'; // role stored as lowercase string
    const cols = isHod ? 7 : 5;

    if (!tools.length) {
      tbody.innerHTML = `<tr><td colspan="${cols}" class="text-center text-muted py-4">No tools found.</td></tr>`;
      return;
    }

    tbody.innerHTML = tools
      .map(
        (t, i) => `
      <tr>
        <td>${i + 1}</td>
        <td><strong>${t.name || '-'}</strong></td>
        <td>${t.manufacturer || '-'}</td>
        <td>${t.model || '-'}</td>
        <td>${t.serial_number || '-'}</td>
        ${isHod ? `<td>${t.workshop || 'Global'}</td>` : ''}
        ${
          isTech
            ? `
          <td>
            <button class="btn btn-outline-primary btn-sm" data-action="edit-tool"
                    data-id="${t.id}" title="Edit">
              <i class="fas fa-edit"></i>
            </button>
            <button class="btn btn-outline-danger btn-sm" data-action="confirm-delete"
                    data-type="tool" data-id="${t.id}"
                    data-name="${t.name || 'this tool'}" title="Delete">
              <i class="fas fa-trash"></i>
            </button>
          </td>`
            : ''
        }
      </tr>`,
      )
      .join('');
  },

  async delete(id, name) {
    Utils.toggleLoading(true);
    try {
      await Utils.fetchData(apiUrl(`/api/tools/${id}`), { method: 'DELETE' });
      Utils.showNotification(`Tool "${name}" deleted.`, 'success');
      await this.load();
    } catch (err) {
      console.error('Delete tool error:', err);
      Utils.showNotification(err.message || 'Failed to delete tool.', 'danger');
    } finally {
      Utils.toggleLoading(false);
    }
  },
};

// ==================== EDIT MODAL LOADERS ====================

const EditLoader = {
  async loadAccessory(id) {
    Utils.toggleLoading(true);
    try {
      // Uses the non-API Django view that returns JSON (get_accessory)
      const data = await Utils.fetchData(apiUrl(`/get/${id}/`));
      const form = document.getElementById('edit-accessory-form');
      if (!form) return;
      form.action = apiUrl(`/edit/${id}/`);

      _setSelectValue(form, '[name="name"]', data.name);
      _setSelectValue(form, '[name="manufacturer"]', data.manufacturer);
      _setSelectValue(form, '[name="equipment_description"]', data.equipment_description);
      _setVal(form, '[name="stock_count"]', data.stock_count);
      _setVal(form, '[name="unit_cost"]', data.unit_cost);
      _setVal(form, '[name="note"]', data.note);

      ModalManager.open('edit-accessory-modal');
    } catch (err) {
      Utils.showNotification('Failed to load accessory data.', 'danger');
    } finally {
      Utils.toggleLoading(false);
    }
  },

  async loadTool(id) {
    Utils.toggleLoading(true);
    try {
      const data = await Utils.fetchData(apiUrl(`/tools/get/${id}/`));
      const form = document.getElementById('edit-tool-form');
      if (!form) return;
      form.action = apiUrl(`/tools/edit/${id}/`);

      _setSelectValue(form, '[name="name"]', data.name);
      _setSelectValue(form, '[name="manufacturer"]', data.manufacturer);
      _setVal(form, '[name="model"]', data.model);
      _setVal(form, '[name="serial_number"]', data.serial_number);

      ModalManager.open('edit-tool-modal');
    } catch (err) {
      Utils.showNotification('Failed to load tool data.', 'danger');
    } finally {
      Utils.toggleLoading(false);
    }
  },
};

function _setSelectValue(form, selector, value) {
  const el = form.querySelector(selector);
  if (el) el.value = value || '';
}

function _setVal(form, selector, value) {
  const el = form.querySelector(selector);
  if (el) el.value = value || '';
}

// ==================== CONFIRM DELETE MODAL ====================

const ConfirmModal = {
  _pendingAction: null,

  prompt(message, onConfirm) {
    const msgEl = document.getElementById('confirm-modal-message');
    const btn = document.getElementById('confirm-modal-action');
    if (msgEl) msgEl.textContent = message;
    this._pendingAction = onConfirm;
    ModalManager.open('confirm-modal');

    // Clone to remove any stale listeners
    if (btn) {
      const fresh = btn.cloneNode(true);
      btn.replaceWith(fresh);
      fresh.addEventListener('click', () => {
        ModalManager.close();
        if (this._pendingAction) this._pendingAction();
        this._pendingAction = null;
      });
    }
  },
};

// ==================== RESPONSE MODAL (HOD approve / decline) ====================

const ResponseModal = {
  open(requestId, action) {
    const form = document.getElementById('response-form');
    const title = document.getElementById('response-modal-title');
    const actionInput = document.getElementById('response-action');
    const submitBtn = document.getElementById('response-submit-btn');

    if (!form) return;

    // Point the form at the correct prefixed URL
    form.action = apiUrl(`/requests/${requestId}/approve/`);
    if (actionInput) actionInput.value = action;

    const isApprove = action === 'approve';
    if (title) title.textContent = isApprove ? 'Approve Request' : 'Decline Request';
    if (submitBtn) {
      submitBtn.textContent = isApprove ? 'Approve' : 'Decline';
      submitBtn.className = `btn ${isApprove ? 'btn-success' : 'btn-danger'}`;
    }

    const approvalFields = document.getElementById('approval-fields');
    if (approvalFields) approvalFields.style.display = isApprove ? 'block' : 'none';

    ModalManager.open('response-modal');
  },
};

// ==================== REQUEST STATUS FILTER ====================

function initRequestStatusFilter() {
  const filter = document.getElementById('requestStatusFilter');
  if (!filter) return;

  filter.addEventListener('change', () => {
    const value = filter.value.toLowerCase();
    document.querySelectorAll('#requests-table-body tr[data-status]').forEach((row) => {
      const rowStatus = (row.dataset.status || '').toLowerCase();
      row.style.display = value === 'all' || rowStatus === value ? '' : 'none';
    });
  });

  // Apply default (pending) on load
  filter.dispatchEvent(new Event('change'));
}

// ==================== INLINE ADD: toggle new-item input ====================

function toggleNewItemInput(groupId, selectId, btnId, inputId) {
  const group = document.getElementById(groupId);
  const select = document.getElementById(selectId);
  const btn = document.getElementById(btnId);
  const input = document.getElementById(inputId);

  if (!group) return;
  const hidden = group.style.display === 'none';
  group.style.display = hidden ? 'block' : 'none';
  if (select) select.disabled = hidden;
  if (btn) btn.classList.toggle('btn-outline-danger', hidden);
  if (input && hidden) input.focus();
}

// ──── Request accessory modal — accessory name select → hidden field ────

function initRequestAccessoryNameSelect() {
  const sel = document.getElementById('request-accessory-name-select');
  const hidden = document.getElementById('request-accessory-name');
  if (sel && hidden) {
    sel.addEventListener('change', () => {
      hidden.value = sel.value;
    });
  }
}

function toggleNewRequestAccessoryNameInput() {
  const group = document.getElementById('new-request-accessory-name-group');
  if (group) group.style.display = group.style.display === 'none' ? 'block' : 'none';
}

async function submitNewRequestAccessoryName() {
  const input = document.getElementById('new-request-accessory-name');
  const sel = document.getElementById('request-accessory-name-select');
  const hidden = document.getElementById('request-accessory-name');
  if (!input?.value.trim()) return;

  try {
    const fd = new FormData();
    fd.append('name', input.value.trim());
    fd.append('csrfmiddlewaretoken', Utils.getCsrfToken());
    const res = await fetch(apiUrl('/ajax/add-accessory-name/'), { method: 'POST', body: fd });
    const data = await res.json();

    if (data.error) {
      Utils.showNotification(data.error, 'warning');
      return;
    }

    const opt = new Option(data.name, data.name, true, true);
    sel?.appendChild(opt);
    if (hidden) hidden.value = data.name;
    input.value = '';
    document.getElementById('new-request-accessory-name-group').style.display = 'none';
    Utils.showNotification(`"${data.name}" added.`, 'success');
  } catch (err) {
    Utils.showNotification('Failed to add accessory name.', 'danger');
  }
}

// ──── Request accessory modal — manufacturer select → hidden field ────

function initManufacturerNameSelect() {
  const sel = document.getElementById('request-manufacturer-name-select');
  const hidden = document.getElementById('request-manufacturer-name');
  if (sel && hidden) {
    sel.addEventListener('change', () => {
      hidden.value = sel.value;
    });
  }
}

function toggleNewRequestManufacturerInput() {
  const group = document.getElementById('new-request-manufacturer-name-group');
  if (group) group.style.display = group.style.display === 'none' ? 'block' : 'none';
}

async function submitNewRequestManufacturer() {
  const input = document.getElementById('new-request-manufacturer-name');
  const sel = document.getElementById('request-manufacturer-name-select');
  const hidden = document.getElementById('request-manufacturer-name');
  if (!input?.value.trim()) return;

  try {
    const fd = new FormData();
    fd.append('name', input.value.trim());
    fd.append('csrfmiddlewaretoken', Utils.getCsrfToken());
    const res = await fetch(apiUrl('/ajax/add-manufacturer/'), { method: 'POST', body: fd });
    const data = await res.json();

    if (data.error) {
      Utils.showNotification(data.error, 'warning');
      return;
    }

    const opt = new Option(data.name, data.name, true, true);
    sel?.appendChild(opt);
    if (hidden) hidden.value = data.name;
    input.value = '';
    document.getElementById('new-request-manufacturer-name-group').style.display = 'none';
    Utils.showNotification(`"${data.name}" added.`, 'success');
  } catch (err) {
    Utils.showNotification('Failed to add manufacturer.', 'danger');
  }
}

// ==================== EVENT HANDLERS ====================

const EventHandlers = {
  init() {
    this._modals();
    this._actionButtons();
    this._workshopFilter();
    this._keyboard();
    this._showTabButton();
    this._formValidation();
  },

  _modals() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action="open-modal"]');
      if (btn) ModalManager.open(btn.dataset.modal);
    });

    document.addEventListener('click', (e) => {
      if (e.target.closest('[data-action="close-modal"], .close-btn')) ModalManager.close();
      if (e.target.classList.contains('modal-overlay')) ModalManager.close();
    });
  },

  _actionButtons() {
    document.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;

      const action = btn.dataset.action;
      const id = btn.dataset.id;
      const name = btn.dataset.name;
      const type = btn.dataset.type;
      const requestId = btn.dataset.requestId;
      const status = btn.dataset.status;

      switch (action) {
        case 'edit-accessory':
          await EditLoader.loadAccessory(id);
          break;

        case 'edit-tool':
          await EditLoader.loadTool(id);
          break;

        case 'confirm-delete':
          ConfirmModal.prompt(
            `Are you sure you want to delete "${name}"? This cannot be undone.`,
            () => {
              if (type === 'accessory') AccessoryManager.delete(id, name);
              else if (type === 'tool') ToolManager.delete(id, name);
            },
          );
          break;

        case 'open-response-modal':
          ResponseModal.open(requestId || id, status || 'approve');
          break;

        case 'show-tab':
          TabManager.switchTo(btn.dataset.tab + '-tab');
          break;

        default:
          break;
      }
    });
  },

  _workshopFilter() {
    const filter = document.getElementById('workshopFilter');
    if (!filter) return;
    filter.addEventListener('change', (e) => {
      const wid = e.target.value;
      AccessoryManager.load(wid);
      ToolManager.load(wid);
    });
  },

  _keyboard() {
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') ModalManager.close();
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        document.querySelector('input[type="search"]')?.focus();
      }
    });
  },

  _showTabButton() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action="show-tab"]');
      if (btn) TabManager.switchTo(btn.dataset.tab + '-tab');
    });
  },

  _formValidation() {
    document.addEventListener('input', (e) => {
      if (e.target.classList.contains('is-invalid')) FormManager.clearError(e.target);
    });
  },
};

// ==================== AUTO-HIDE DJANGO ALERTS ====================

function autoHideAlerts() {
  document.querySelectorAll('.alert:not(.dynamic-alert)').forEach((alert) => {
    setTimeout(() => {
      alert.style.transition = 'opacity .3s';
      alert.style.opacity = '0';
      setTimeout(() => alert.remove(), 350);
    }, 5000);
  });
}

// ==================== INJECT MODAL CSS ====================

(function injectStyles() {
  const style = document.createElement('style');
  style.textContent = `
    @keyframes fadeOut {
      from { opacity: 1; transform: translateY(0); }
      to   { opacity: 0; transform: translateY(-8px); }
    }
    .modal { display: none; position: fixed; inset: 0; z-index: 1055;
             align-items: center; justify-content: center; }
    .modal.show { display: flex; }
    .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.45); }
    .modal-content { position: relative; z-index: 1; background: #fff;
                     border-radius: .5rem; width: 100%; max-width: 560px;
                     max-height: 90vh; overflow-y: auto;
                     box-shadow: 0 8px 32px rgba(0,0,0,.2); }
    .modal-header { display: flex; align-items: center; justify-content: space-between;
                    padding: 1rem 1.25rem; border-bottom: 1px solid #dee2e6; }
    .modal-body   { padding: 1.25rem; }
    .modal-footer { display: flex; justify-content: flex-end; gap: .5rem;
                    padding: 1rem 1.25rem; border-top: 1px solid #dee2e6; }
    .close-btn { background: none; border: none; font-size: 1.4rem; line-height: 1;
                 cursor: pointer; color: #6c757d; }
    .close-btn:hover { color: #000; }
  `;
  document.head.appendChild(style);
})();

// ==================== INIT ====================

document.addEventListener('DOMContentLoaded', () => {
  console.log('Accessories & Tools — DOMContentLoaded | API_BASE:', window.API_BASE || '(none)');

  TabManager.init();
  EventHandlers.init();
  autoHideAlerts();
  initRequestStatusFilter();
  initRequestAccessoryNameSelect();
  initManufacturerNameSelect();

  // Only fetch from the JSON API if Django did not already render table rows
  const accessoryTbody = document.querySelector('#accessories-table tbody');
  const toolTbody = document.querySelector('#tools-table tbody');

  const hasAccessoryRows =
    accessoryTbody &&
    accessoryTbody.querySelectorAll('tr').length > 0 &&
    !accessoryTbody.textContent.includes('No accessories found');

  const hasToolRows =
    toolTbody &&
    toolTbody.querySelectorAll('tr').length > 0 &&
    !toolTbody.textContent.includes('No tools found');

  if (!hasAccessoryRows) AccessoryManager.load();
  if (!hasToolRows) ToolManager.load();
});
