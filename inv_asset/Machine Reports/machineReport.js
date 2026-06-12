/* machineReport.js – unified for new IDs/structure with fixed SPA navigation */

/* =========================
   Utility: CSRF (Django)
   ========================= */
function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return decodeURIComponent(parts.pop().split(';').shift());
  return null;
}
const CSRF_TOKEN = getCookie('csrftoken');

/* =========================
   Toasts & Loading Overlay
   ========================= */
function createToastContainer() {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container position-fixed top-0 end-0 p-3';
    container.style.zIndex = '1100';
    document.body.appendChild(container);
  }
  return container;
}

function showToast(message, type = 'info') {
  const toastContainer = createToastContainer();
  const toast = document.createElement('div');
  toast.className = `toast align-items-center text-white bg-${type} border-0`;
  toast.setAttribute('role', 'alert');
  toast.setAttribute('aria-live', 'assertive');
  toast.setAttribute('aria-atomic', 'true');
  toast.setAttribute('data-bs-autohide', 'true');
  toast.setAttribute('data-bs-delay', '3000');
  toast.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">
        <i class="fas fa-info-circle me-2"></i>${message}
      </div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
    </div>
  `;
  toastContainer.appendChild(toast);
  new bootstrap.Toast(toast).show();
  toast.addEventListener('hidden.bs.toast', () => toast.remove());
}

function showLoadingOverlay() {
  const el = document.getElementById('loading-overlay');
  if (el) el.style.display = 'flex';
}
function hideLoadingOverlay() {
  const el = document.getElementById('loading-overlay');
  if (el) el.style.display = 'none';
}

/* =========================
   Date/Time header (optional)
   ========================= */
function updateDateTime() {
  const now = new Date();
  const timeEl = document.getElementById('current-time');
  if (timeEl) timeEl.textContent = now.toLocaleTimeString();
  if (now.getSeconds() === 0) {
    const lastUpdatedEl = document.getElementById('last-updated');
    if (lastUpdatedEl) {
      lastUpdatedEl.textContent = new Intl.DateTimeFormat('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      }).format(now);
    }
  }
}

/* =========================
   Filters & Export
   ========================= */
function clearFilters() {
  ['workshop-filter', 'category-filter', 'year-filter', 'month-filter'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  showLoadingOverlay();
  const form = document.getElementById('dashboard-filter-form');
  if (form) form.submit();
}

function exportEquipmentList() {
  showLoadingOverlay();
  // Replace this with a real export endpoint if needed
  setTimeout(() => {
    hideLoadingOverlay();
    showToast('Equipment list exported successfully!', 'success');
  }, 1200);
}

/* =========================
   Equipment Search
   ========================= */
function filterEquipmentTable(searchTerm) {
  const rows = document.querySelectorAll('.equipment-row');
  const term = (searchTerm || '').toLowerCase();
  let visibleCount = 0;

  rows.forEach(row => {
    const name = row.dataset.equipmentName || '';
    const serial = row.dataset.serial || '';
    const isVisible = name.includes(term) || serial.includes(term);
    row.style.display = isVisible ? '' : 'none';
    if (isVisible) visibleCount++;
  });

  const tbody = document.getElementById('equipment-table-body');
  if (!tbody) return;
  const existing = tbody.querySelector('.search-empty-state');
  if (existing) existing.remove();

  if (visibleCount === 0 && term) {
    const emptyRow = document.createElement('tr');
    emptyRow.className = 'search-empty-state';
    emptyRow.innerHTML = `
      <td colspan="${tbody.closest('table')?.querySelectorAll('thead th').length || 8}" class="text-center py-5">
        <div class="empty-state">
          <i class="bx bx-search" style="font-size: 3rem; display: block; margin-bottom: 15px; opacity: 0.5;"></i>
          <h5>No Results Found</h5>
          <p>No equipment matches "${searchTerm}".</p>
        </div>
      </td>
    `;
    tbody.appendChild(emptyRow);
  }
}

/* =========================
   Repair Details Modal
   ========================= */
function getStatusClass(status) {
  switch (status) {
    case 'Working': return 'bg-success';
    case 'Under repair': return 'bg-warning text-dark';
    case 'Not working': return 'bg-danger';
    default: return 'bg-secondary';
  }
}

function createRepairCard(repair) {
  const card = document.createElement('div');
  card.className = 'card mb-3';

  const partsHtml = (repair.spare_parts || []).map(part => `
    <div class="spare-part-item">
      <div class="d-flex justify-content-between">
        <span>${part.name}</span>
        <span class="fw-bold">x${part.quantity}</span>
      </div>
      ${part.remarks ? `<small class="text-muted">${part.remarks}</small>` : ''}
    </div>
  `).join('');

  card.innerHTML = `
    <div class="card-header bg-light">
      <div class="d-flex justify-content-between align-items-center">
        <div><i class="fas fa-calendar me-2"></i>${repair.date}</div>
        <span class="badge bg-primary">Job Card #${repair.id}</span>
      </div>
    </div>
    <div class="card-body">
      <h6 class="card-title">Description</h6>
      <p class="card-text">${repair.description || '-'}</p>

      <div class="row g-3">
        <div class="col-md-6">
          <h6>Action Taken</h6>
          <p class="mb-0">${repair.action_taken || '-'}</p>
        </div>
        <div class="col-md-6">
          <h6>Performed By</h6>
          <p class="mb-0">${repair.performed_by || '-'}</p>
        </div>
      </div>

      ${ (repair.spare_parts && repair.spare_parts.length) ? `
        <div class="mt-3">
          <h6>Spare Parts Used</h6>
          <div class="spare-parts-list">${partsHtml}</div>
        </div>` : '' }

      <div class="mt-3 pt-3 border-top">
        <div class="row g-2">
          <div class="col-auto">
            <span class="badge bg-info">
              <i class="fas fa-clock me-1"></i> Downtime: ${repair.downtime_hours}h
            </span>
          </div>
          <div class="col-auto">
            <span class="badge bg-success">
              <i class="fas fa-dollar-sign me-1"></i> Total Cost: ${Number(repair.total_cost || 0).toFixed(2)}
            </span>
          </div>
        </div>
      </div>
    </div>
  `;
  return card;
}

function showRepairDetails(equipmentId) {
  const modalEl = document.getElementById('repairDetailsModal');
  if (!modalEl) return;
  const modal = new bootstrap.Modal(modalEl);
  const content = document.getElementById('repairContent');
  const spinner = document.getElementById('repairLoadingSpinner');

  modal.show();
  if (content) content.style.display = 'none';
  if (spinner) spinner.style.display = 'block';

  fetch(`/machineReports/equipment/${equipmentId}/repair-details/`, {
    headers: CSRF_TOKEN ? { 'X-CSRFToken': CSRF_TOKEN } : {}
  })
    .then(res => res.json())
    .then(data => {
      // Equipment header
      const nameEl = document.querySelector('.equipment-name');
      const serialEl = document.querySelector('.equipment-serial');
      const categoryEl = document.querySelector('.equipment-category');
      const statusBadge = document.querySelector('.equipment-status');

      if (nameEl) nameEl.textContent = data.equipment?.name || '';
      if (serialEl) serialEl.textContent = data.equipment?.serial_number || '';
      if (categoryEl) categoryEl.textContent = data.equipment?.category || '';
      if (statusBadge) {
        statusBadge.className = `badge ${getStatusClass(data.equipment?.status)}`;
        statusBadge.textContent = data.equipment?.status || 'Unknown';
      }

      // History
      const historyList = document.querySelector('.repair-history-list');
      if (historyList) {
        historyList.innerHTML = '';
        const repairs = data.repairs || [];
        if (!repairs.length) {
          historyList.innerHTML = `
            <div class="text-center py-4">
              <i class="fas fa-tools fa-2x text-muted mb-3"></i>
              <p class="mb-0">No repair history found</p>
            </div>
          `;
        } else {
          repairs.forEach(r => historyList.appendChild(createRepairCard(r)));
        }
      }

      if (spinner) spinner.style.display = 'none';
      if (content) content.style.display = 'block';
    })
    .catch(err => {
      console.error('Error fetching repair details:', err);
      const body = document.getElementById('repairDetailsBody');
      if (body) {
        body.innerHTML = `
          <div class="alert alert-danger">
            <i class="fas fa-exclamation-triangle me-2"></i>
            Error loading repair details
          </div>
        `;
      }
    });
}

/* =========================
   Manufacturer Chart + Summary
   ========================= */
function renderChart(ctx, data) {
  const labels = Object.keys(data);
  const repairData = labels.map(label => data[label].total_repairs);
  const uptimeData = labels.map(label => data[label].avg_uptime);

  // Destroy existing chart if re-rendering
  if (ctx._chartInstance) {
    ctx._chartInstance.destroy();
    ctx._chartInstance = null;
  }

  const chart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Total Repairs',
          data: repairData,
          backgroundColor: 'rgba(255, 99, 132, 0.2)',
          borderColor: 'rgba(255, 99, 132, 1)',
          borderWidth: 1,
          yAxisID: 'y'
        },
        {
          label: 'Uptime %',
          data: uptimeData,
          backgroundColor: 'rgba(54, 162, 235, 0.2)',
          borderColor: 'rgba(54, 162, 235, 1)',
          borderWidth: 1,
          type: 'line',
          yAxisID: 'y1'
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { type: 'linear', display: true, position: 'left', beginAtZero: true },
        y1: { type: 'linear', display: true, position: 'right', grid: { drawOnChartArea: false }, min: 0, max: 100 }
      }
    }
  });

  ctx._chartInstance = chart;
}

function renderSummary(manufacturerData) {
  const table = document.getElementById('summary-table');
  const tbody = document.getElementById('summary-body');
  if (!table || !tbody || !manufacturerData) return;

  const getBestManufacturer = () => {
    let best = null;
    for (const [name, stats] of Object.entries(manufacturerData)) {
      if (stats.avg_uptime <= 80) continue;
      if (!best ||
          stats.total_repairs < best.stats.total_repairs ||
          (stats.total_repairs === best.stats.total_repairs && stats.avg_uptime > best.stats.avg_uptime)) {
        best = { name, stats };
      }
    }
    return best;
  };

  const getWorstManufacturer = () => {
    let worst = null;
    for (const [name, stats] of Object.entries(manufacturerData)) {
      if (!worst ||
          stats.total_repairs > worst.stats.total_repairs ||
          (stats.total_repairs === worst.stats.total_repairs && (stats.total_downtime || 0) > (worst.stats.total_downtime || 0))) {
        worst = { name, stats };
      }
    }
    return worst;
  };

  const best = getBestManufacturer();
  const worst = getWorstManufacturer();

  tbody.innerHTML = '';
  table.style.display = 'table';

  const addRow = (category, item, rowClass = '') => {
    if (!item) return;
    const tr = document.createElement('tr');
    if (rowClass) tr.classList.add(rowClass);
    tr.innerHTML = `
      <td><strong>${category}</strong></td>
      <td>${item.name}</td>
      <td>${item.stats.avg_uptime}%</td>
      <td>${item.stats.total_repairs}</td>
      <td>${item.stats.total_downtime || 0}</td>
    `;
    tbody.appendChild(tr);
  };

  if (best) addRow('Best', best, 'table-success');
  if (worst) addRow('Worst', worst, 'table-danger');
}

function renderManufacturerChart() {
  const jsonEl = document.getElementById('manufacturer-data');
  if (!jsonEl) return;
  
  try {
    const data = JSON.parse(jsonEl.textContent || '{}');
    const canvas = document.getElementById('manufacturerChart');
    if (canvas) {
      renderChart(canvas.getContext('2d'), data);
    }
    renderSummary(data);
  } catch (error) {
    console.error('Error parsing manufacturer data:', error);
  }
}

/* =========================
   SPA Navigation (Fixed Implementation)
   ========================= */
function setupSpaNav() {
  const navItems = document.querySelectorAll('.spa-nav .nav-item');
  const sections = document.querySelectorAll('.content-section');

  console.log('Found nav items:', navItems.length);
  console.log('Found sections:', sections.length);

  const activateSection = (sectionName) => {
    console.log('Activating section:', sectionName);
    
    // Update navigation active states
    navItems.forEach(navItem => {
      const isActive = navItem.dataset.section === sectionName;
      navItem.classList.toggle('active', isActive);
    });

    // Hide all sections first
    sections.forEach(section => {
      section.classList.remove('active', 'fade-in');
    });

    // Show target section
    const targetSection = document.getElementById(sectionName + 'Section');
    if (targetSection) {
      console.log('Found target section:', targetSection.id);
      targetSection.classList.add('active');
      
      // Add fade-in effect with a small delay
      setTimeout(() => {
        targetSection.classList.add('fade-in');
      }, 10);

      // Special handling for manufacturer section
      if (sectionName === 'manufacturers') {
        // Small delay to ensure section is visible before rendering chart
        setTimeout(() => {
          renderManufacturerChart();
        }, 100);
      }
    } else {
      console.error('Target section not found:', sectionName + 'Section');
    }
  };

  // Add click handlers to navigation items
  navItems.forEach(item => {
    const sectionName = item.dataset.section;
    if (sectionName) {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        console.log('Nav item clicked:', sectionName);
        activateSection(sectionName);
      });
    }
  });

  // Set default active section (dashboard)
  activateSection('dashboard');
}

/* =========================
   Best Criteria Handler for Manufacturer Section
   ========================= */
function setupBestCriteriaHandler() {
  const bestCriteriaSelect = document.getElementById('bestCriteria');
  if (bestCriteriaSelect) {
    bestCriteriaSelect.addEventListener('change', function() {
      // You can implement different sorting/highlighting logic here based on criteria
      const criteria = this.value;
      console.log('Best criteria changed to:', criteria);
      
      // Re-render chart/summary based on new criteria if needed
      renderManufacturerChart();
    });
  }
}

/* =========================
   DOM Ready
   ========================= */
document.addEventListener('DOMContentLoaded', () => {
  console.log('DOM Content Loaded - Initializing...');
  
  // Initialize SPA navigation
  setupSpaNav();
  
  // Setup best criteria handler
  setupBestCriteriaHandler();

  // Filter form overlay
  const filterForm = document.getElementById('dashboard-filter-form');
  if (filterForm) {
    filterForm.addEventListener('submit', () => showLoadingOverlay());
  }

  // Equipment search
    const searchInput = document.getElementById('equipment-search');
    if (searchInput) {
      searchInput.addEventListener('input', e => filterEquipmentTable(e.target.value));
    }
    $("#equipment-search").on("keyup", function () {
      fetchEquipment(1); // always reset to page 1 when searching
  });


  // Date/time tick
  updateDateTime();
  setInterval(updateDateTime, 1000);

  // Modal accessibility setup
  const repairModal = document.getElementById('repairDetailsModal');
  if (repairModal) {
    repairModal.addEventListener('shown.bs.modal', function () {
      const title = this.querySelector('h5.modal-title');
      if (title) {
        title.setAttribute('tabindex', '-1');
        title.focus();
      }
    });
  }

  // Close modals by clicking outside (optional)
  document.addEventListener('click', function (e) {
    const repairModal = document.getElementById('repairDetailsModal');
    if (!repairModal) return;
    const instance = bootstrap.Modal.getInstance(repairModal);
    if (instance &&
        repairModal.classList.contains('show') &&
        !repairModal.querySelector('.modal-content').contains(e.target)) {
      instance.hide();
    }
  });

    // Intercept pagination clicks
    $(document).on("click", "#equipment-pagination a", function (e) {
    e.preventDefault();
    const url = new URL($(this).attr("href"), window.location.origin);
    const page = url.searchParams.get("page") || 1;  // get just the page number
    fetchEquipment(page);
  });

  // Initial chart render if we're on manufacturers section
  const manufacturersSection = document.getElementById('manufacturersSection');
  if (manufacturersSection && manufacturersSection.classList.contains('active')) {
    renderManufacturerChart();
  }
});
function fetchEquipment(page = 1) {
    let searchQuery = $("#equipment-search").val();

    $.ajax({
        url: window.location.pathname,
        type: "GET",
        data: {
            page: page,
            search: searchQuery   // 👈 this must be passed
        },
        headers: {
            "X-Requested-With": "XMLHttpRequest"
        },
        success: function (data) {
            $("#equipment-table-body").html(data.rows);
            $("#equipment-pagination").html(data.pagination);
        },
        error: function (xhr, status, error) {
            console.error("Failed to load equipment:", error);
        }
    });
}


/* =========================
   Expose needed globals for template onClick
   ========================= */
window.clearFilters = clearFilters;
window.exportEquipmentList = exportEquipmentList;
window.filterEquipmentTable = filterEquipmentTable;
window.showRepairDetails = showRepairDetails;
window.renderManufacturerChart = renderManufacturerChart;




// Add these functions to your existing machineReport.js file

// Enhanced equipment count preview with AJAX
function updateEquipmentCountPreview() {
    const category = document.getElementById('export_category_advanced')?.value;
    const workshop = document.getElementById('export_workshop_advanced')?.value;
    const search = document.getElementById('export_search_advanced')?.value;
    const downloadAll = document.getElementById('downloadAllCategories')?.checked;
    
    // Create query parameters
    const params = new URLSearchParams();
    if (category && !downloadAll) params.append('category', category);
    if (workshop) params.append('workshop', workshop);
    if (search) params.append('search', search);
    
    // Make AJAX request to get equipment count
    fetch(`/equipment/count-preview/?${params.toString()}`, {
        method: 'GET',
        headers: {
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': document.querySelector('[name=csrf-token]').getAttribute('content')
        }
    })
    .then(response => response.json())
    .then(data => {
        let previewText;
        if (downloadAll) {
            previewText = `All categories (${data.total_equipment} total equipment across ${data.category_count} categories)`;
        } else if (category) {
            previewText = `${data.category_equipment} equipment in selected category`;
        } else {
            previewText = `${data.total_equipment} equipment matching current filters`;
        }
        
        if (search) {
            previewText += ` (search: "${search}")`;
        }
        
        // Update preview with status breakdown
        if (data.status_breakdown) {
            previewText += `\nStatus: ${data.status_breakdown.working} working, ${data.status_breakdown.under_repair} under repair, ${data.status_breakdown.not_working} not working`;
        }
        
        document.getElementById('preview-text').textContent = previewText;
        
        // Update category badges in quick export section
        updateCategoryBadges(data.category_counts);
    })
    .catch(error => {
        console.error('Error fetching equipment count:', error);
        document.getElementById('preview-text').textContent = 'Unable to preview equipment count';
    });
}

// Update category badges with current counts
function updateCategoryBadges(categoryCounts) {
    if (!categoryCounts) return;
    
    Object.entries(categoryCounts).forEach(([categoryId, count]) => {
        const badge = document.querySelector(`button[form*="${categoryId}"] .badge`);
        if (badge) {
            badge.textContent = count;
            badge.className = count > 0 ? 'badge bg-primary float-end' : 'badge bg-secondary float-end';
        }
    });
}

// Enhanced export validation
function validateExportForm() {
    const form = document.getElementById('advanced-export-form');
    const formData = new FormData(form);
    
    // Check if any filters are selected
    const hasFilters = formData.get('workshop') || 
                      formData.get('category') || 
                      formData.get('search') || 
                      formData.get('download_all_categories');
    
    if (!hasFilters) {
        const confirmExportAll = confirm(
            'No filters selected. This will export ALL equipment from ALL categories.\n\n' +
            'This may result in a very large PDF file. Continue?'
        );
        if (!confirmExportAll) return false;
    }
    
    // Check if repair history is selected with large dataset
    if (formData.get('include_repair_history') && formData.get('download_all_categories')) {
        const confirmLargeExport = confirm(
            'You have selected to include repair history for all categories.\n\n' +
            'This may result in a very large PDF file and longer generation time. Continue?'
        );
        if (!confirmLargeExport) return false;
    }
    
    return true;
}

// Smart category selection
function selectCategoryForExport(categoryId, categoryName) {
    // Update the advanced form
    const categorySelect = document.getElementById('export_category_advanced');
    const downloadAllCheckbox = document.getElementById('downloadAllCategories');
    
    if (categorySelect) {
        downloadAllCheckbox.checked = false;
        categorySelect.disabled = false;
        categorySelect.value = categoryId;
        
        // Switch to filters tab
        const filtersTab = document.getElementById('basic-tab');
        if (filtersTab) filtersTab.click();
        
        // Highlight the selection
        categorySelect.style.borderColor = '#0d6efd';
        categorySelect.style.boxShadow = '0 0 0 0.2rem rgba(13, 110, 253, 0.25)';
        
        setTimeout(() => {
            categorySelect.style.borderColor = '';
            categorySelect.style.boxShadow = '';
        }, 2000);
        
        updateEquipmentCountPreview();
        
        // Scroll to export section
        document.querySelector('.card.border-primary').scrollIntoView({ 
            behavior: 'smooth', 
            block: 'start' 
        });
    }
}

// Batch export functionality
function initiateBatchExport() {
    const selectedCategories = [];
    document.querySelectorAll('.category-checkbox:checked').forEach(checkbox => {
        selectedCategories.push({
            id: checkbox.value,
            name: checkbox.dataset.categoryName
        });
    });
    
    if (selectedCategories.length === 0) {
        alert('Please select at least one category for batch export.');
        return;
    }
    
    const confirmBatch = confirm(
        `You are about to generate ${selectedCategories.length} separate PDF reports:\n\n` +
        selectedCategories.map(cat => `• ${cat.name}`).join('\n') +
        '\n\nEach report will open in a new tab. Continue?'
    );
    
    if (!confirmBatch) return;
    
    // Generate reports with small delays to avoid overwhelming the server
    selectedCategories.forEach((category, index) => {
        setTimeout(() => {
            const form = document.createElement('form');
            form.method = 'GET';
            form.action = '/equipment/export-equipment-category-detailed-pdf/';
            form.target = '_blank';
            
            // Add category ID
            const categoryInput = document.createElement('input');
            categoryInput.type = 'hidden';
            categoryInput.name = 'category';
            categoryInput.value = category.id;
            form.appendChild(categoryInput);
            
            // Add standard options
            const repairHistoryInput = document.createElement('input');
            repairHistoryInput.type = 'hidden';
            repairHistoryInput.name = 'include_repair_history';
            repairHistoryInput.value = 'true';
            form.appendChild(repairHistoryInput);
            
            // Add workshop if not HOD
            const workshopInput = document.querySelector('input[name="workshop"][type="hidden"]');
            if (workshopInput) {
                const newWorkshopInput = document.createElement('input');
                newWorkshopInput.type = 'hidden';
                newWorkshopInput.name = 'workshop';
                newWorkshopInput.value = workshopInput.value;
                form.appendChild(newWorkshopInput);
            }
            
            document.body.appendChild(form);
            form.submit();
            document.body.removeChild(form);
        }, index * 500); // 500ms delay between each export
    });
}

// Export progress tracking
class ExportProgressTracker {
    constructor() {
        this.exports = new Map();
    }
    
    startExport(exportId, estimatedTime = 10000) {
        const exportInfo = {
            startTime: Date.now(),
            estimatedTime: estimatedTime,
            status: 'generating'
        };
        this.exports.set(exportId, exportInfo);
        this.showProgressModal(exportId);
    }
    
    showProgressModal(exportId) {
        const modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.id = `exportModal-${exportId}`;
        modal.innerHTML = `
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">
                            <i class="fas fa-file-pdf text-danger"></i> 
                            Generating PDF Report
                        </h5>
                    </div>
                    <div class="modal-body text-center">
                        <div class="spinner-border text-primary mb-3" role="status">
                            <span class="visually-hidden">Loading...</span>
                        </div>
                        <p>Your detailed equipment report is being generated...</p>
                        <div class="progress">
                            <div class="progress-bar progress-bar-striped progress-bar-animated" 
                                 role="progressbar" style="width: 0%"></div>
                        </div>
                        <small class="text-muted mt-2 d-block">
                            This may take 10-30 seconds depending on the amount of data.
                        </small>
                    </div>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
        const bootstrapModal = new bootstrap.Modal(modal);
        bootstrapModal.show();
        
        // Simulate progress
        this.simulateProgress(exportId);
        
        // Auto-close after estimated time
        setTimeout(() => {
            bootstrapModal.hide();
            modal.remove();
            this.exports.delete(exportId);
        }, this.exports.get(exportId).estimatedTime);
    }
    
    simulateProgress(exportId) {
        const progressBar = document.querySelector(`#exportModal-${exportId} .progress-bar`);
        if (!progressBar) return;
        
        let progress = 0;
        const interval = setInterval(() => {
            progress += Math.random() * 15;
            if (progress > 95) progress = 95;
            
            progressBar.style.width = `${progress}%`;
            
            if (progress >= 95) {
                clearInterval(interval);
                setTimeout(() => {
                    progressBar.style.width = '100%';
                    progressBar.textContent = 'Complete!';
                }, 1000);
            }
        }, 500);
    }
}

// Initialize progress tracker
const exportTracker = new ExportProgressTracker();

// Enhanced form submission with progress tracking
document.addEventListener('DOMContentLoaded', function() {
    const advancedForm = document.getElementById('advanced-export-form');
    if (advancedForm) {
        advancedForm.addEventListener('submit', function(e) {
            if (!validateExportForm()) {
                e.preventDefault();
                return;
            }
            
            const exportId = 'advanced-' + Date.now();
            const formData = new FormData(this);
            
            // Estimate time based on options
            let estimatedTime = 10000; // Base 10 seconds
            if (formData.get('download_all_categories')) estimatedTime += 15000;
            if (formData.get('include_repair_history')) estimatedTime += 10000;
            if (formData.get('include_calibration_history')) estimatedTime += 5000;
            
            exportTracker.startExport(exportId, estimatedTime);
        });
    }
});

// Add category selection checkboxes for batch export (call this after DOM is loaded)
function addBatchExportUI() {
    const categoryCards = document.querySelectorAll('.equipment-category-card');
    categoryCards.forEach(card => {
        const header = card.querySelector('.card-header');
        if (header) {
            const categoryName = header.querySelector('h5').textContent.trim();
            const categoryId = header.dataset.categoryId; // You'll need to add this data attribute
            
            const checkbox = document.createElement('div');
            checkbox.className = 'form-check form-check-inline ms-3';
            checkbox.innerHTML = `
                <input class="form-check-input category-checkbox" type="checkbox" 
                       value="${categoryId}" data-category-name="${categoryName}">
                <label class="form-check-label text-white">
                    <small>Select for batch export</small>
                </label>
            `;
            header.appendChild(checkbox);
        }
    });
    
    // Add batch export button
    const exportSection = document.querySelector('.card.border-primary .card-body');
    if (exportSection) {
        const batchButton = document.createElement('button');
        batchButton.type = 'button';
        batchButton.className = 'btn btn-warning me-2';
        batchButton.innerHTML = '<i class="fas fa-layer-group"></i> Batch Export Selected';
        batchButton.onclick = initiateBatchExport;
        
        const buttonContainer = exportSection.querySelector('.d-flex.gap-2');
        if (buttonContainer) {
            buttonContainer.appendChild(batchButton);
        }
    }
}