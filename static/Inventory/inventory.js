/**
 * inventory.js - COMPLETE CORRECTED VERSION
 * All fixes applied: Edit modal, Manufacturer field, Model filter
 */

// ==========================================
// 1. STATE MANAGEMENT
// ==========================================
const InventoryState = {
  djangoData: {},
  currentPage: 1,
  currentPerPage: 10,
  currentFilters: {
    department: '',
    search: '',
    status: '',
    workshop: '',
    model: '', // ✅ ADDED MODEL FILTER
  },
  charts: {
    status: null,
    department: null,
  },
  initialized: false,
  retryCount: 0,
};

// ==========================================
// 2. INITIALIZATION
// ==========================================
document.addEventListener('DOMContentLoaded', function () {
  if (InventoryState.initialized) return;

  try {
    loadDjangoData();
    initializeEventListeners();
    initializeSearchAndFilter();
    initializePaginationSystem();
    EquipmentReactivation.init();
    ModelSelect.init();
    TransferLogic.init();
    ExcelImport.init();

    if (document.getElementById('inventorySection')) {
      loadEquipmentData();
      loadSummaryData();
      loadAnalyticsData();
    }

    InventoryState.initialized = true;
    console.log('✅ Inventory System Initialized');
  } catch (error) {
    console.error('❌ Initialization Error:', error);
    UI.showNotification('Failed to start application', 'error');
  }
});

function loadDjangoData() {
  const script = document.getElementById('django-data');
  if (script) {
    try {
      InventoryState.djangoData = JSON.parse(script.textContent);
      const params = new URLSearchParams(window.location.search);
      InventoryState.currentFilters.department =
        params.get('department') || InventoryState.djangoData.currentDepartment || '';
      InventoryState.currentFilters.search = params.get('search') || '';
      InventoryState.currentFilters.status = params.get('status') || '';
      InventoryState.currentFilters.workshop = params.get('workshop') || '';
      InventoryState.currentFilters.model = params.get('model') || ''; // ✅ ADDED
      InventoryState.currentPage = parseInt(params.get('page')) || 1;
    } catch (e) {
      console.error('Error parsing Django JSON:', e);
    }
  }
}

// ==========================================
// 3. CORE DATA LOADING
// ==========================================
function loadEquipmentData() {
  if (!InventoryState.djangoData.urls?.inventory) return;

  UI.toggleLoading(true);

  const params = new URLSearchParams();
  Object.entries(InventoryState.currentFilters).forEach(([k, v]) => {
    if (v) params.append(k, v);
  });
  params.append('page', InventoryState.currentPage);
  params.append('per_page', InventoryState.currentPerPage);

  fetch(`${InventoryState.djangoData.urls.inventory}?${params.toString()}`, {
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  })
    .then((res) => {
      if (!res.ok) throw new Error(`Server Error (${res.status})`);
      return res.json();
    })
    .then((data) => {
      if (data.success) {
        UI.renderTable(data.equipments || [], data.pagination);
        UI.renderPagination(data.pagination);
        updateBrowserURL(params);
        InventoryState.retryCount = 0;
      } else {
        UI.renderEmptyState(data.error || 'Failed to load data');
      }
    })
    .catch((err) => {
      console.error('Fetch error:', err);
      if (err.message.includes('Failed to fetch') || err.message.includes('NetworkError')) {
        UI.showNotification('Cannot connect to server', 'error');
        if (InventoryState.retryCount < 3) {
          InventoryState.retryCount++;
          setTimeout(loadEquipmentData, 1000);
        }
      } else {
        UI.renderEmptyState('Error loading data. Please refresh.');
      }
    })
    .finally(() => UI.toggleLoading(false));
}

function loadSummaryData() {
  if (!InventoryState.djangoData.urls?.inventorySummary) return;

  const params = new URLSearchParams();
  if (InventoryState.currentFilters.workshop)
    params.append('workshop', InventoryState.currentFilters.workshop);

  fetch(`${InventoryState.djangoData.urls.inventorySummary}?${params.toString()}`)
    .then((res) => res.json())
    .then((data) => {
      UI.updateSummaryWidgets(data.overall_totals || {});
      UI.renderSummaryTable(data.summary_data || []);
      if (data.overall_totals) {
        Charts.updateStatusChart(data.overall_totals);
      }
    })
    .catch((err) => console.error('Summary load error:', err));
}

function loadAnalyticsData() {
  if (!InventoryState.djangoData.urls?.equipmentAnalytics) return;

  const params = new URLSearchParams();
  Object.entries(InventoryState.currentFilters).forEach(([k, v]) => {
    if (v) params.append(k, v);
  });

  fetch(`${InventoryState.djangoData.urls.equipmentAnalytics}?${params.toString()}`)
    .then((res) => res.json())
    .then((data) => {
      if (data.department_counts) {
        Charts.updateDepartmentChart(data.department_counts);
      }
    })
    .catch((err) => console.error('Analytics load error:', err));
}

// ==========================================
// 4. UI & RENDERING
// ==========================================
const UI = {
  toggleLoading: (show) => {
    const loader = document.getElementById('loadingIndicator');
    if (loader) loader.style.display = show ? 'flex' : 'none';
    const inputs = document.querySelectorAll('.filter-grid input, .filter-grid select');
    inputs.forEach((el) => (el.disabled = show));
  },

  renderTable: (equipments, pagination) => {
    const tbody = document.getElementById('equipmentTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (equipments.length === 0) {
      UI.renderEmptyState('No equipment found matching your criteria.');
      return;
    }

    const startCount =
      pagination?.start_index != null
        ? pagination.start_index - 1
        : (InventoryState.currentPage - 1) * InventoryState.currentPerPage;

    equipments.forEach((item, index) => {
      const tr = document.createElement('tr');
      tr.style.animationDelay = `${index * 0.05}s`;
      tr.className = 'fade-in table-row-fade-in';

      let badgeClass = 'badge-secondary';
      if (item.status === 'Working') badgeClass = 'badge-success';
      else if (item.status === 'Not working') badgeClass = 'badge-danger';
      else if (item.status === 'Under repair') badgeClass = 'badge-warning';

      tr.innerHTML = `
                <td>${escapeHTML(startCount + index + 1)}</td>
                <td class="fw-bold text-primary">${escapeHtml(item.description || 'Unknown')}</td>
                <td>${escapeHtml(item.manufacturer || '-')}</td>
                <td>${escapeHtml(item.model || '-')}</td>
                <td class="font-monospace">${escapeHtml(item.serial || '-')}</td>
                <td>${escapeHtml(item.department || '-')}</td>
                <td><span class="badge ${badgeClass}">${escapeHTML(item.status)}</span></td>
                <td class="text-end">
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-primary edit-button"
                            data-id="${escapeHTML(item.id)}"
                            data-description="${escapeHTML(item.description_id)}"
                            data-manufacturer="${escapeHTML(item.manufacturer_id || '')}"
                            data-model="${escapeHtml(item.model || '')}"
                            data-serial="${escapeHtml(item.serial || '')}"
                            data-asset-tag="${escapeHtml(item.asset_tag || '')}"
                            data-department="${escapeHTML(item.department_id)}"
                            data-status="${escapeHTML(item.status)}"
                            title="Edit">
                            <i class="fas fa-pen"></i>
                        </button>
                        <button class="btn btn-outline-danger delete-button"
                            data-equipment-id="${escapeHTML(item.id)}"
                            title="Delete">
                            <i class="fas fa-trash"></i>
                        </button>
                        <button class="btn btn-outline-info transfer-button"
                            data-id="${escapeHTML(item.id)}"
                            data-description="${escapeHtml(item.description)}"
                            data-serial="${escapeHtml(item.serial)}"
                            data-department="${escapeHtml(item.department)}"
                            data-department-id="${escapeHTML(item.department_id)}"
                            data-workshop="${escapeHtml(item.workshop)}"
                            data-workshop-id="${escapeHTML(item.workshop_id)}"
                            data-status="${escapeHTML(item.status)}"
                            title="Transfer">
                            <i class="fas fa-exchange-alt"></i>
                        </button>
                    </div>
                </td>
            `;
      tbody.appendChild(tr);
    });
  },

  renderEmptyState: (msg) => {
    const tbody = document.getElementById('equipmentTableBody');
    if (tbody) {
      tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="text-center py-5 text-muted">
                        <i class="fas fa-inbox fa-3x mb-3 opacity-25"></i>
                        <p>${escapeHTML(msg)}</p>
                    </td>
                </tr>`;
    }
    const pag = document.getElementById('paginationWrapper');
    if (pag) pag.innerHTML = '';
  },

  renderPagination: (pagination) => {
    const wrapper = document.getElementById('paginationWrapper');
    if (!wrapper || !pagination || pagination.total_count === 0) {
      if (wrapper) wrapper.innerHTML = '';
      return;
    }

    let buttons = '';
    if (pagination.has_previous) {
      buttons += `<button class="btn btn-sm btn-outline-secondary ajax-page-btn" data-page="${pagination.previous_page_number}"><i class="fas fa-chevron-left"></i></button>`;
    }

    pagination.page_range.forEach((p) => {
      if (p === '...') buttons += `<span class="px-2 text-muted">...</span>`;
      else {
        const active =
          p === pagination.current_page ? 'active btn-primary' : 'btn-outline-secondary';
        buttons += `<button class="btn btn-sm ${escapeHTML(active)} ajax-page-btn" data-page="${p}">${p}</button>`;
      }
    });

    if (pagination.has_next) {
      buttons += `<button class="btn btn-sm btn-outline-secondary ajax-page-btn" data-page="${pagination.next_page_number}"><i class="fas fa-chevron-right"></i></button>`;
    }

    wrapper.innerHTML = `
            <div class="card-footer d-flex justify-content-between align-items-center">
                <small class="text-muted">Showing ${pagination.start_index}-${pagination.end_index} of ${pagination.total_count}</small>
                <div class="btn-group">${buttons}</div>
            </div>
        `;
  },

  updateSummaryWidgets: (totals) => {
    setText('totalEquipment', totals.total_equipment || 0);
    setText('workingEquipment', totals.total_working || 0);
    setText('notWorkingEquipment', totals.total_not_working || 0);
    setText('underRepairEquipment', totals.total_under_repair || 0);
  },

  renderSummaryTable: (data) => {
    const tbody = document.getElementById('summaryTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    data.forEach((row, index) => {
      const percent =
        row.total_count > 0 ? Math.round((row.working_count / row.total_count) * 100) : 0;
      const tr = document.createElement('tr');
      tr.style.animationDelay = `${index * 0.05}s`;
      tr.className = 'fade-in';

      tr.innerHTML = `
                <td>${escapeHTML(row.description__name)}</td>
                <td class="text-center text-success">${row.working_count}</td>
                <td class="text-center text-danger">${row.not_working_count}</td>
                <td class="text-center text-warning">${row.under_repair_count}</td>
                <td class="text-center fw-bold">${row.total_count}</td>
                <td>
                    <div class="progress" style="height: 6px;">
                        <div class="progress-bar bg-success" role="progressbar" style="width: ${percent}%"></div>
                    </div>
                    <small class="text-success fw-bold">${percent}%</small>
                </td>
            `;
      tbody.appendChild(tr);
    });
  },

  // Delegates to the shared toast system (static/js/notify.js, loaded globally in base.html)
  showNotification: (msg, type = 'info') => window.notify(msg, type),
};

// ==========================================
// 5. EVENT HANDLERS
// ==========================================
function initializeEventListeners() {
  // SPA Navigation
  document.querySelectorAll('.spa-nav-item[data-section]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      const section = btn.dataset.section;
      document.querySelectorAll('.spa-nav-item').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');

      document.querySelectorAll('.content-section').forEach((s) => (s.style.display = 'none'));
      const target = document.getElementById(`${section}Section`);
      if (target) {
        target.style.display = 'block';
        target.classList.add('fade-in');
      }

      if (section === 'summary') loadSummaryData();
      if (section === 'analytics') Charts.refresh();
    });
  });

  // Pagination
  document.getElementById('paginationWrapper')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.ajax-page-btn');
    if (btn) {
      InventoryState.currentPage = parseInt(btn.dataset.page);
      loadEquipmentData();
    }
  });

  // Department and Status Filters
  ['departmentFilter', 'statusFilter'].forEach((id) => {
    document.getElementById(id)?.addEventListener('change', (e) => {
      InventoryState.currentFilters[id.replace('Filter', '')] = e.target.value;
      InventoryState.currentPage = 1;
      loadEquipmentData();
    });
  });

  // ✅ MODEL FILTER - ADDED
  const modelFilter = document.getElementById('modelFilter');
  if (modelFilter) {
    let timeout;
    modelFilter.addEventListener('input', (e) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => {
        InventoryState.currentFilters.model = e.target.value.trim();
        InventoryState.currentPage = 1;
        loadEquipmentData();
      }, 500);
    });
  }

  // Debounced Search
  const searchInput = document.getElementById('searchInput');
  if (searchInput) {
    let timeout;
    searchInput.addEventListener('input', (e) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => {
        InventoryState.currentFilters.search = e.target.value.trim();
        InventoryState.currentPage = 1;
        loadEquipmentData();
      }, 500);
    });
  }

  // ✅ UPDATED Clear Filters - Include Model
  document.getElementById('clearFiltersBtn')?.addEventListener('click', () => {
    InventoryState.currentFilters = {
      department: '',
      search: '',
      status: '',
      workshop: '',
      model: '', // ✅ ADDED
    };
    if (document.getElementById('searchInput')) document.getElementById('searchInput').value = '';
    if (document.getElementById('departmentFilter'))
      document.getElementById('departmentFilter').value = '';
    if (document.getElementById('statusFilter')) document.getElementById('statusFilter').value = '';
    if (document.getElementById('modelFilter')) document.getElementById('modelFilter').value = ''; // ✅ ADDED
    loadEquipmentData();
  });

  // Table Actions Delegation
  document.addEventListener('click', (e) => {
    const editBtn = e.target.closest('.edit-button');
    if (editBtn) Modals.openEdit(editBtn.dataset);

    const delBtn = e.target.closest('.delete-button');
    if (delBtn) Modals.openDelete(delBtn.dataset.equipmentId);

    const transBtn = e.target.closest('.transfer-button');
    if (transBtn) Modals.openTransfer(transBtn.dataset);
  });

  // Modal Actions
  document.getElementById('confirmDelete')?.addEventListener('click', Modals.executeDelete);

  // Analytics Tabs
  document.querySelectorAll('.analytics-nav-btn').forEach((btn) => {
    btn.addEventListener('click', function () {
      document
        .querySelectorAll('.analytics-nav-btn')
        .forEach((b) => b.classList.remove('active', 'btn-primary'));
      this.classList.add('active', 'btn-primary');
      this.classList.remove('btn-outline-secondary');

      const target = this.dataset.analytics === 'charts' ? 'chartsAnalytics' : 'reportsAnalytics';
      document.querySelectorAll('.analytics-content').forEach((c) => (c.style.display = 'none'));
      document.getElementById(target).style.display = 'block';
    });
  });

  // Helper Toggles
  window.toggleNewEquipmentDescriptionInput = () =>
    toggleVisible('new-equipment-description-group');
  window.toggleNewManufacturerInput = () => toggleVisible('new-manufacturer-group'); // ✅ ADDED

  // Reset inputs when dropdown changes
  window.toggleNewEquipmentDescription = () => {
    if (document.getElementById('equipment_description_select').value)
      document.getElementById('new-equipment-description-group').style.display = 'none';
  };

  // ✅ ADDED MANUFACTURER TOGGLE
  window.toggleNewManufacturer = () => {
    if (document.getElementById('manufacturer_select').value)
      document.getElementById('new-manufacturer-group').style.display = 'none';
  };

  // ✅ ADDED CANCEL FUNCTIONS
  window.cancelNewManufacturer = () => {
    document.getElementById('new-manufacturer-group').style.display = 'none';
    document.getElementById('new_manufacturer').value = '';
  };

  window.cancelNewEquipmentDescription = () => {
    document.getElementById('new-equipment-description-group').style.display = 'none';
    document.getElementById('new_equipment_description').value = '';
  };
}

// ==========================================
// 6. MODAL MANAGEMENT - ✅ FIXED EDIT MODAL
// ==========================================
const Modals = {
  deleteId: null,

  // ✅ COMPLETELY REWRITTEN openEdit FUNCTION
  openEdit: (data) => {
    // Populate ALL fields correctly
    document.getElementById('edit_equipment_id').value = data.id;
    document.getElementById('edit_description').value = data.description;
    document.getElementById('edit_manufacturer').value = data.manufacturer || ''; // ✅ FIXED ID
    ModelSelect.load('edit', data.model || '');
    document.getElementById('edit_serial_number').value = data.serial || '';
    document.getElementById('edit_asset_tag').value = data.assetTag || '';
    document.getElementById('edit_department').value = data.department;
    document.getElementById('edit_status').value = data.status;

    // ✅ SET THE FORM ACTION DYNAMICALLY
    const form = document.getElementById('edit_form');
    form.action = `/Inventory/edit_inventory/${data.id}/`;

    // Show Modal
    const modal = new bootstrap.Modal(document.getElementById('editEquipmentModal'));
    modal.show();
  },

  openDelete: (id) => {
    Modals.deleteId = id;
    const el = document.getElementById('deleteModal');
    new bootstrap.Modal(el).show();
  },

  executeDelete: () => {
    if (!Modals.deleteId) return;

    const btn = document.getElementById('confirmDelete');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = 'Deleting...';

    fetch(`/Inventory/delete_equipment/${Modals.deleteId}/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCSRFToken(),
        'X-Requested-With': 'XMLHttpRequest',
      },
    })
      .then((res) => res.json())
      .then((data) => {
        bootstrap.Modal.getInstance(document.getElementById('deleteModal')).hide();
        if (data.success) {
          UI.showNotification('Equipment deleted successfully', 'success');
          loadEquipmentData();
        } else {
          UI.showNotification(data.error || 'Delete failed', 'error');
        }
      })
      .catch(() => UI.showNotification('Connection error during delete', 'error'))
      .finally(() => {
        btn.disabled = false;
        btn.innerHTML = originalText;
        Modals.deleteId = null;
      });
  },

  openTransfer: (data) => {
    const equipId = document.getElementById('transfer-equipment-id');
    const workshopId = document.getElementById('transfer-current-workshop-id');

    if (equipId) equipId.value = data.id;
    if (workshopId) workshopId.value = data.workshopId || '';

    setText('transfer-description', data.description);
    setText('transfer-serial', data.serial);
    setText('transfer-current-department', data.department);
    setText('transfer-current-workshop', data.workshop);

    TransferLogic.loadDepartments();
    new bootstrap.Modal(document.getElementById('transferModal')).show();
  },
};

// ==========================================
// 7. TRANSFER LOGIC
// ==========================================
const TransferLogic = {
  init: () => {},

  loadDepartments: () => {
    const select = document.getElementById('transfer-target-department');
    select.innerHTML = '<option>Loading...</option>';

    fetch('/Inventory/get_available_departments_for_transfer/', {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
      .then((res) => res.json())
      .then((data) => {
        select.innerHTML = '<option value="">Select Target Department</option>';

        const grouped = {};
        data.departments.forEach((dept) => {
          if (!grouped[dept.workshop_name]) grouped[dept.workshop_name] = [];
          grouped[dept.workshop_name].push(dept);
        });

        Object.keys(grouped).forEach((workshop) => {
          const group = document.createElement('optgroup');
          group.label = workshop;
          grouped[workshop].forEach((d) => {
            const opt = document.createElement('option');
            opt.value = d.id;
            opt.text = d.name;
            opt.dataset.workshopId = d.workshop_id;
            group.appendChild(opt);
          });
          select.appendChild(group);
        });

        select.onchange = TransferLogic.checkCrossWorkshop;
      });
  },

  checkCrossWorkshop: (e) => {
    const opt = e.target.selectedOptions[0];
    if (!opt) return;

    const currentWorkshopId = document.getElementById('transfer-current-workshop-id').value;
    const targetWorkshopId = opt.dataset.workshopId;
    const warning = document.getElementById('transfer-cross-workshop-warning');

    if (
      currentWorkshopId &&
      targetWorkshopId &&
      String(currentWorkshopId) !== String(targetWorkshopId)
    ) {
      warning.style.display = 'block';
    } else {
      warning.style.display = 'none';
    }
  },
};

window.confirmTransfer = function () {
  const id = document.getElementById('transfer-equipment-id').value;
  const dept = document.getElementById('transfer-target-department').value;
  const status = document.getElementById('transfer-new-status').value;

  if (!dept) return UI.showNotification('Please select a department', 'warning');

  const btn = document.getElementById('confirmTransferBtn');
  btn.disabled = true;

  const formData = new FormData();
  formData.append('target_department_id', dept);
  if (status) formData.append('status', status);

  fetch(`/Inventory/transfer_equipment/${id}/`, {
    method: 'POST',
    headers: { 'X-CSRFToken': getCSRFToken() },
    body: formData,
  })
    .then((res) => res.json())
    .then((data) => {
      if (data.success) {
        bootstrap.Modal.getInstance(document.getElementById('transferModal')).hide();
        UI.showNotification('Transfer successful!', 'success');
        InventoryState.currentPage = 1;
        loadEquipmentData();
      } else {
        alert(data.error);
      }
    })
    .finally(() => (btn.disabled = false));
};

// ==========================================
// 8. EQUIPMENT REACTIVATION LOGIC
// ==========================================
const EquipmentReactivation = {
  init: () => {
    document.querySelectorAll('input[name="serial_number"]').forEach((input) => {
      input.addEventListener('input', (e) => {
        e.target.value = e.target.value.toUpperCase();
      });

      input.addEventListener('blur', (e) => {
        if (e.target.value.length >= 3) EquipmentReactivation.check(e.target.value);
      });
    });
  },

  check: (serial) => {
    fetch(`/Inventory/check_equipment_availability/?serial_number=${encodeURIComponent(serial)}`)
      .then((res) => res.json())
      .then((data) => {
        if (data.can_reactivate) {
          EquipmentReactivation.showModal(data);
        } else if (data.is_active) {
          UI.showNotification(`Serial ${serial} is already active!`, 'warning');
          document.querySelector('#add_form input[name="serial_number"]').value = '';
        }
      })
      .catch((err) => console.log('Availability check skipped'));
  },

  showModal: (data) => {
    setText('reactivate-description', data.description);
    setText('reactivate-serial', data.serial_number);
    setText('reactivate-location', `${data.last_department} (${data.last_workshop})`);
    document.getElementById('reactivate-equipment-id').value = data.equipment_id;
    new bootstrap.Modal(document.getElementById('reactivationModal')).show();
  },
};

window.confirmReactivation = function () {
  const id = document.getElementById('reactivate-equipment-id').value;
  const dept = document.getElementById('reactivate-new-department').value;
  const status = document.getElementById('reactivate-new-status').value;

  const formData = new FormData();
  formData.append('department_id', dept);
  formData.append('status', status);
  formData.append('csrfmiddlewaretoken', getCSRFToken());

  fetch(`/Inventory/reactivate_equipment/${id}/`, {
    method: 'POST',
    body: formData,
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  })
    .then((res) => res.json())
    .then((data) => {
      if (data.success) {
        bootstrap.Modal.getInstance(document.getElementById('reactivationModal')).hide();
        UI.showNotification('Equipment reactivated!', 'success');
        loadEquipmentData();
        document.querySelector('#add_form input[name="serial_number"]').value = '';
      } else {
        alert(data.error);
      }
    });
};

window.clearSerialAndStartFresh = function () {
  bootstrap.Modal.getInstance(document.getElementById('reactivationModal')).hide();
  const input = document.querySelector('#add_form input[name="serial_number"]');
  if (input) {
    input.value = '';
    input.focus();
  }
};

// ==========================================
// 9. CHARTS
// ==========================================
const Charts = {
  updateStatusChart: (totals) => {
    const ctx = document.getElementById('statusChart');
    if (!ctx || typeof Chart === 'undefined') return;

    if (InventoryState.charts.status) InventoryState.charts.status.destroy();

    InventoryState.charts.status = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: ['Working', 'Not Working', 'Repair'],
        datasets: [
          {
            data: [totals.total_working, totals.total_not_working, totals.total_under_repair],
            backgroundColor: ['#10b981', '#ef4444', '#f59e0b'],
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
      },
    });
  },

  updateDepartmentChart: (departmentCounts) => {
    const ctx = document.getElementById('departmentChart');
    if (!ctx || typeof Chart === 'undefined') return;

    if (InventoryState.charts.department) InventoryState.charts.department.destroy();

    const labels = Object.keys(departmentCounts);
    const values = Object.values(departmentCounts);

    if (labels.length === 0) {
      ctx.parentElement.innerHTML = `
        <div class="d-flex flex-column align-items-center justify-content-center h-100 text-muted py-5">
          <i class="fas fa-building fa-3x mb-3 opacity-25"></i>
          <p>No department data available</p>
        </div>`;
      return;
    }

    // Generate a palette of colours that scales with department count
    const palette = [
      '#6366f1',
      '#10b981',
      '#f59e0b',
      '#ef4444',
      '#3b82f6',
      '#8b5cf6',
      '#ec4899',
      '#14b8a6',
      '#f97316',
      '#84cc16',
    ];
    const backgroundColors = labels.map((_, i) => palette[i % palette.length]);

    InventoryState.charts.department = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Equipment Count',
            data: values,
            backgroundColor: backgroundColors,
            borderRadius: 6,
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => ` ${ctx.parsed.y} item${ctx.parsed.y !== 1 ? 's' : ''}`,
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: { stepSize: 1, precision: 0 },
            grid: { color: 'rgba(0,0,0,0.05)' },
          },
          x: {
            ticks: {
              maxRotation: 35,
              minRotation: 0,
              font: { size: 11 },
            },
            grid: { display: false },
          },
        },
      },
    });
  },

  refresh: () => {
    loadSummaryData();
    loadAnalyticsData();
  },
};

function initializePaginationSystem() {}
function initializeSearchAndFilter() {}

// ==========================================
// 10. MODEL SELECT + EDITABLE LISTS
// ==========================================
// Same name ignoring case, spaces and punctuation: "PatienT MONITOR" = "patient- Monitor"
// (core/names.py name_key does the same on the server).
function nameKey(value) {
  return String(value || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

// The Model drop-down lists the models already used for the chosen
// description; "+" adds one that is not there yet.
const ModelSelect = {
  ids: {
    add: { desc: 'equipment_description_select', model: 'model', group: 'new-model-group-add', input: 'new_model_add' },
    edit: { desc: 'edit_description', model: 'edit_model', group: 'new-model-group-edit', input: 'new_model_edit' },
  },

  init() {
    Object.entries(this.ids).forEach(([mode, ids]) => {
      document.getElementById(ids.desc)?.addEventListener('change', () => this.load(mode));
    });
    // Reopening the Add Equipment modal starts on the form, not the list editor.
    document
      .getElementById('addEquipmentModal')
      ?.addEventListener('hidden.bs.modal', () => NameLists.toggle(false));
  },

  // Fill the list for the selected description, keeping ``keep`` selected.
  load(mode, keep = '') {
    const ids = this.ids[mode];
    const desc = document.getElementById(ids.desc)?.value;
    const select = document.getElementById(ids.model);
    if (!select) return Promise.resolve();
    if (!desc) {
      select.innerHTML = '<option value="" disabled selected>Select a description first...</option>';
      return Promise.resolve();
    }
    select.innerHTML = '<option value="" disabled selected>Loading models...</option>';
    return fetch(`/Inventory/api/models/${desc}/`)
      .then((res) => res.json())
      .then((data) => this.fill(select, data.success ? data.models : [], keep))
      .catch(() => this.fill(select, [], keep));
  },

  fill(select, models, keep) {
    const names = [...models];
    if (keep && !names.some((m) => m === keep)) names.unshift(keep);
    const first = names.length
      ? '<option value="" disabled>Select...</option>'
      : '<option value="" disabled>No models yet - press + to add one</option>';
    select.innerHTML =
      first + names.map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('');
    select.value = keep || '';
  },

  showNew(mode) {
    const ids = this.ids[mode];
    toggleVisible(ids.group);
    document.getElementById(ids.input)?.focus();
  },

  cancelNew(mode) {
    const ids = this.ids[mode];
    document.getElementById(ids.group).style.display = 'none';
    document.getElementById(ids.input).value = '';
  },

  addNew(mode) {
    const ids = this.ids[mode];
    const input = document.getElementById(ids.input);
    const select = document.getElementById(ids.model);
    const value = input.value.trim().replace(/\s+/g, ' ');
    if (!value) return UI.showNotification('Please enter a model', 'warning');
    // Already in the list under another spelling: pick that one.
    const same = [...select.options].find((o) => o.value && nameKey(o.value) === nameKey(value));
    if (same) {
      select.value = same.value;
      UI.showNotification(`"${same.value}" is already in the list - selected it`, 'info');
    } else {
      select.add(new Option(value, value, true, true));
    }
    this.cancelNew(mode);
  },
};

// Settings button on the Add Equipment modal: rename or delete descriptions,
// manufacturers and models. Delete is offered only for names nothing uses.
const NameLists = {
  kind: 'description',
  rows: [],

  toggle(open) {
    const panel = document.getElementById('nameListsPanel');
    const form = document.getElementById('add_form');
    const show = open === undefined ? panel.style.display === 'none' : open;
    panel.style.display = show ? 'block' : 'none';
    form.style.display = show ? 'none' : 'block';
    setText('addEquipmentTitle', show ? 'Edit Lists' : 'Add New Equipment');
    if (show) this.show(this.kind);
  },

  show(kind) {
    this.kind = kind;
    document.querySelectorAll('#nameListsPanel [data-list]').forEach((b) =>
      b.classList.toggle('active', b.dataset.list === kind),
    );
    document.getElementById('nameListsModelPicker').style.display = kind === 'model' ? 'block' : 'none';
    document.getElementById('nameListsFilter').value = '';
    setText(
      'nameListsHint',
      kind === 'model'
        ? 'A model is renamed on every device of this description. Renaming it to a model already listed merges the two.'
        : 'Delete is only possible when nothing uses the name.',
    );
    this.error('');
    if (kind === 'model') return this.loadModels();
    this.rows = [];
    this.render('Loading...');
    fetch('/Inventory/api/name-lists/')
      .then((res) => res.json())
      .then((data) => {
        if (!data.success) throw new Error(data.error || 'Could not load the list');
        this.rows = data[kind + 's'];
        this.render();
      })
      .catch((e) => this.error(e.message));
  },

  loadModels() {
    const desc = document.getElementById('nameListsModelDescription').value;
    this.rows = [];
    if (!desc) return this.render('Pick a description to see its models.');
    this.render('Loading...');
    fetch(`/Inventory/api/name-lists/models/${desc}/`)
      .then((res) => res.json())
      .then((data) => {
        if (!data.success) throw new Error(data.error || 'Could not load the models');
        this.rows = data.models.map((m) => ({ id: m.name, name: m.name, uses: m.uses }));
        this.render();
      })
      .catch((e) => this.error(e.message));
  },

  render(message) {
    const box = document.getElementById('nameListsRows');
    if (message) {
      box.innerHTML = `<div class="list-group-item text-muted">${escapeHtml(message)}</div>`;
      return;
    }
    const filter = nameKey(document.getElementById('nameListsFilter').value);
    const rows = this.rows.filter((r) => !filter || nameKey(r.name).includes(filter));
    if (!rows.length) {
      box.innerHTML = '<div class="list-group-item text-muted">Nothing to show.</div>';
      return;
    }
    const isModel = this.kind === 'model';
    box.innerHTML = rows
      .map((r, i) => {
        const devices = isModel ? r.uses : r.devices;
        const used = devices
          ? `<span class="badge text-bg-light border">${devices} device${devices === 1 ? '' : 's'}</span>`
          : r.uses
            ? '<span class="badge text-bg-light border" title="Used by other records">in use</span>'
            : '<span class="badge text-bg-light border text-muted">unused</span>';
        const del = isModel
          ? ''
          : `<button type="button" class="btn btn-sm btn-outline-danger" data-act="delete" data-i="${i}"
               ${r.uses ? 'disabled title="In use - cannot be deleted"' : 'title="Delete"'} aria-label="Delete">
               <i class="fas fa-trash"></i></button>`;
        return `<div class="list-group-item d-flex align-items-center gap-2" data-row="${i}">
            <span class="flex-grow-1 text-truncate" data-name>${escapeHtml(r.name)}</span>
            ${used}
            <button type="button" class="btn btn-sm btn-outline-secondary" data-act="edit" data-i="${i}" title="Rename" aria-label="Rename">
              <i class="fas fa-pen"></i></button>
            ${del}
          </div>`;
      })
      .join('');
    box.querySelectorAll('[data-act]').forEach((btn) => {
      const row = rows[Number(btn.dataset.i)];
      btn.addEventListener('click', () => (btn.dataset.act === 'edit' ? this.startEdit(btn, row) : this.remove(row)));
    });
  },

  startEdit(btn, row) {
    const item = btn.closest('.list-group-item');
    item.innerHTML = `
      <input type="text" class="form-control form-control-sm" maxlength="${this.kind === 'model' ? 100 : 150}">
      <button type="button" class="btn btn-sm btn-success" data-save aria-label="Save"><i class="fas fa-check"></i></button>
      <button type="button" class="btn btn-sm btn-secondary" data-cancel aria-label="Cancel"><i class="fas fa-times"></i></button>`;
    const input = item.querySelector('input');
    input.value = row.name;
    input.focus();
    const save = () => this.rename(row, input.value.trim());
    item.querySelector('[data-save]').addEventListener('click', save);
    item.querySelector('[data-cancel]').addEventListener('click', () => this.render());
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        save();
      } else if (e.key === 'Escape') {
        e.stopPropagation();
        this.render();
      }
    });
  },

  post(url, fields) {
    const body = new FormData();
    Object.entries(fields).forEach(([k, v]) => body.append(k, v));
    body.append('csrfmiddlewaretoken', getCSRFToken());
    return fetch(url, { method: 'POST', body, headers: { 'X-CSRFToken': getCSRFToken() } }).then((res) =>
      res.json().then((data) => {
        if (!data.success) throw new Error(data.error || 'Failed');
        return data;
      }),
    );
  },

  rename(row, name) {
    if (!name || name === row.name) return this.render();
    this.error('');
    const url =
      this.kind === 'model'
        ? `/Inventory/api/name-lists/models/${document.getElementById('nameListsModelDescription').value}/rename/`
        : `/Inventory/api/name-lists/${this.kind}/${row.id}/rename/`;
    this.post(url, { name, old: row.name })
      .then((data) => {
        if (this.kind === 'model') {
          UI.showNotification(`Model renamed on ${data.changed} device(s)`, 'success');
          return this.loadModels();
        }
        this.updateSelects(row.id, data.name);
        row.name = data.name;
        this.render();
        UI.showNotification('Renamed', 'success');
      })
      .catch((e) => this.error(e.message));
  },

  remove(row) {
    this.error('');
    this.post(`/Inventory/api/name-lists/${this.kind}/${row.id}/delete/`, {})
      .then(() => {
        this.updateSelects(row.id, null);
        this.rows = this.rows.filter((r) => r !== row);
        this.render();
        UI.showNotification(`"${row.name}" deleted`, 'success');
      })
      .catch((e) => this.error(e.message));
  },

  // Keep the page's drop-downs in step (name = null removes the option).
  updateSelects(id, name) {
    const selects =
      this.kind === 'description'
        ? ['equipment_description_select', 'edit_description', 'nameListsModelDescription']
        : ['manufacturer_select', 'edit_manufacturer'];
    selects.forEach((sid) => {
      const opt = document.getElementById(sid)?.querySelector(`option[value="${CSS.escape(id)}"]`);
      if (!opt) return;
      if (name === null) opt.remove();
      else opt.textContent = name;
    });
  },

  error(message) {
    const box = document.getElementById('nameListsError');
    box.textContent = message;
    box.style.display = message ? 'block' : 'none';
  },
};

window.ModelSelect = ModelSelect;
window.NameLists = NameLists;

// ==========================================
// 11. REPORTS & UTILS
// ==========================================
window.showReportModal = function (type, name) {
  document.getElementById('reportType').value = type;
  new bootstrap.Modal(document.getElementById('reportModal')).show();
};

window.generateReport = function () {
  document.getElementById('reportForm').submit();
  bootstrap.Modal.getInstance(document.getElementById('reportModal')).hide();

  const overlay = document.getElementById('loadingOverlay');
  if (overlay) {
    overlay.style.display = 'flex';
    setTimeout(() => (overlay.style.display = 'none'), 3000);
  }
};

// ✅ FIXED - Submit new Description
window.submitNewEquipmentDescription = function () {
  submitAuxData(
    'new_equipment_description',
    'equipment_description_select',
    '/Inventory/create_equipment_description/',
    'description_name',
  );
};

// ✅ ADDED - Submit new Manufacturer
window.submitNewManufacturer = function () {
  submitAuxData(
    'new_manufacturer',
    'manufacturer_select',
    '/Inventory/create-manufacturer/',
    'manufacturer_name',
  );
};

function submitAuxData(inputId, selectId, url, fieldName) {
  const input = document.getElementById(inputId);
  const select = document.getElementById(selectId);

  if (!input.value.trim()) return alert('Please enter a value');

  const formData = new FormData();
  formData.append(fieldName, input.value.trim());
  formData.append('csrfmiddlewaretoken', getCSRFToken());

  fetch(url, { method: 'POST', body: formData })
    .then((res) => res.json())
    .then((data) => {
      const id = data[fieldName.replace('_name', '_id')] || data.id;
      if (data.success || data.existing) {
        // An existing name (same apart from case, spaces or punctuation) is
        // selected rather than added twice.
        let opt = id && select.querySelector(`option[value="${CSS.escape(String(id))}"]`);
        if (!opt) {
          opt = new Option(data[fieldName] || input.value, id);
          select.add(opt);
        }
        select.value = opt.value;
        select.dispatchEvent(new Event('change'));

        input.parentElement.style.display = 'none';
        input.value = '';
        UI.showNotification(
          data.existing ? `"${opt.text}" already exists - selected it` : 'Added successfully',
          data.existing ? 'info' : 'success',
        );
      } else {
        UI.showNotification(data.error || 'Failed to add', 'danger');
      }
    });
}

// ==========================================
// BULK EXCEL IMPORT
// ==========================================
// The chosen File is kept in memory and posted twice: once to preview
// (server validates and rolls back) and once to commit. Nothing is stored
// server-side between the two calls.
const ExcelImport = {
  file: null,
  modalEl: null,

  init() {
    this.modalEl = document.getElementById('uploadEquipmentModal');
    if (!this.modalEl) return;

    document
      .getElementById('uploadPreviewBtn')
      ?.addEventListener('click', () => this.send(false));
    document
      .getElementById('uploadConfirmBtn')
      ?.addEventListener('click', () => this.send(true));
    document
      .getElementById('uploadBackBtn')
      ?.addEventListener('click', () => this.reset());
    document
      .getElementById('uploadDoneBtn')
      ?.addEventListener('click', () => this.finish());

    this.modalEl.addEventListener('hidden.bs.modal', () => this.reset());
  },

  reset() {
    this.file = null;
    const input = document.getElementById('equipmentUploadFile');
    if (input) input.value = '';
    document.getElementById('uploadStepSelect').style.display = 'block';
    document.getElementById('uploadStepReview').style.display = 'none';
    document.getElementById('uploadFooter').style.display = 'none';
    document.getElementById('uploadError').style.display = 'none';
    document.getElementById('uploadConfirmBtn').style.display = 'inline-block';
    document.getElementById('uploadBackBtn').style.display = 'inline-block';
    document.getElementById('uploadDoneBtn').style.display = 'none';
    document.getElementById('uploadPreviewBody').innerHTML = '';
  },

  finish() {
    bootstrap.Modal.getInstance(this.modalEl)?.hide();
    loadEquipmentData();
    loadSummaryData();
    loadAnalyticsData();
  },

  showError(message) {
    const box = document.getElementById('uploadError');
    box.textContent = message;
    box.style.display = 'block';
  },

  send(commit) {
    if (!commit) {
      const input = document.getElementById('equipmentUploadFile');
      if (!input.files.length) return this.showError('Please choose an Excel file first.');
      this.file = input.files[0];
      document.getElementById('uploadError').style.display = 'none';
    }
    if (!this.file) return this.showError('Please choose an Excel file first.');

    const button = document.getElementById(commit ? 'uploadConfirmBtn' : 'uploadPreviewBtn');
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spinner-border spinner-border-sm me-2"></span>${
      commit ? 'Importing...' : 'Validating...'
    }`;

    const formData = new FormData();
    formData.append('file', this.file);
    formData.append('commit', commit ? 'true' : 'false');
    formData.append(
      'create_missing',
      document.getElementById('uploadCreateMissing').checked ? 'true' : 'false',
    );
    formData.append('csrfmiddlewaretoken', getCSRFToken());

    fetch(InventoryState.djangoData.urls.uploadEquipmentExcel, {
      method: 'POST',
      body: formData,
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
      .then((res) => res.json())
      .then((data) => {
        if (!data.success) {
          if (commit) {
            UI.showNotification(data.error || 'Import failed', 'error');
          } else {
            this.showError(data.error || 'Could not read that file.');
          }
          return;
        }
        this.render(data);
      })
      .catch(() => {
        const message = 'Upload failed. Please try again.';
        commit ? UI.showNotification(message, 'error') : this.showError(message);
      })
      .finally(() => {
        button.disabled = false;
        button.innerHTML = original;
      });
  },

  render(data) {
    const counts = data.counts;
    const importable = counts.create + counts.reactivate;

    document.getElementById('uploadStepSelect').style.display = 'none';
    document.getElementById('uploadStepReview').style.display = 'block';
    document.getElementById('uploadFooter').style.display = 'flex';

    setText('uploadStatTotal', data.total);
    setText('uploadStatCreate', counts.create);
    setText('uploadStatReactivate', counts.reactivate);
    setText('uploadStatError', counts.error);

    const banner = document.getElementById('uploadResultBanner');
    banner.style.display = 'block';
    if (data.committed) {
      banner.className = 'alert alert-success';
      banner.innerHTML = `<i class="fas fa-check-circle me-2"></i><strong>Import complete.</strong>
        ${escapeHTML(counts.create)} added, ${escapeHTML(counts.reactivate)} reactivated${
        counts.error ? `, ${counts.error} skipped due to errors` : ''
      }.`;
    } else if (importable === 0) {
      banner.className = 'alert alert-danger';
      banner.innerHTML = `<i class="fas fa-times-circle me-2"></i><strong>Nothing can be imported.</strong>
        Fix the errors below and upload again.`;
    } else {
      banner.className = 'alert alert-info';
      banner.innerHTML = `<i class="fas fa-info-circle me-2"></i><strong>Preview only — nothing has been saved yet.</strong>
        ${escapeHTML(importable)} row(s) are ready to import${
        counts.error ? `; ${counts.error} row(s) have errors and will be skipped` : ''
      }.`;
    }

    const newValues = document.getElementById('uploadNewValues');
    const parts = [];
    if (data.created_descriptions.length) {
      parts.push(
        `<strong>Descriptions ${data.committed ? 'created' : 'to create'}:</strong> ${data.created_descriptions
          .map(escapeHtml)
          .join(', ')}`,
      );
    }
    if (data.created_manufacturers.length) {
      parts.push(
        `<strong>Manufacturers ${data.committed ? 'created' : 'to create'}:</strong> ${data.created_manufacturers
          .map(escapeHtml)
          .join(', ')}`,
      );
    }
    newValues.innerHTML = parts.join('<br>');
    newValues.style.display = parts.length ? 'block' : 'none';

    const truncated = document.getElementById('uploadTruncated');
    const warnings = [];
    if (data.file_truncated) {
      warnings.push('The file exceeds the 5,000-row limit; only the first 5,000 rows were processed.');
    }
    if (data.rows_truncated) {
      warnings.push('Only the first 500 rows are listed below; all rows were still processed.');
    }
    truncated.innerHTML = warnings.join('<br>');
    truncated.style.display = warnings.length ? 'block' : 'none';

    const badges = {
      create: '<span class="badge badge-success">New</span>',
      reactivate: '<span class="badge badge-warning">Reactivate</span>',
      error: '<span class="badge badge-danger">Error</span>',
    };
    document.getElementById('uploadPreviewBody').innerHTML = data.rows
      .map(
        (row) => `<tr class="${escapeHTML(row.action === 'error' ? 'table-danger' : '')}">
          <td>${escapeHTML(row.row)}</td>
          <td>${badges[row.action] || escapeHtml(row.action)}</td>
          <td class="font-monospace">${escapeHtml(row.serial) || '-'}</td>
          <td>${escapeHtml(row.description) || '-'}</td>
          <td>${escapeHtml(row.department) || '-'}</td>
          <td class="small ${escapeHTML(row.action === 'error' ? 'text-danger' : 'text-muted')}">
            ${row.messages.map(escapeHtml).join('<br>')}
          </td>
        </tr>`,
      )
      .join('');

    const confirmBtn = document.getElementById('uploadConfirmBtn');
    const doneBtn = document.getElementById('uploadDoneBtn');
    const backBtn = document.getElementById('uploadBackBtn');

    if (data.committed) {
      confirmBtn.style.display = 'none';
      backBtn.style.display = 'none';
      doneBtn.style.display = 'inline-block';
      UI.showNotification(`Imported ${importable} equipment record(s)`, 'success');
    } else {
      confirmBtn.style.display = importable ? 'inline-block' : 'none';
      confirmBtn.innerHTML = `<i class="fas fa-check"></i> Import ${escapeHTML(importable)} row(s)`;
      doneBtn.style.display = 'none';
      backBtn.style.display = 'inline-block';
    }
  },
};

// Utils
function getCSRFToken() {
  return (
    document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
    document.cookie.match(/csrftoken=([\w-]+)/)?.[1]
  );
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function escapeHtml(text) {
  return window.escapeHTML(text); // static/js/escape.js
}

function updateBrowserURL(params) {
  const newURL = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState(null, '', newURL);
}

function toggleVisible(id) {
  const el = document.getElementById(id);
  el.style.display = el.style.display === 'none' || el.style.display === '' ? 'block' : 'none';
}
