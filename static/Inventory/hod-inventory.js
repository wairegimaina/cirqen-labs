/**
 * hod-inventory.js
 * For Head of Department (HOD) users — read-only inventory view + analytics + PDF reports.
 * No edit, delete, or transfer actions. 7-column table (no Actions column).
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
        model: ''
    },
    charts: {
        status: null,
        department: null
    },
    initialized: false,
    retryCount: 0
};

// ==========================================
// 2. INITIALIZATION
// ==========================================
document.addEventListener('DOMContentLoaded', function () {
    if (InventoryState.initialized) return;

    try {
        loadDjangoData();
        initializeEventListeners();

        if (document.getElementById('inventorySection')) {
            loadEquipmentData();
            loadSummaryData();
        }

        InventoryState.initialized = true;
        console.log('✅ HOD Inventory System Initialized');
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
            InventoryState.currentFilters.department = params.get('department') || '';
            InventoryState.currentFilters.search = params.get('search') || '';
            InventoryState.currentFilters.status = params.get('status') || '';
            InventoryState.currentFilters.workshop = params.get('workshop') || InventoryState.djangoData.selectedWorkshop || '';
            InventoryState.currentFilters.model = params.get('model') || '';
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
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
        .then(res => {
            if (!res.ok) throw new Error(`Server Error (${res.status})`);
            return res.json();
        })
        .then(data => {
            if (data.success) {
                UI.renderTable(data.equipments || [], data.pagination);
                UI.renderPagination(data.pagination);
                updateBrowserURL(params);
                InventoryState.retryCount = 0;
            } else {
                UI.renderEmptyState(data.error || 'Failed to load data');
            }
        })
        .catch(err => {
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
    if (InventoryState.currentFilters.workshop) params.append('workshop', InventoryState.currentFilters.workshop);

    fetch(`${InventoryState.djangoData.urls.inventorySummary}?${params.toString()}`)
        .then(res => res.json())
        .then(data => {
            UI.updateSummaryWidgets(data.overall_totals || {});
            UI.renderSummaryTable(data.summary_data || []);
            if (data.overall_totals) {
                Charts.updateStatusChart(data.overall_totals);
            }
        })
        .catch(err => console.error('Summary load error:', err));
}

// ==========================================
// 4. UI & RENDERING
// ==========================================
const UI = {
    toggleLoading: (show) => {
        const loader = document.getElementById('loadingIndicator');
        if (loader) loader.style.display = show ? 'flex' : 'none';
        // Use .filter-section scope — consistent with HTML structure
        const inputs = document.querySelectorAll('.filter-section input, .filter-section select');
        inputs.forEach(el => el.disabled = show);
    },

    /**
     * HOD table is read-only: 7 columns (No, Description, Manufacturer, Model, Serial, Department, Status).
     * No action buttons rendered.
     */
    renderTable: (equipments, pagination) => {
        const tbody = document.getElementById('equipmentTableBody');
        if (!tbody) return;
        tbody.innerHTML = '';

        if (equipments.length === 0) {
            UI.renderEmptyState('No equipment found matching your criteria.');
            return;
        }

        const startCount = (pagination?.start_index != null ? pagination.start_index - 1 : (InventoryState.currentPage - 1) * InventoryState.currentPerPage);

        equipments.forEach((item, index) => {
            const tr = document.createElement('tr');
            tr.style.animationDelay = `${index * 0.05}s`;
            tr.className = 'fade-in table-row-fade-in';

            let badgeClass = 'bg-secondary';
            if (item.status === 'Working') badgeClass = 'bg-success';
            else if (item.status === 'Not working') badgeClass = 'bg-danger';
            else if (item.status === 'Under repair') badgeClass = 'bg-warning text-dark';

            tr.innerHTML = `
                <td>${escapeHTML(startCount + index + 1)}</td>
                <td class="fw-bold text-primary">${escapeHtml(item.description || 'Unknown')}</td>
                <td>${escapeHtml(item.manufacturer || '-')}</td>
                <td>${escapeHtml(item.model || '-')}</td>
                <td class="font-monospace">${escapeHtml(item.serial || '-')}</td>
                <td>${escapeHtml(item.department || '-')}</td>
                <td><span class="badge ${badgeClass}">${escapeHtml(item.status)}</span></td>
            `;
            tbody.appendChild(tr);
        });
    },

    renderEmptyState: (msg) => {
        const tbody = document.getElementById('equipmentTableBody');
        if (tbody) {
            // Matches HOD table: 7 columns, no Actions column
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center py-5 text-muted">
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

        pagination.page_range.forEach(p => {
            if (p === '...') {
                buttons += `<span class="px-2 text-muted">...</span>`;
            } else {
                const active = p === pagination.current_page ? 'active btn-primary' : 'btn-outline-secondary';
                buttons += `<button class="btn btn-sm ${escapeHTML(active)} ajax-page-btn" data-page="${p}">${p}</button>`;
            }
        });

        if (pagination.has_next) {
            buttons += `<button class="btn btn-sm btn-outline-secondary ajax-page-btn" data-page="${pagination.next_page_number}"><i class="fas fa-chevron-right"></i></button>`;
        }

        wrapper.innerHTML = `
            <div class="card-footer d-flex justify-content-between align-items-center">
                <small class="text-muted">Showing ${pagination.start_index}–${pagination.end_index} of ${pagination.total_count}</small>
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

        if (!data.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No summary data available.</td></tr>';
            return;
        }

        data.forEach((row, index) => {
            const percent = row.total_count > 0
                ? Math.round((row.working_count / row.total_count) * 100)
                : 0;
            const tr = document.createElement('tr');
            tr.style.animationDelay = `${index * 0.05}s`;
            tr.className = 'fade-in';
            tr.innerHTML = `
                <td>${escapeHtml(row.description__name)}</td>
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
    showNotification: (msg, type = 'info') => window.notify(msg, type)
};

// ==========================================
// 5. EVENT HANDLERS
// ==========================================
function initializeEventListeners() {
    // SPA Navigation — matches .spa-nav-item in HTML and CSS
    document.querySelectorAll('.spa-nav-item').forEach(btn => {
        btn.addEventListener('click', () => {
            const section = btn.dataset.section;

            document.querySelectorAll('.spa-nav-item').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            document.querySelectorAll('.content-section').forEach(s => {
                s.style.display = 'none';
                s.classList.remove('active');
            });

            const target = document.getElementById(`${section}Section`);
            if (target) {
                target.style.display = 'block';
                target.classList.add('active', 'fade-in');
            }

            if (section === 'summary') loadSummaryData();
            if (section === 'analytics') {
                Charts.refresh();
                // Ensure first analytics sub-tab is visible
                const chartsEl = document.getElementById('chartsAnalytics');
                const reportsEl = document.getElementById('reportsAnalytics');
                if (chartsEl) chartsEl.style.display = 'block';
                if (reportsEl) reportsEl.style.display = 'none';
            }
        });
    });

    // Pagination delegation
    document.getElementById('paginationWrapper')?.addEventListener('click', (e) => {
        const btn = e.target.closest('.ajax-page-btn');
        if (btn) {
            InventoryState.currentPage = parseInt(btn.dataset.page);
            loadEquipmentData();
        }
    });

    // Department and Status Filters
    ['departmentFilter', 'statusFilter'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', (e) => {
            InventoryState.currentFilters[id.replace('Filter', '')] = e.target.value;
            InventoryState.currentPage = 1;
            loadEquipmentData();
        });
    });

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

    // Clear Filters
    document.getElementById('clearFiltersBtn')?.addEventListener('click', () => {
        InventoryState.currentFilters = { department: '', search: '', status: '', workshop: InventoryState.djangoData.selectedWorkshop || '', model: '' };
        ['searchInput', 'departmentFilter', 'statusFilter'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        InventoryState.currentPage = 1;
        loadEquipmentData();
    });

    // Analytics Sub-Navigation (Charts vs Reports)
    document.querySelectorAll('.analytics-nav-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            document.querySelectorAll('.analytics-nav-btn').forEach(b => {
                b.classList.remove('active', 'btn-primary');
                b.classList.add('btn-outline-secondary');
            });
            this.classList.add('active', 'btn-primary');
            this.classList.remove('btn-outline-secondary');

            const targetId = this.dataset.analytics === 'charts' ? 'chartsAnalytics' : 'reportsAnalytics';
            document.querySelectorAll('.analytics-content').forEach(c => c.style.display = 'none');
            const el = document.getElementById(targetId);
            if (el) el.style.display = 'block';
        });
    });
}

// ==========================================
// 6. CHARTS
// ==========================================
const Charts = {
    updateStatusChart: (totals) => {
        const ctx = document.getElementById('statusChart');
        if (!ctx || typeof Chart === 'undefined') return;
        if (InventoryState.charts.status) InventoryState.charts.status.destroy();

        InventoryState.charts.status = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Working', 'Not Working', 'Under Repair'],
                datasets: [{
                    data: [totals.total_working || 0, totals.total_not_working || 0, totals.total_under_repair || 0],
                    backgroundColor: ['#10b981', '#ef4444', '#f59e0b'],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: 'bottom' } }
            }
        });
    },

    refresh: () => { loadSummaryData(); }
};

// ==========================================
// 7. REPORTS
// ==========================================
window.showReportModal = function (type, name) {
    const typeEl = document.getElementById('reportType');
    if (typeEl) typeEl.value = type;
    const modalEl = document.getElementById('reportModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
};

window.generateReport = function () {
    document.getElementById('reportForm')?.submit();
    bootstrap.Modal.getInstance(document.getElementById('reportModal'))?.hide();
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) {
        overlay.style.display = 'flex';
        setTimeout(() => overlay.style.display = 'none', 3000);
    }
};

// ==========================================
// UTILITIES
// ==========================================
function getCSRFToken() {
    return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
        document.cookie.match(/csrftoken=([\w-]+)/)?.[1];
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
    if (el) el.style.display = (el.style.display === 'none' || el.style.display === '') ? 'block' : 'none';
}
