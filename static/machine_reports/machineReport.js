document.addEventListener('DOMContentLoaded', () => {
  // 1. Initialize Tabs
  const tabs = document.querySelectorAll('.nav-item');
  const sections = document.querySelectorAll('.content-section');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      // Remove active class from all
      tabs.forEach(t => t.classList.remove('active'));
      sections.forEach(s => s.classList.remove('active'));

      // Add active to clicked
      tab.classList.add('active');
      const target = document.getElementById(`${tab.dataset.section}Section`);
      if (target) target.classList.add('active');

      // Render chart if Manufacturers tab
      if (tab.dataset.section === 'manufacturers') renderManufacturerChart();
    });
  });

  // 2. Initial Data Load for Equipment Table
  fetchEquipment(1);

  // 3. Search Listener
  const searchInput = document.getElementById('equipment-search');
  let debounceTimer;
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        fetchEquipment(1, e.target.value);
      }, 300);
    });
  }

  // 4. Export Modal Handlers
  setupExportModal();
});

// --- CORE FUNCTION: Fetch and Render Table ---
function fetchEquipment(page = 1, search = '') {
  const loading = document.getElementById('table-loading');
  const tbody = document.getElementById('equipment-table-body');
  const category = document.getElementById('category-filter')?.value || '';

  if (loading) loading.style.display = 'flex';

  const params = new URLSearchParams({ page, search, category });
  const currentPath = window.location.pathname;

  fetch(`${currentPath}?${params}`, {
    method: 'GET',
    headers: {
      'X-CSRFToken': getCookie('csrftoken'),
      'Accept': 'application/json'
    }
  })
    .then(res => {
      if (!res.ok) {
        throw new Error(`HTTP error! status: ${res.status}`);
      }
      return res.json();
    })
    .then(data => {
      tbody.innerHTML = '';

      if (!data.results || data.results.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-5 text-muted">No equipment found.</td></tr>`;
      } else {
        const startIdx = (data.current_page - 1) * 20;
        data.results.forEach((item, index) => {
          const row = `
                    <tr>
                        <td>${startIdx + index + 1}</td>
                        <td>
                            <div class="fw-bold">${escapeHtml(item.description?.name || item.name || '-')}</div>
                        </td>
                        <td>${escapeHtml(item.manufacturer || '-')}</td>
                        <td class="font-monospace">${escapeHtml(item.serial_number || '-')}</td>
                        <td>${escapeHtml(item.model || '-')}</td>
                        <td><span class="badge badge-category">${escapeHtml(item.category?.name || '-')}</span></td>
                        <td>
                            <span class="status-badge ${getStatusClass(item.status)}">${escapeHtml(item.status)}</span>
                        </td>
                        <td class="text-center">
                            <button class="btn-icon" onclick="showRepairDetails('${escapeHtml(item.id)}')" title="View History">
                                <i class="fas fa-history"></i>
                            </button>
                            <a class="btn-icon ms-1" href="/machineReports/export/${escapeHtml(item.id)}/" title="Download Excel History" download>
                                <i class="fas fa-file-excel"></i>
                            </a>
                        </td>
                    </tr>
                `;
          tbody.insertAdjacentHTML('beforeend', row);
        });
      }

      renderPagination(data);
    })
    .catch(err => {
      console.error('Error fetching equipment:', err);
      tbody.innerHTML = `<tr><td colspan="8" class="text-center py-5 text-danger">Error loading equipment data. Please try again.</td></tr>`;
    })
    .finally(() => {
      if (loading) loading.style.display = 'none';
    });
}

function renderPagination(data) {
  const container = document.getElementById('pagination-controls');
  if (!container) return;

  container.innerHTML = `
        <div class="text-muted small">Page ${data.current_page || 1} of ${data.total_pages || 1} (${data.total_count || 0} items)</div>
        <div class="btn-group">
            <button class="btn btn-outline-secondary btn-sm"
                ${!data.has_previous ? 'disabled' : ''}
                onclick="fetchEquipment(${data.previous || 1})">Previous</button>
            <button class="btn btn-outline-secondary btn-sm"
                ${!data.has_next ? 'disabled' : ''}
                onclick="fetchEquipment(${data.next || 1})">Next</button>
        </div>
    `;
}

// --- REPAIR DETAILS MODAL FUNCTION ---
function showRepairDetails(equipmentId) {
  console.log('Fetching repair details for equipment:', equipmentId);

  // Get modal elements
  const modal = new bootstrap.Modal(document.getElementById('repairDetailsModal'));
  const loadingSpinner = document.getElementById('repairLoadingSpinner');
  const contentDiv = document.getElementById('repairContent');

  // Show modal and loading state
  modal.show();
  if (loadingSpinner) loadingSpinner.style.display = 'block';
  if (contentDiv) contentDiv.style.display = 'none';

  // Fetch repair details from API
  fetch(`/machineReports/equipment/${equipmentId}/repair-details/`, {
    method: 'GET',
    headers: {
      'X-CSRFToken': getCookie('csrftoken'),
      'Accept': 'application/json'
    }
  })
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return response.json();
    })
    .then(data => {
      console.log('Repair details received:', data);
      populateRepairModal(data);

      // Hide loading, show content
      if (loadingSpinner) loadingSpinner.style.display = 'none';
      if (contentDiv) contentDiv.style.display = 'block';
    })
    .catch(error => {
      console.error('Error fetching repair details:', error);
      if (loadingSpinner) loadingSpinner.style.display = 'none';
      if (contentDiv) {
        contentDiv.style.display = 'block';
        contentDiv.innerHTML = `
        <div class="alert alert-danger">
          <i class="fas fa-exclamation-triangle me-2"></i>
          <strong>Error loading repair history:</strong> ${error.message}
        </div>
      `;
      }
    });
}

// --- POPULATE MODAL WITH DATA ---
function populateRepairModal(data) {
  const { equipment, repairs, summary, calibration_certificates } = data;

  // Update equipment header
  document.querySelector('.equipment-name').textContent = equipment.description || equipment.name || 'Unknown Equipment';
  document.querySelector('.equipment-serial').textContent = equipment.serial_number || 'N/A';
  document.querySelector('.equipment-category').textContent = equipment.category || 'N/A';

  const statusBadge = document.querySelector('.equipment-status');
  statusBadge.textContent = equipment.status || 'Unknown';
  statusBadge.className = `badge equipment-status fs-6 ${getStatusBadgeClass(equipment.status)}`;

  // Populate summary statistics
  document.querySelector('.summary-total-repairs').textContent = summary.total_repairs || 0;
  document.querySelector('.summary-total-cost').textContent = parseFloat(summary.total_cost || 0).toFixed(2);
  document.querySelector('.summary-total-downtime').textContent = (summary.total_downtime || 0).toFixed(1);
  document.querySelector('.summary-avg-repair-cost').textContent = parseFloat(summary.average_repair_cost || 0).toFixed(2);

  // Populate repair history
  const repairHistoryList = document.querySelector('.repair-history-list');
  if (repairs && repairs.length > 0) {
    repairHistoryList.innerHTML = repairs.map(repair => `
      <div class="card mb-3 border-start border-primary border-3">
        <div class="card-body p-3">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <div>
              <h6 class="mb-1 fw-bold text-primary">Job Card #${repair.id.substring(0, 8)}</h6>
              <small class="text-muted">
                <i class="fas fa-calendar me-1"></i>${formatDate(repair.date)}
                ${repair.time_started ? `<span class="mx-1">•</span>${repair.time_started} - ${repair.time_completed || 'Ongoing'}` : ''}
              </small>
            </div>
            <span class="badge bg-success">KSh ${parseFloat(repair.total_cost).toFixed(2)}</span>
          </div>

          <p class="mb-2"><strong>Description:</strong> ${repair.description || 'No description'}</p>
          <p class="mb-2"><small class="text-muted"><i class="fas fa-user me-1"></i>${repair.performed_by}</small></p>

          ${repair.downtime_hours > 0 ? `
            <div class="alert alert-warning alert-sm py-1 px-2 mb-2">
              <i class="fas fa-clock me-1"></i>Downtime: <strong>${repair.downtime_hours} hours</strong>
            </div>
          ` : ''}

          <!-- Cost Breakdown -->
          <div class="border rounded p-2 mb-2" style="background:var(--bg-tertiary)">
            <small class="text-muted d-block mb-1"><strong>Cost Breakdown:</strong></small>
            <div class="row g-1 small">
              <div class="col-4">Labor: <strong>KSh ${parseFloat(repair.labor_cost).toFixed(2)}</strong></div>
              <div class="col-4">Parts: <strong>KSh ${parseFloat(repair.total_parts_cost).toFixed(2)}</strong></div>
              ${repair.additional_costs > 0 ? `<div class="col-4">Other: <strong>KSh ${parseFloat(repair.additional_costs).toFixed(2)}</strong></div>` : ''}
            </div>
          </div>

          <!-- Spare Parts -->
          ${repair.spare_parts && repair.spare_parts.length > 0 ? `
            <div class="mt-2">
              <small class="text-success fw-bold d-block mb-1"><i class="fas fa-tools me-1"></i>Spare Parts Used:</small>
              <div class="table-responsive">
                <table class="table table-sm table-bordered mb-0">
                  <thead class="table-light">
                    <tr>
                      <th class="small">Part</th>
                      <th class="small text-center">Qty</th>
                      <th class="small text-end">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${repair.spare_parts.map(sp => `
                      <tr>
                        <td class="small">${sp.name}</td>
                        <td class="small text-center">${sp.quantity}</td>
                        <td class="small text-end">KSh ${parseFloat(sp.total_cost).toFixed(2)}</td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            </div>
          ` : ''}
        </div>
      </div>
    `).join('');
  } else {
    repairHistoryList.innerHTML = `
      <div class="text-center text-muted py-5">
        <i class="fas fa-inbox fa-3x mb-3 opacity-50"></i>
        <p>No repair history found for this equipment.</p>
      </div>
    `;
  }

  // Populate calibration certificates
  const calibrationList = document.querySelector('.calibration-list');
  if (calibration_certificates && calibration_certificates.length > 0) {
    calibrationList.innerHTML = calibration_certificates.map(cert => `
      <div class="card mb-3 border-start border-success border-3">
        <div class="card-body p-3">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <div>
              <h6 class="mb-1 fw-bold text-success">
                <i class="fas fa-certificate me-1"></i>Cert #${cert.certificate_number}
              </h6>
              <small class="text-muted">
                <i class="fas fa-calendar me-1"></i>${formatDate(cert.date)}
              </small>
            </div>
            <span class="badge ${cert.overall_pass ? 'bg-success' : 'bg-warning'}">${cert.overall_pass ? 'PASS' : 'FAIL'}</span>
          </div>

          <p class="mb-2 small"><strong>Procedure:</strong> ${cert.procedure_name}</p>
          <p class="mb-2 small"><strong>Performed by:</strong> ${cert.performed_by}</p>
          <p class="mb-2 small"><strong>Next Due:</strong> ${formatDate(cert.next_due)}</p>

          ${cert.notes ? `<p class="mb-2 small text-muted"><em>${cert.notes}</em></p>` : ''}

          <a href="/calibration/sessions/${cert.id}/certificate/comprehensive/" target="_blank" class="btn btn-sm btn-outline-success w-100">
            <i class="fas fa-download me-1"></i>View Certificate
          </a>
        </div>
      </div>
    `).join('');
  } else {
    calibrationList.innerHTML = `
      <div class="text-center text-muted py-5">
        <i class="fas fa-certificate fa-3x mb-3 opacity-50"></i>
        <p>No calibration certificates found.</p>
      </div>
    `;
  }

  // Setup download buttons
  setupDownloadButtons(equipment.id, data);
}

// --- SETUP DOWNLOAD BUTTONS ---
function setupDownloadButtons(equipmentId, data) {
  const downloadAllBtn = document.getElementById('downloadAllHistory');
  const downloadRepairsBtn = document.getElementById('downloadRepairsBtn');
  const downloadCalibrationsBtn = document.getElementById('downloadCalibrationsBtn');

  if (downloadAllBtn) {
    downloadAllBtn.onclick = () => {
      window.open(`/machineReports/equipment/${equipmentId}/export/`, '_blank');
    };
  }

  if (downloadRepairsBtn) {
    downloadRepairsBtn.onclick = () => {
      window.open(`/machineReports/equipment/${equipmentId}/export/`, '_blank');
    };
  }

  if (downloadCalibrationsBtn) {
    downloadCalibrationsBtn.onclick = () => {
      // Download all calibration certificates
      if (data.calibration_certificates && data.calibration_certificates.length > 0) {
        data.calibration_certificates.forEach((cert, index) => {
          // Stagger the downloads slightly to avoid browser blocking
          setTimeout(() => {
            window.open(`/calibration/sessions/${cert.id}/certificate/comprehensive/`, '_blank');
          }, index * 100);
        });
      } else {
        alert('No calibration certificates available to download.');
      }
    };
  }
}

// --- UTILITIES ---
function getStatusClass(status) {
  switch (status) {
    case 'Working': return 'bg-success text-white';
    case 'Under repair': return 'bg-warning text-dark';
    case 'Not working': return 'bg-danger text-white';
    default: return 'bg-secondary text-white';
  }
}

function getStatusBadgeClass(status) {
  switch (status) {
    case 'Working': return 'bg-success';
    case 'Under repair': return 'bg-warning text-dark';
    case 'Not working': return 'bg-danger';
    case 'Due calibration': return 'bg-info';
    default: return 'bg-secondary';
  }
}

function getCookie(name) {
  let cookieValue = null;
  if (document.cookie && document.cookie !== '') {
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === (name + '=')) {
        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
        break;
      }
    }
  }
  return cookieValue;
}

function escapeHtml(text) {
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;'
  };
  return String(text).replace(/[&<>"']/g, m => map[m]);
}

function formatDate(dateString) {
  if (!dateString) return 'N/A';
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

// --- MANUFACTURER CHART ---
function renderManufacturerChart() {
  const canvas = document.getElementById('manufacturerChart');
  if (!canvas) {
    console.warn('Manufacturer chart canvas not found');
    return;
  }

  if (window.myChart) {
    console.log('Destroying existing chart instance');
    window.myChart.destroy();
    window.myChart = null;
  }

  const dataScript = document.getElementById('manufacturer-data');
  if (!dataScript) {
    console.warn('Manufacturer data script not found');
    return;
  }

  try {
    const textContent = dataScript.textContent.trim();

    if (!textContent) {
      console.warn('Manufacturer data is empty');
      return;
    }

    const rawData = JSON.parse(textContent);

    if (!rawData || typeof rawData !== 'object') {
      console.warn('Invalid manufacturer data format');
      return;
    }

    const labels = Object.keys(rawData);
    const repairs = Object.values(rawData).map(x => x.total_repairs || 0);

    if (labels.length === 0) {
      console.warn('No manufacturer data to display');
      canvas.parentElement.innerHTML = '<p class="text-center text-muted py-5">No manufacturer data available</p>';
      return;
    }

    const ctx = canvas.getContext('2d');
    window.myChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: 'Total Repairs',
          data: repairs,
          backgroundColor: '#3b82f6',
          borderColor: '#2563eb',
          borderWidth: 2,
          borderRadius: 8,
          hoverBackgroundColor: '#2563eb'
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: {
              font: {
                size: 13,
                weight: 'bold'
              }
            }
          },
          tooltip: {
            backgroundColor: 'rgba(0, 0, 0, 0.8)',
            padding: 12,
            titleFont: {
              size: 14,
              weight: 'bold'
            },
            bodyFont: {
              size: 13
            },
            borderColor: '#3b82f6',
            borderWidth: 1,
            callbacks: {
              afterLabel: function (context) {
                const manufacturer = labels[context.dataIndex];
                const data = rawData[manufacturer];
                return [
                  `Equipment: ${data.equipment_count}`,
                  `Avg Repair Cost: KSh ${data.avg_repair_cost.toFixed(2)}`,
                  `Downtime: ${data.total_downtime.toFixed(1)}hrs`
                ];
              }
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              precision: 0,
              font: {
                size: 12
              }
            },
            grid: {
              color: 'rgba(0, 0, 0, 0.05)'
            }
          },
          x: {
            grid: {
              display: false
            },
            ticks: {
              font: {
                size: 12
              }
            }
          }
        }
      }
    });

    console.log('Manufacturer chart rendered successfully');
  } catch (error) {
    console.error('Error rendering manufacturer chart:', error);
    console.error('Data content:', dataScript.textContent);
    canvas.parentElement.innerHTML = '<p class="text-center text-danger py-5">Error loading chart data</p>';
  }
}

// --- EXPORT MODAL ---
function setupExportModal() {
  const modal = document.getElementById('exportModal');
  const openBtn = document.getElementById('openExportModal');
  const closeBtn = modal?.querySelector('.btn-close');

  if (openBtn && modal) {
    openBtn.onclick = () => {
      modal.style.display = 'block';
    };
  }

  if (closeBtn && modal) {
    closeBtn.onclick = () => {
      modal.style.display = 'none';
    };
  }

  // Close on outside click
  if (modal) {
    window.onclick = (event) => {
      if (event.target === modal) {
        modal.style.display = 'none';
      }
    };
  }
}

// Dashboard filter form submission
document.addEventListener('DOMContentLoaded', () => {
  const filterForm = document.getElementById('dashboard-filter-form');
  if (filterForm) {
    filterForm.addEventListener('submit', (e) => {
      e.preventDefault();
      filterForm.submit();
    });
  }
});

// Clear filters function
function clearFilters() {
  const form = document.getElementById('dashboard-filter-form');
  if (form) {
    form.reset();
    form.submit();
  }
}

// Export full equipment list as PDF (respects current filters)
function exportEquipmentList() {
  const category = document.getElementById('category-filter')?.value || '';
  const search = document.getElementById('equipment-search')?.value || '';
  const params = new URLSearchParams();
  if (category) params.set('category', category);
  if (search) params.set('search', search);
  const url = `/machineReports/export-equipment-category-detailed-pdf/?${params.toString()}`;
  window.open(url, '_blank');
}
