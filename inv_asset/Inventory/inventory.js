// Global variables
let statusChart, departmentChart;
let currentDepartment = null;
let deleteUrl = '';
let djangoData = {};
let currentPage = 1;
let currentPerPage = 10;
let currentFilters = {
  department: '',
  search: '',
  status: '',
  workshop: '',
};
let isAddingDescription = false;
let paginationInitialized = false;

// Initialize app when DOM is loaded
document.addEventListener('DOMContentLoaded', function () {
  try {
    loadDjangoData();
    initializeSPA();
    initializeCharts();
    initializeEventListeners();
    initializeSearchAndFilter();
    initializePaginationSystem();
    loadSummaryData();

    // Load initial data for inventory section
    if (window.location.pathname.includes('inventory')) {
      setTimeout(() => loadEquipmentData(), 100);
    }

    console.log('Inventory management system initialized successfully');
  } catch (error) {
    console.error('Initialization error:', error);
    showNotification('Failed to initialize the application. Please refresh the page.', 'error');
  }
});

// Load Django data from JSON script
function loadDjangoData() {
  const dataScript = document.getElementById('django-data');
  if (dataScript) {
    try {
      djangoData = JSON.parse(dataScript.textContent);
      currentDepartment = djangoData.currentDepartment;

      // Initialize current filters from URL parameters
      const urlParams = new URLSearchParams(window.location.search);
      currentFilters = {
        department: urlParams.get('department') || currentDepartment || '',
        search: urlParams.get('search') || '',
        status: urlParams.get('status') || '',
        workshop: urlParams.get('workshop') || '',
      };

      // Get current page from URL
      currentPage = parseInt(urlParams.get('page')) || 1;
      currentPerPage = parseInt(urlParams.get('per_page')) || 10;

      console.log('Django data loaded:', djangoData);
      console.log('Current filters:', currentFilters);
      console.log('Current page:', currentPage);
    } catch (error) {
      console.error('Error parsing Django data:', error);
    }
  }
}

// Initialize comprehensive pagination system
function initializePaginationSystem() {
  if (paginationInitialized) {
    return; // Prevent duplicate initialization
  }

  // Single event listener for all pagination interactions
  document.addEventListener('click', handlePaginationClick);

  // Handle browser back/forward buttons
  window.addEventListener('popstate', function (e) {
    if (e.state && e.state.page) {
      currentPage = e.state.page;
      currentFilters = e.state.filters || currentFilters;
      loadEquipmentData();
    }
  });

  paginationInitialized = true;
  console.log('Pagination system initialized');
}

// Unified pagination click handler
function handlePaginationClick(e) {
  // Handle any pagination button click
  const paginationBtn = e.target.closest('[data-page]');
  if (!paginationBtn) return;

  e.preventDefault();
  e.stopPropagation();

  const targetPage = parseInt(paginationBtn.dataset.page);

  // Validate page number
  if (isNaN(targetPage) || targetPage < 1) {
    console.warn('Invalid page number:', targetPage);
    return;
  }

  // Don't reload if we're already on this page
  if (targetPage === currentPage) {
    return;
  }

  console.log(`Navigating to page ${targetPage} from page ${currentPage}`);

  // Update current page and load data
  currentPage = targetPage;
  loadEquipmentData();
}

// Initialize search and filter functionality
function initializeSearchAndFilter() {
  // Search input with debouncing
  const searchInput =
    document.getElementById('searchInput') || document.querySelector('input[name="search"]');
  if (searchInput) {
    searchInput.value = currentFilters.search;

    // Debounced search handler
    const debouncedSearch = debounce(function () {
      currentFilters.search = searchInput.value.trim();
      resetToFirstPage();
      loadEquipmentData();
    }, 500);

    searchInput.addEventListener('input', debouncedSearch);
    searchInput.addEventListener('keypress', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        currentFilters.search = searchInput.value.trim();
        resetToFirstPage();
        loadEquipmentData();
      }
    });
  }

  // Department filter
  const departmentFilter =
    document.getElementById('departmentFilter') ||
    document.getElementById('departmentFilterSelect') ||
    document.querySelector('select[name="department"]');
  if (departmentFilter) {
    departmentFilter.value = currentFilters.department;
    departmentFilter.addEventListener('change', function () {
      currentFilters.department = this.value;
      resetToFirstPage();
      loadEquipmentData();
    });
  }

  // Status filter
  const statusFilter =
    document.getElementById('statusFilter') || document.querySelector('select[name="status"]');
  if (statusFilter) {
    statusFilter.value = currentFilters.status;
    statusFilter.addEventListener('change', function () {
      currentFilters.status = this.value;
      resetToFirstPage();
      loadEquipmentData();
    });
  }

  // Workshop filter (for HODs)
  const workshopFilter =
    document.getElementById('workshopFilter') || document.querySelector('select[name="workshop"]');
  if (workshopFilter) {
    workshopFilter.value = currentFilters.workshop;
    workshopFilter.addEventListener('change', function () {
      currentFilters.workshop = this.value;
      // Clear department when workshop changes
      currentFilters.department = '';
      const deptFilter =
        document.getElementById('departmentFilter') ||
        document.getElementById('departmentFilterSelect') ||
        document.querySelector('select[name="department"]');
      if (deptFilter) deptFilter.value = '';

      resetToFirstPage();
      loadEquipmentData();
    });
  }

  // Per page selector
  const perPageSelect =
    document.getElementById('perPageSelect') || document.querySelector('select[name="per_page"]');
  if (perPageSelect) {
    perPageSelect.value = currentPerPage;
    perPageSelect.addEventListener('change', function () {
      const newPerPage = parseInt(this.value) || 10;
      if (newPerPage !== currentPerPage) {
        currentPerPage = newPerPage;
        resetToFirstPage();
        loadEquipmentData();
      }
    });
  }

  // Clear filters button
  const clearFiltersBtn = document.getElementById('clearFiltersBtn');
  if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener('click', clearAllFilters);
  }

  console.log('Search and filter initialization complete');
}

// Reset to first page (used when filters change)
function resetToFirstPage() {
  currentPage = 1;
  console.log('Reset to first page');
}

// Load equipment data with comprehensive pagination support
function loadEquipmentData() {
  if (!djangoData.urls || !djangoData.urls.inventory) {
    console.error('Inventory URL not found');
    return;
  }

  console.log(`Loading equipment data - Page: ${currentPage}, PerPage: ${currentPerPage}`);
  console.log('Current filters:', currentFilters);

  showLoading();

  // Build comprehensive query parameters
  const params = buildQueryParams();
  const url = `${djangoData.urls.inventory}?${params.toString()}`;

  console.log('Request URL:', url);

  // Make AJAX request with proper error handling
  fetch(url, {
    method: 'GET',
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
  })
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      return response.json();
    })
    .then(data => {
      console.log('Equipment data received:', data);

      if (data.success) {
        updateEquipmentTable(data.equipments || data.equipment || []);
        updatePaginationSystem(data.pagination || {});
        updateSummaryInfo(data.summary || {});
        updateFiltersDisplay(data.filters || {});
        updateBrowserURL(params);

        // Update page state for browser history
        const state = {
          page: currentPage,
          filters: { ...currentFilters },
          perPage: currentPerPage,
        };
        window.history.replaceState(state, '', `${window.location.pathname}?${params.toString()}`);
      } else {
        throw new Error(data.error || 'Failed to load equipment data');
      }
    })
    .catch(error => {
      console.error('Error loading equipment data:', error);
      showNotification(`Failed to load equipment data: ${error.message}`, 'error');
      showEmptyTable('Error loading data. Please try again.');
    })
    .finally(() => {
      hideLoading();
    });
}

// Build comprehensive query parameters
function buildQueryParams() {
  const params = new URLSearchParams();

  // Add all current filters
  Object.entries(currentFilters).forEach(([key, value]) => {
    if (value && value.trim) {
      value = value.trim();
    }
    if (value) {
      params.set(key, value);
    }
  });

  // Add pagination parameters
  if (currentPage > 1) {
    params.set('page', currentPage.toString());
  }

  if (currentPerPage !== 10) {
    params.set('per_page', currentPerPage.toString());
  }

  console.log('Built query params:', params.toString());
  return params;
}
// Update and render the pagination system
function updatePaginationSystem(paginationData) {
  console.log('Updating pagination system with data:', paginationData);

  if (!paginationData || Object.keys(paginationData).length === 0) {
    console.warn('No pagination data provided');
    hidePaginationControls();
    return;
  }

  // Update pagination info (e.g., "Showing 11–20 of 53")
  const paginationInfo = document.getElementById('paginationInfo');
  if (paginationInfo) {
    paginationInfo.textContent = `Showing ${paginationData.start_index}–${paginationData.end_index} of ${paginationData.total_items}`;
  }

  // Update results count
  const resultsCount = document.getElementById('resultsCount');
  if (resultsCount) {
    resultsCount.textContent = `${paginationData.total_items} results found`;
  }

  // Render pagination controls
  const paginationContainer = document.getElementById('paginationControls');
  if (paginationContainer) {
    paginationContainer.innerHTML = '';

    // Previous button
    const prevBtn = document.createElement('button');
    prevBtn.textContent = 'Previous';
    prevBtn.disabled = !paginationData.has_previous;
    prevBtn.addEventListener('click', () => {
      if (paginationData.has_previous) {
        loadEquipmentData(paginationData.previous_page_number);
      }
    });
    paginationContainer.appendChild(prevBtn);

    // Page buttons
    paginationData.page_range.forEach(page => {
      const pageBtn = document.createElement('button');
      pageBtn.textContent = page;
      if (page === paginationData.current_page) {
        pageBtn.classList.add('active');
      }
      pageBtn.addEventListener('click', () => {
        loadEquipmentData(page);
      });
      paginationContainer.appendChild(pageBtn);
    });

    // Next button
    const nextBtn = document.createElement('button');
    nextBtn.textContent = 'Next';
    nextBtn.disabled = !paginationData.has_next;
    nextBtn.addEventListener('click', () => {
      if (paginationData.has_next) {
        loadEquipmentData(paginationData.next_page_number);
      }
    });
    paginationContainer.appendChild(nextBtn);
  }
}

// Update pagination information display
function updatePaginationInfo(paginationData) {
  const paginationInfo = document.getElementById('paginationInfo');
  if (paginationInfo && paginationData.total_count !== undefined) {
    const startIndex = paginationData.start_index || 0;
    const endIndex = paginationData.end_index || 0;
    const totalCount = paginationData.total_count || 0;

    if (totalCount > 0) {
      paginationInfo.textContent = `Showing ${startIndex} to ${endIndex} of ${totalCount} entries`;
    } else {
      paginationInfo.textContent = 'No entries found';
    }
  }
}

// Update results count display
function updateResultsCount(paginationData) {
  const resultsCount = document.getElementById('resultsCount');
  if (resultsCount && paginationData.total_count !== undefined) {
    const startIndex = paginationData.start_index || 0;
    const endIndex = paginationData.end_index || 0;
    const totalCount = paginationData.total_count || 0;

    if (totalCount > 0) {
      resultsCount.textContent = `Showing ${startIndex}-${endIndex} of ${totalCount} equipment items`;
    } else {
      resultsCount.textContent = 'No equipment found';
    }
  }
}

// Update pagination controls with comprehensive support
function updatePaginationControls(paginationData) {
  // Find or create pagination container
  let paginationContainer = findOrCreatePaginationContainer();
  if (!paginationContainer) {
    console.error('Could not find or create pagination container');
    return;
  }

  const totalPages = paginationData.total_pages || 1;
  const currentPageNum = paginationData.current_page || currentPage || 1;
  const totalCount = paginationData.total_count || 0;

  // Hide pagination if no results or only one page
  if (totalCount === 0 || totalPages <= 1) {
    paginationContainer.innerHTML = '';
    paginationContainer.style.display = 'none';
    return;
  }

  // Show pagination container
  paginationContainer.style.display = 'block';

  // Generate comprehensive pagination HTML
  const paginationHTML = generatePaginationHTML(paginationData, currentPageNum, totalPages);
  paginationContainer.innerHTML = paginationHTML;

  console.log(`Pagination updated: Page ${currentPageNum} of ${totalPages}`);
}

// Find or create pagination container
function findOrCreatePaginationContainer() {
  // Try to find existing pagination container
  let container =
    document.getElementById('paginationWrapper') ||
    document.getElementById('paginationContainer') ||
    document.querySelector('.pagination-wrapper') ||
    document.querySelector('.pagination-container');

  if (container) {
    return container;
  }

  // Create pagination container if it doesn't exist
  const tableContainer =
    document.querySelector('.table-container') ||
    document.querySelector('.table-responsive') ||
    document.querySelector('#equipmentTableContainer') ||
    document.querySelector('.equipment-table-wrapper');

  if (tableContainer) {
    container = document.createElement('div');
    container.id = 'paginationWrapper';
    container.className = 'pagination-wrapper mt-4';
    tableContainer.appendChild(container);
    console.log('Created new pagination container');
    return container;
  }

  // Last resort: append to body
  console.warn('No suitable container found, appending to body');
  container = document.createElement('div');
  container.id = 'paginationWrapper';
  container.className = 'pagination-wrapper mt-4';
  document.body.appendChild(container);
  return container;
}

// Generate comprehensive pagination HTML
function generatePaginationHTML(paginationData, currentPageNum, totalPages) {
  let html = `
        <div class="pagination-container">
            <div class="pagination-info-row">
                <div class="pagination-summary">
                    <i class="bx fas fa-info-circle me-2"></i>
                    Showing <strong>${paginationData.start_index || 0}</strong> to
                    <strong>${paginationData.end_index || 0}</strong> of
                    <strong>${paginationData.total_count || 0}</strong> entries
                </div>
                <nav aria-label="Equipment pagination" class="pagination-nav">
                    <div class="pagination-controls">
    `;

  // First page button
  if (currentPageNum > 1) {
    html += `<button class="pagination-btn" data-page="1" title="First page">
                    <i class="bx bx-chevrons-left"></i>
                 </button>`;
  }

  // Previous page button
  if (paginationData.has_previous) {
    html += `<button class="pagination-btn" data-page="${
      paginationData.previous_page_number || currentPageNum - 1
    }" title="Previous page">
                    <i class="bx bx-chevron-left"></i>
                 </button>`;
  }

  // Page numbers with ellipsis support
  const pageRange = generateSmartPageRange(currentPageNum, totalPages);
  pageRange.forEach(page => {
    if (page === '...') {
      html += '<span class="pagination-btn disabled">…</span>';
    } else if (page === currentPageNum) {
      html += `<span class="pagination-btn active" title="Current page">${page}</span>`;
    } else {
      html += `<button class="pagination-btn" data-page="${page}" title="Go to page ${page}">${page}</button>`;
    }
  });

  // Next page button
  if (paginationData.has_next) {
    html += `<button class="pagination-btn" data-page="${
      paginationData.next_page_number || currentPageNum + 1
    }" title="Next page">
                    <i class="bx bx-chevron-right"></i>
                 </button>`;
  }

  // Last page button
  if (currentPageNum < totalPages) {
    html += `<button class="pagination-btn" data-page="${totalPages}" title="Last page">
                    <i class="bx bx-chevrons-right"></i>
                 </button>`;
  }

  html += `
                    </div>
                </nav>
            </div>
        </div>
    `;

  return html;
}

// Generate smart page range with ellipsis for better UX
function generateSmartPageRange(currentPage, totalPages, delta = 2) {
  const range = [];
  const left = Math.max(1, currentPage - delta);
  const right = Math.min(totalPages, currentPage + delta);

  // Always include first page
  if (left > 1) {
    range.push(1);
    if (left > 2) {
      range.push('...');
    }
  }

  // Include pages around current page
  for (let i = left; i <= right; i++) {
    range.push(i);
  }

  // Always include last page
  if (right < totalPages) {
    if (right < totalPages - 1) {
      range.push('...');
    }
    range.push(totalPages);
  }

  return range;
}

// Hide pagination controls
function hidePaginationControls() {
  const containers = [
    document.getElementById('paginationWrapper'),
    document.getElementById('paginationContainer'),
    document.querySelector('.pagination-wrapper'),
    document.querySelector('.pagination-container'),
  ].filter(Boolean);

  containers.forEach(container => {
    container.style.display = 'none';
  });
}

// Update equipment table with new data
function updateEquipmentTable(equipments) {
  const tableBody =
    document.getElementById('equipmentTableBody') ||
    document.querySelector('.equipment-table tbody') ||
    document.querySelector('#equipmentTable tbody');

  if (!tableBody) {
    console.warn('Equipment table body not found');
    return;
  }

  if (!equipments || equipments.length === 0) {
    showEmptyTable('No equipment found matching your criteria.');
    return;
  }

  // Clear existing content
  tableBody.innerHTML = '';

  // Calculate row number based on current page and per page
  const startRowNum = (currentPage - 1) * currentPerPage + 1;

  equipments.forEach((equipment, index) => {
    const row = document.createElement('tr');
    row.style.animationDelay = `${index * 0.05}s`;
    row.classList.add('fade-in');

    const rowNumber = startRowNum + index;
    const statusClass = getStatusClass(equipment.status);
    const statusBadge = `<span class="badge ${statusClass}">${equipment.status}</span>`;

    row.innerHTML = `
            <td>${rowNumber}</td>
            <td>${escapeHtml(equipment.description || 'Unknown')}</td>
            <td>${escapeHtml(equipment.manufacturer || 'N/A')}</td>
            <td>${escapeHtml(equipment.model || 'N/A')}</td>
            <td>${escapeHtml(equipment.serial || 'N/A')}</td>
            <td>${escapeHtml(equipment.department || 'N/A')}</td>
            <td>${statusBadge}</td>
            <td>
                <div class="btn-group" role="group">
                    <button type="button"
                            class="btn edit-button"
                            data-id="${equipment.id}"
                            data-description="${equipment.description_id}"
                            data-manufacturer="${equipment.manufacturer || ''}"
                            data-model="${escapeHtml(equipment.model || '')}"
                            data-serial="${escapeHtml(equipment.serial || '')}"
                            data-department="${equipment.department_id}"
                            data-status="${equipment.status}"
                            title="Edit Equipment">
                        Edit <i class="bx bx-edit"></i>
                    </button>
                    <button type="button"
                            class="btn btn-sm btn-outline-danger delete-button"
                            data-url="/Inventory/delete_equipment/${equipment.id}/"
                            title="Delete Equipment">
                        Delete <i class="bx fas fa-trash"></i>
                    </button>
                </div>
            </td>
        `;

    tableBody.appendChild(row);
  });
}

// Show empty table message
function showEmptyTable(message) {
  const tableBody =
    document.getElementById('equipmentTableBody') ||
    document.querySelector('.equipment-table tbody') ||
    document.querySelector('#equipmentTable tbody');

  if (!tableBody) return;

  tableBody.innerHTML = `
        <tr>
            <td colspan="8" class="text-center py-5">
                <div class="empty-state">
                    <i class="bx bx-search" style="font-size: 3rem; color: #6c757d; margin-bottom: 1rem;"></i>
                    <h5 class="text-muted">${message}</h5>
                    <p class="text-muted">Try adjusting your search criteria or filters.</p>
                </div>
            </td>
        </tr>
    `;

  // Hide pagination when no data
  hidePaginationControls();
}

// Clear all filters
function clearAllFilters() {
  // Reset filters (keep workshop for HODs if needed)
  const previousWorkshop = currentFilters.workshop;
  currentFilters = {
    department: '',
    search: '',
    status: '',
    workshop: previousWorkshop || '',
  };

  // Reset pagination
  currentPage = 1;

  // Reset form inputs
  const inputs = [
    { selector: '#searchInput, input[name="search"]', value: '' },
    {
      selector: '#departmentFilter, #departmentFilterSelect, select[name="department"]',
      value: '',
    },
    { selector: '#statusFilter, select[name="status"]', value: '' },
  ];

  inputs.forEach(({ selector, value }) => {
    const element = document.querySelector(selector);
    if (element) element.value = value;
  });

  // Reload data
  loadEquipmentData();

  showNotification('All filters cleared', 'info');
}

// Update browser URL without page reload
function updateBrowserURL(params) {
  const newURL = new URL(window.location.href);
  newURL.search = params.toString();

  if (newURL.href !== window.location.href) {
    const state = {
      page: currentPage,
      filters: { ...currentFilters },
      perPage: currentPerPage,
    };
    window.history.pushState(state, '', newURL.href);
  }
}

// SPA Navigation System
function initializeSPA() {
  const navItems = document.querySelectorAll('.nav-item');
  const sections = document.querySelectorAll('.content-section');

  navItems.forEach(item => {
    item.addEventListener('click', function (e) {
      e.preventDefault();

      const targetSection = this.dataset.section;
      if (!targetSection) return;

      // Update nav active state
      navItems.forEach(nav => nav.classList.remove('active'));
      this.classList.add('active');

      // Update section visibility
      sections.forEach(section => section.classList.remove('active'));
      const targetElement = document.getElementById(targetSection + 'Section');
      if (targetElement) {
        targetElement.classList.add('active');
        targetElement.classList.add('fade-in');
      }

      // Load data based on section
      switch (targetSection) {
        case 'summary':
          loadSummaryData();
          break;
        case 'analytics':
          updateChartData();
          break;
        case 'inventory':
          loadEquipmentData();
          break;
      }
    });
  });
}

// Initialize charts
function initializeCharts() {
  const statusCtx = document.getElementById('statusChart');
  const departmentCtx = document.getElementById('departmentChart');

  if (!statusCtx || !departmentCtx) {
    console.warn('Chart canvases not found');
    return;
  }

  // Status Chart (Doughnut)
  statusChart = new Chart(statusCtx.getContext('2d'), {
    type: 'doughnut',
    data: {
      labels: ['Working', 'Not Working', 'Under Repair'],
      datasets: [
        {
          data: [
            djangoData.statusSummary?.Working || 0,
            djangoData.statusSummary?.NotWorking || 0,
            djangoData.statusSummary?.UnderRepair || 0,
          ],
          backgroundColor: [
            'rgba(40, 167, 69, 0.8)',
            'rgba(220, 53, 69, 0.8)',
            'rgba(255, 193, 7, 0.8)',
          ],
          borderColor: ['rgba(40, 167, 69, 1)', 'rgba(220, 53, 69, 1)', 'rgba(255, 193, 7, 1)'],
          borderWidth: 2,
          hoverOffset: 10,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: '#495057',
            font: { size: 14 },
            padding: 20,
            usePointStyle: true,
          },
        },
        tooltip: {
          backgroundColor: 'rgba(0,0,0,0.8)',
          titleColor: 'white',
          bodyColor: 'white',
          cornerRadius: 10,
        },
      },
      animation: {
        animateScale: true,
        duration: 1000,
      },
    },
  });

  // Department Chart (Bar)
  departmentChart = new Chart(departmentCtx.getContext('2d'), {
    type: 'bar',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Equipment Count',
          data: [],
          backgroundColor: 'rgba(20, 141, 240, 0.95)',
          borderColor: 'rgba(70, 130, 180, 1)',
          borderWidth: 2,
          borderRadius: 10,
          borderSkipped: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: false,
          labels: {
            color: '#495057',
            font: { size: 14 },
          },
        },
        tooltip: {
          backgroundColor: 'rgba(0,0,0,0.8)',
          titleColor: 'white',
          bodyColor: 'white',
          cornerRadius: 10,
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            color: '#151516ff',
            font: { size: 12 },
          },
          grid: {
            color: 'rgba(0, 0, 0, 0.1)',
          },
        },
        x: {
          ticks: {
            color: '#495057',
            font: { size: 12 },
          },
          grid: {
            color: 'rgba(0, 0, 0, 0.1)',
          },
        },
      },
      animation: {
        duration: 1000,
        easing: 'easeOutQuart',
      },
    },
  });
}

// Initialize all event listeners
function initializeEventListeners() {
  // Sidebar toggle
  const sidebarBtn = document.getElementById('btn');
  if (sidebarBtn) {
    sidebarBtn.addEventListener('click', toggleSidebar);
  }

  // Dashboard controls
  const closeDashboard = document.getElementById('closeDashboard');
  if (closeDashboard) {
    closeDashboard.addEventListener('click', hideDashboard);
  }

  // Equipment form controls
  initializeFormControls();

  // Equipment table controls
  initializeTableControls();

  // Export functionality
  const exportBtn = document.getElementById('exportBtn');
  if (exportBtn) {
    exportBtn.addEventListener('click', handleExport);
  }

  // Refresh button
  document.addEventListener('click', function (e) {
    if (e.target.closest('#refreshBtn')) {
      refreshData();
    }
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', handleKeyboardShortcuts);

  // Auto-hide alerts
  autoHideAlerts();
}

// Sidebar toggle functionality
function toggleSidebar() {
  const sidebar = document.querySelector('.sidebar');
  const navbar = document.querySelector('.navbar');
  const mainContainer = document.querySelector('.main-container');

  if (sidebar) sidebar.classList.toggle('active');
  if (navbar) navbar.classList.toggle('active');
  if (mainContainer) mainContainer.classList.toggle('active');
}

// Dashboard functions
function showDashboard() {
  const overlay = document.getElementById('dashboardOverlay');
  if (!overlay) return;

  overlay.style.display = 'flex';
  document.body.style.overflow = 'hidden';

  setTimeout(() => {
    overlay.classList.add('show');
    updateChartData();
  }, 10);
}

function hideDashboard() {
  const overlay = document.getElementById('dashboardOverlay');
  if (!overlay) return;

  overlay.classList.remove('show');
  document.body.style.overflow = 'auto';

  setTimeout(() => {
    overlay.style.display = 'none';
  }, 300);
}

function updateChartData() {
  if (!djangoData.urls?.equipmentAnalytics) {
    console.error('Equipment analytics URL not found');
    return;
  }

  showLoading();

  // Build URL with current filters
  const params = buildQueryParams();
  const url = `${djangoData.urls.equipmentAnalytics}?${params.toString()}`;

  fetch(url)
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return response.json();
    })
    .then(data => {
      console.log('Chart data received:', data);

      // Update status chart
      if (statusChart && data.status_summary) {
        statusChart.data.datasets[0].data = [
          data.status_summary.Working || 0,
          data.status_summary['Not working'] || data.status_summary.Not_working || 0,
          data.status_summary['Under repair'] || data.status_summary.Under_repair || 0,
        ];
        statusChart.update('active');
      }

      // Update department chart
      if (departmentChart && data.department_counts) {
        departmentChart.data.labels = Object.keys(data.department_counts);
        departmentChart.data.datasets[0].data = Object.values(data.department_counts);
        departmentChart.update('active');
      }

      hideLoading();
    })
    .catch(error => {
      console.error('Error loading chart data:', error);
      showNotification('Failed to load chart data. Please try again.', 'error');
      hideLoading();
    });
}

// Summary functions
function loadSummaryData() {
  if (!djangoData.urls?.inventorySummary) {
    console.error('Inventory summary URL not found');
    return;
  }

  showLoading();

  const params = buildQueryParams();
  const url = `${djangoData.urls.inventorySummary}?${params.toString()}`;

  fetch(url)
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return response.json();
    })
    .then(data => {
      console.log('Summary data received:', data);
      updateSummaryCards(data.overall_totals || {});
      updateSummaryTable(data.summary_data || []);
      hideLoading();
    })
    .catch(error => {
      console.error('Error loading summary data:', error);
      showNotification('Failed to load summary data. Please try again.', 'error');
      hideLoading();
    });
}

function updateSummaryCards(totals) {
  const elements = {
    totalEquipment: document.getElementById('totalEquipment'),
    workingEquipment: document.getElementById('workingEquipment'),
    notWorkingEquipment: document.getElementById('notWorkingEquipment'),
    underRepairEquipment: document.getElementById('underRepairEquipment'),
  };

  if (elements.totalEquipment) {
    elements.totalEquipment.textContent = totals.total_equipment || 0;
  }
  if (elements.workingEquipment) {
    elements.workingEquipment.textContent = totals.total_working || 0;
  }
  if (elements.notWorkingEquipment) {
    elements.notWorkingEquipment.textContent = totals.total_not_working || 0;
  }
  if (elements.underRepairEquipment) {
    elements.underRepairEquipment.textContent = totals.total_under_repair || 0;
  }

  // Animate numbers
  Object.values(elements).forEach(el => {
    if (el) el.classList.add('slide-up');
  });
}

function updateSummaryTable(summaryData) {
  const tbody = document.getElementById('summaryTableBody');
  if (!tbody) return;

  tbody.innerHTML = '';

  if (!summaryData || summaryData.length === 0) {
    tbody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center text-muted">
                    <i class="bx fas fa-info-circle" style="font-size: 2rem; display: block; margin-bottom: 10px;"></i>
                    No equipment found matching the current filters.
                </td>
            </tr>
        `;
    return;
  }

  summaryData.forEach((item, index) => {
    const workingPercent =
      item.total_count > 0 ? Math.round((item.working_count / item.total_count) * 100) : 0;

    const row = document.createElement('tr');
    row.style.animationDelay = `${index * 0.1}s`;
    row.classList.add('fade-in');

    row.innerHTML = `
            <td><strong>${item.description__name || 'Unknown'}</strong></td>
            <td><span class="badge">${item.working_count || 0}</span></td>
            <td><span class="badge">${item.not_working_count || 0}</span></td>
            <td><span class="badge">${item.under_repair_count || 0}</span></td>
            <td><strong>${item.total_count || 0}</strong></td>
            <td>
                <div style="display: flex; align-items: center; gap: 10px;">
                    <div style="flex: 1; background: #e0e0e0; height: 8px; border-radius: 4px; overflow: hidden;">
                        <div style="width: ${workingPercent}%; height: 100%; background: linear-gradient(90deg, #28a745, #20c997); transition: width 0.5s ease;"></div>
                    </div>
                    <span style="font-weight: 600; color: #28a745;">${workingPercent}%</span>
                </div>
            </td>
        `;
    tbody.appendChild(row);
  });
}

// Update summary information
function updateSummaryInfo(summary) {
  if (!summary) return;

  const summaryElements = {
    totalEquipment: document.getElementById('totalEquipment'),
    workingCount: document.getElementById('workingCount'),
    notWorkingCount: document.getElementById('notWorkingCount'),
    underRepairCount: document.getElementById('underRepairCount'),
  };

  if (summaryElements.totalEquipment) {
    summaryElements.totalEquipment.textContent = summary.total || 0;
  }
  if (summaryElements.workingCount) {
    summaryElements.workingCount.textContent = summary.working || 0;
  }
  if (summaryElements.notWorkingCount) {
    summaryElements.notWorkingCount.textContent = summary.not_working || 0;
  }
  if (summaryElements.underRepairCount) {
    summaryElements.underRepairCount.textContent = summary.under_repair || 0;
  }

  // Update charts if visible
  if (statusChart) {
    statusChart.data.datasets[0].data = [
      summary.working || 0,
      summary.not_working || 0,
      summary.under_repair || 0,
    ];
    statusChart.update('active');
  }
}

// Update filters display (dropdowns, etc.)
function updateFiltersDisplay(filters) {
  if (!filters) return;

  // Update departments dropdown
  const departmentSelect =
    document.getElementById('departmentFilter') ||
    document.getElementById('departmentFilterSelect') ||
    document.querySelector('select[name="department"]');

  if (departmentSelect && filters.departments) {
    const currentValue = departmentSelect.value;
    departmentSelect.innerHTML = '<option value="">All Departments</option>';

    filters.departments.forEach(dept => {
      const option = document.createElement('option');
      option.value = dept.id;
      option.textContent = dept.name;
      if (dept.id.toString() === currentValue) {
        option.selected = true;
      }
      departmentSelect.appendChild(option);
    });
  }

  // Update status dropdown
  const statusSelect =
    document.getElementById('statusFilter') || document.querySelector('select[name="status"]');

  if (statusSelect && filters.statuses) {
    const currentValue = statusSelect.value;
    statusSelect.innerHTML = '<option value="">All Statuses</option>';

    filters.statuses.forEach(status => {
      const option = document.createElement('option');
      option.value = status;
      option.textContent = status;
      if (status === currentValue) {
        option.selected = true;
      }
      statusSelect.appendChild(option);
    });
  }
}

// Form management
function initializeFormControls() {
  // Add equipment form
  const addBtn = document.getElementById('add_new');
  const closeAddBtn = document.getElementById('closeAddEquipment');
  const addOverlay = document.getElementById('addEquipmentOverlay');
  const addForm = document.getElementById('addEquipmentForm');

  if (addBtn) addBtn.addEventListener('click', showAddEquipmentForm);
  if (closeAddBtn) closeAddBtn.addEventListener('click', hideAddEquipmentForm);
  if (addOverlay) addOverlay.addEventListener('click', hideAddEquipmentForm);
  if (addForm) {
    addForm.addEventListener('click', function (e) {
      e.stopPropagation();
    });
  }

  // Edit equipment form
  const closeEditBtn = document.getElementById('closeEditEquipment');
  const editOverlay = document.getElementById('editEquipmentOverlay');
  const editForm = document.getElementById('editEquipmentForm');

  if (closeEditBtn) closeEditBtn.addEventListener('click', hideEditEquipmentForm);
  if (editOverlay) editOverlay.addEventListener('click', hideEditEquipmentForm);
  if (editForm) {
    editForm.addEventListener('click', function (e) {
      e.stopPropagation();
    });
  }

  // Form submissions
  const addFormElement = document.getElementById('add_form');
  const editFormElement = document.getElementById('edit_form');

  if (addFormElement) {
    addFormElement.addEventListener('submit', function (e) {
      handleFormSubmission(this, e);
    });
  }

  if (editFormElement) {
    editFormElement.addEventListener('submit', function (e) {
      handleFormSubmission(this, e);
    });
  }
}

// Handle form submission with loading state
function handleFormSubmission(form, event) {
  const submitBtn = form.querySelector('button[type="submit"]');
  if (submitBtn) {
    submitBtn.classList.add('loading');
    submitBtn.disabled = true;

    // Re-enable button after 3 seconds as fallback
    setTimeout(() => {
      submitBtn.classList.remove('loading');
      submitBtn.disabled = false;
    }, 3000);
  }
}

// Table controls initialization
function initializeTableControls() {
  // Use event delegation for dynamically loaded content
  document.addEventListener('click', function (e) {
    // Edit button handler
    if (e.target.closest('.edit-button')) {
      e.preventDefault();
      const button = e.target.closest('.edit-button');
      const equipmentData = {
        id: button.dataset.id,
        description: button.dataset.description,
        manufacturer: button.dataset.manufacturer || '',
        model: button.dataset.model || '',
        serial: button.dataset.serial || '',
        department: button.dataset.department,
        status: button.dataset.status,
      };
      showEditEquipmentForm(equipmentData);
    }

    // Delete button handler
    if (e.target.closest('.delete-button')) {
      e.preventDefault();
      const button = e.target.closest('.delete-button');
      deleteUrl = button.getAttribute('data-url');
      if (deleteUrl) {
        const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
        modal.show();
      }
    }
  });

  // Delete confirmation
  const confirmDeleteBtn = document.getElementById('confirmDelete');
  if (confirmDeleteBtn) {
    confirmDeleteBtn.addEventListener('click', function () {
      if (deleteUrl) {
        showLoading();
        window.location.href = deleteUrl;
      }
    });
  }
}

// ================================
// Equipment Description Functions
// ================================

function toggleNewEquipmentDescriptionInput() {
  const group = document.getElementById('new-equipment-description-group');
  const input = document.getElementById('new_equipment_description');

  if (group.style.display === 'none' || group.style.display === '') {
    group.style.display = 'block';
    input.focus();
  } else {
    cancelNewEquipmentDescription();
  }
}

function cancelNewEquipmentDescription() {
  const group = document.getElementById('new-equipment-description-group');
  const input = document.getElementById('new_equipment_description');
  group.style.display = 'none';
  input.value = '';
}

function submitNewEquipmentDescription() {
  const input = document.getElementById('new_equipment_description');
  const select = document.getElementById('equipment_description_select');
  const url = select.dataset.urlCreateDesc;

  if (!input.value.trim()) {
    alert('Please enter an equipment description');
    return;
  }

  const formData = new FormData();
  formData.append('description_name', input.value.trim());
  formData.append(
    'csrfmiddlewaretoken',
    document.querySelector('[name=csrfmiddlewaretoken]').value,
  );

  fetch(url, {
    method: 'POST',
    body: formData,
  })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        const option = new Option(data.description_name, data.description_id);
        select.add(option);
        select.value = data.description_id;

        cancelNewEquipmentDescription();
        showTempMessage('Equipment description added successfully!', 'success');
      } else {
        alert('Error: ' + (data.error || 'Unknown error'));
      }
    })
    .catch(error => {
      console.error('Error:', error);
      alert('Error adding equipment description.');
    });
}

// ================================
// Manufacturer Functions
// ================================

function toggleNewManufacturerInput() {
  const group = document.getElementById('new-manufacturer-group');
  const input = document.getElementById('new_manufacturer');

  if (group.style.display === 'none' || group.style.display === '') {
    group.style.display = 'block';
    input.focus();
  } else {
    cancelNewManufacturer();
  }
}

function cancelNewManufacturer() {
  const group = document.getElementById('new-manufacturer-group');
  const input = document.getElementById('new_manufacturer');
  group.style.display = 'none';
  input.value = '';
}

function submitNewManufacturer() {
  const input = document.getElementById('new_manufacturer');
  const select = document.getElementById('manufacturer_select');
  const url = select.dataset.urlCreateManufacturer;

  if (!input.value.trim()) {
    alert('Please enter a manufacturer name');
    return;
  }

  const formData = new FormData();
  formData.append('manufacturer_name', input.value.trim());
  formData.append(
    'csrfmiddlewaretoken',
    document.querySelector('[name=csrfmiddlewaretoken]').value,
  );

  fetch(url, {
    method: 'POST',
    body: formData,
  })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        const option = new Option(data.manufacturer_name, data.manufacturer_id);
        select.add(option);
        select.value = data.manufacturer_id;

        cancelNewManufacturer();
        showTempMessage('Manufacturer added successfully!', 'success');
      } else {
        alert('Error: ' + (data.error || 'Unknown error'));
      }
    })
    .catch(error => {
      console.error('Error:', error);
      alert('Error adding manufacturer.');
    });
}

// ================================
// Keyboard Shortcuts
// ================================
document.addEventListener('DOMContentLoaded', function () {
  const descInput = document.getElementById('new_equipment_description');
  const manuInput = document.getElementById('new_manufacturer');

  if (descInput) {
    descInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitNewEquipmentDescription();
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        cancelNewEquipmentDescription();
      }
    });
  }

  if (manuInput) {
    manuInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitNewManufacturer();
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        cancelNewManufacturer();
      }
    });
  }
});

// ================================
// Utility: Temporary Alert Messages
// ================================
function showTempMessage(message, type) {
  const alertDiv = document.createElement('div');
  alertDiv.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
  alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
  alertDiv.innerHTML = `
    ${message}
    <button type="button" class="btn-close" onclick="this.parentElement.remove()"></button>
  `;
  document.body.appendChild(alertDiv);

  setTimeout(() => {
    if (alertDiv.parentElement) {
      alertDiv.remove();
    }
  }, 3000);
}
function toggleNewManufacturer() {
  const select = document.getElementById('manufacturer_select');
  const group = document.getElementById('new-manufacturer-group');
  const input = document.getElementById('new_manufacturer');

  // If user picks something from the dropdown, hide the "new" input
  if (select.value) {
    group.style.display = 'none';
    input.value = '';
  }
}
function toggleNewEquipmentDescription() {
  const select = document.getElementById('equipment_description_select');
  const group = document.getElementById('new-equipment-description-group');
  const input = document.getElementById('new_equipment_description');

  if (select.value) {
    group.style.display = 'none';
    input.value = '';
  }
}

// Equipment form functions
function showAddEquipmentForm() {
  const form = document.getElementById('addEquipmentForm');
  const overlay = document.getElementById('addEquipmentOverlay');

  if (form && overlay) {
    overlay.style.display = 'block';
    form.style.display = 'flex';
    document.body.style.overflow = 'hidden';

    setTimeout(() => {
      overlay.classList.add('show');
      form.classList.add('show');
    }, 10);
  }
}

function hideAddEquipmentForm() {
  const form = document.getElementById('addEquipmentForm');
  const overlay = document.getElementById('addEquipmentOverlay');

  if (form && overlay) {
    overlay.classList.remove('show');
    form.classList.remove('show');
    document.body.style.overflow = 'auto';

    setTimeout(() => {
      overlay.style.display = 'none';
      form.style.display = 'none';

      const formElement = document.getElementById('add_form');
      if (formElement) formElement.reset();
    }, 300);
  }
}

function showEditEquipmentForm(equipmentData) {
  if (!equipmentData) return;

  // Populate form fields
  const fields = {
    edit_description: equipmentData.description,
    edit_manufacturer: equipmentData.manufacturer,
    edit_model: equipmentData.model,
    edit_serial_number: equipmentData.serial,
    edit_department: equipmentData.department,
    edit_status: equipmentData.status,
  };

  Object.entries(fields).forEach(([id, value]) => {
    const element = document.getElementById(id);
    if (element) element.value = value || '';
  });

  // Set form action
  const form = document.getElementById('edit_form');
  if (form && equipmentData.id) {
    form.action = `/Inventory/edit_inventory/${equipmentData.id}/`;
  }

  // Show form
  const formContainer = document.getElementById('editEquipmentForm');
  const overlay = document.getElementById('editEquipmentOverlay');

  if (formContainer && overlay) {
    overlay.style.display = 'block';
    formContainer.style.display = 'flex';
    document.body.style.overflow = 'hidden';

    setTimeout(() => {
      overlay.classList.add('show');
      formContainer.classList.add('show');
    }, 10);
  }
}

function hideEditEquipmentForm() {
  const form = document.getElementById('editEquipmentForm');
  const overlay = document.getElementById('editEquipmentOverlay');

  if (form && overlay) {
    overlay.classList.remove('show');
    form.classList.remove('show');
    document.body.style.overflow = 'auto';

    setTimeout(() => {
      overlay.style.display = 'none';
      form.style.display = 'none';
    }, 300);
  }
}

// Export functionality
function handleExport() {
  if (!djangoData.urls?.exportExcel) {
    console.error('Export URL not found');
    showNotification('Export functionality is not available', 'error');
    return;
  }

  showLoading();

  const params = buildQueryParams();
  let exportUrl = djangoData.urls.exportExcel;
  if (params.toString()) {
    exportUrl += '?' + params.toString();
  }

  const link = document.createElement('a');
  link.href = exportUrl;
  link.download = 'equipment_inventory.xlsx';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  hideLoading();
  showNotification('Export started. Download will begin shortly.', 'info');
}

// Utility functions
function showLoading() {
  const spinner =
    document.getElementById('loadingSpinner') || document.querySelector('.loading-spinner');
  if (spinner) {
    spinner.style.display = 'block';
  }

  const controls = document.querySelectorAll('input, select, button');
  controls.forEach(control => {
    control.style.opacity = '0.6';
    control.style.pointerEvents = 'none';
  });
}

function hideLoading() {
  const spinner =
    document.getElementById('loadingSpinner') || document.querySelector('.loading-spinner');
  if (spinner) {
    spinner.style.display = 'none';
  }

  const controls = document.querySelectorAll('input, select, button');
  controls.forEach(control => {
    control.style.opacity = '1';
    control.style.pointerEvents = 'auto';
  });
}

function getCSRFToken() {
  const methods = [
    () => document.querySelector('[name=csrfmiddlewaretoken]')?.value,
    () => document.querySelector('meta[name=csrf-token]')?.getAttribute('content'),
    () => document.querySelector('input[name=csrfmiddlewaretoken]')?.value,
  ];

  for (const method of methods) {
    try {
      const token = method();
      if (token) return token;
    } catch (e) {
      continue;
    }
  }

  console.warn('CSRF token not found');
  return '';
}

// Enhanced notification system
function showNotification(message, type = 'info', duration = 5000) {
  const existingNotifications = document.querySelectorAll('.custom-notification');
  existingNotifications.forEach(n => n.remove());

  const notification = document.createElement('div');
  notification.className = `custom-notification alert alert-${type}`;
  notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 10001;
        min-width: 300px;
        max-width: 400px;
        border-radius: 10px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        transform: translateX(100%);
        transition: transform 0.3s ease;
        padding: 15px 20px;
        display: flex;
        align-items: center;
        gap: 10px;
    `;

  const icons = {
    success: 'fas fa-check-circle',
    error: 'bx-error-circle',
    warning: 'bx-error',
    info: 'fas fa-info-circle',
  };

  notification.innerHTML = `
        <i class="bx ${icons[type] || icons.info}" style="font-size: 1.2rem;"></i>
        <span style="flex: 1;">${message}</span>
        <button onclick="this.parentElement.remove()" style="background: none; border: none; color: inherit; font-size: 1.2rem; cursor: pointer;">
            <i class="bx fas fa-times"></i>
        </button>
    `;

  document.body.appendChild(notification);

  setTimeout(() => {
    notification.style.transform = 'translateX(0)';
  }, 10);

  setTimeout(() => {
    if (notification.parentNode) {
      notification.style.transform = 'translateX(100%)';
      setTimeout(() => notification.remove(), 300);
    }
  }, duration);
}

// Utility functions
function getStatusClass(status) {
  switch (status) {
    case 'Working':
      return 'badge-success';
    case 'Not working':
      return 'badge-danger';
    case 'Under repair':
      return 'badge-warning';
    default:
      return 'badge-secondary';
  }
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Keyboard shortcuts
function handleKeyboardShortcuts(e) {
  if (e.key === 'Escape') {
    const dashboardOverlay = document.getElementById('dashboardOverlay');
    if (dashboardOverlay && dashboardOverlay.classList.contains('show')) {
      hideDashboard();
      return;
    }

    hideAddEquipmentForm();
    hideEditEquipmentForm();
  }

  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault();
    showAddEquipmentForm();
  }

  if ((e.ctrlKey || e.metaKey) && e.key === 'd') {
    e.preventDefault();
    showDashboard();
  }

  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    const searchInput =
      document.getElementById('searchInput') || document.querySelector('input[name="search"]');
    if (searchInput) {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
  }

  if ((e.ctrlKey || e.metaKey) && e.key === 'r') {
    if (window.location.pathname.includes('inventory')) {
      e.preventDefault();
      loadEquipmentData();
    }
  }
}

// Auto-hide alerts
function autoHideAlerts() {
  setTimeout(function () {
    const alerts = document.querySelectorAll('.alert:not(.custom-notification)');
    alerts.forEach(alert => {
      if (!alert.classList.contains('alert-dismissible')) {
        alert.style.transition = 'opacity 0.5s ease';
        alert.style.opacity = '0';
        setTimeout(() => {
          if (alert.parentNode) {
            alert.remove();
          }
        }, 500);
      }
    });
  }, 5000);
}

// Refresh data functionality
function refreshData() {
  showLoading();

  const activeSection = document.querySelector('.content-section.active');
  if (activeSection) {
    const sectionId = activeSection.id;

    if (sectionId.includes('inventory')) {
      loadEquipmentData();
    } else if (sectionId.includes('summary')) {
      loadSummaryData();
    } else if (sectionId.includes('analytics')) {
      updateChartData();
    } else {
      location.reload();
    }
  } else {
    location.reload();
  }
}

// Debounce function for search operations
function debounce(func, wait, immediate) {
  let timeout;
  return function executedFunction() {
    const context = this;
    const args = arguments;
    const later = function () {
      timeout = null;
      if (!immediate) func.apply(context, args);
    };
    const callNow = immediate && !timeout;
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
    if (callNow) func.apply(context, args);
  };
}

// Error handling
window.addEventListener('error', function (e) {
  console.error('Global error caught:', e.error);
  showNotification(
    'An unexpected error occurred. Please refresh the page if issues persist.',
    'error',
  );
});

window.addEventListener('unhandledrejection', function (e) {
  console.error('Unhandled promise rejection:', e.reason);
  showNotification('A network error occurred. Please check your connection.', 'error');
});

// Global exports for external access
window.InventoryManager = {
  showDashboard,
  hideDashboard,
  showAddEquipmentForm,
  hideAddEquipmentForm,
  showEditEquipmentForm,
  hideEditEquipmentForm,
  loadSummaryData,
  updateChartData,
  loadEquipmentData,
  clearAllFilters,
  refreshData,
  showNotification,
  resetToFirstPage,
  buildQueryParams,
  updatePaginationSystem,
};

// jQuery integration for legacy support
$(document).ready(function () {
  if (typeof djangoData.urls !== 'undefined' && djangoData.urls.createDescription) {
    const createDescriptionUrl = djangoData.urls.createDescription;
    const csrfToken = getCSRFToken();

    $('#addDescriptionBtn').on('click', function () {
      const newDesc = $('#new_equipment_description').val().trim();

      if (!newDesc) {
        showNotification('Please enter a description name.', 'warning');
        return;
      }

      $.ajax({
        url: createDescriptionUrl,
        type: 'POST',
        data: {
          description_name: newDesc,
          csrfmiddlewaretoken: csrfToken,
        },
        success: function (response) {
          if (response.success) {
            $('#equipment_description_select').append(
              `<option value="${response.description_id}" selected>${response.description_name}</option>`,
            );
            $('#new_equipment_description').val('');
            $('#descriptionSuccessMessage').fadeIn().delay(2000).fadeOut();
            showNotification('Equipment description added successfully!', 'success');
          } else {
            showNotification('Error: ' + response.error, 'error');
          }
        },
        error: function (xhr) {
          showNotification('Request failed: ' + xhr.responseText, 'error');
        },
      });
    });
  }
});

// CSS injection for enhanced pagination styling
const enhancedPaginationCSS = `
.pagination-container {
    background: #f8f9fa;
    border-radius: 10px;
    padding: 20px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
}

.pagination-info-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 15px;
}

.pagination-summary {
    color: #495057;
    font-size: 14px;
    display: flex;
    align-items: center;
}

.pagination-controls {
    display: flex;
    gap: 5px;
    align-items: center;
    flex-wrap: wrap;
}

.pagination-btn {
    padding: 10px 15px;
    border: 2px solid #dee2e6;
    background: white;
    color: #495057;
    text-decoration: none;
    cursor: pointer;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 500;
    transition: all 0.3s ease;
    min-width: 45px;
    text-align: center;
    display: inline-flex;
    align-items: center;
    justify-content: center;
}

.pagination-btn:hover:not(.disabled):not(.active) {
    background: #e3f2fd;
    border-color: #2196f3;
    color: #1976d2;
    transform: translateY(-1px);
    box-shadow: 0 4px 8px rgba(33,150,243,0.3);
}

.pagination-btn.active {
    background: linear-gradient(135deg, #2196f3, #1976d2);
    color: white;
    border-color: #1976d2;
    box-shadow: 0 4px 12px rgba(33,150,243,0.4);
    transform: translateY(-1px);
}

.pagination-btn.disabled {
    background: #f8f9fa;
    color: #6c757d;
    cursor: not-allowed;
    opacity: 0.6;
}

.pagination-btn i {
    font-size: 16px;
}

@media (max-width: 768px) {
    .pagination-info-row {
        flex-direction: column;
        text-align: center;
    }

    .pagination-controls {
        justify-content: center;
    }

    .pagination-btn {
        padding: 8px 12px;
        min-width: 40px;
    }
}

.fade-in {
    animation: fadeIn 0.5s ease-in;
}

@keyframes fadeIn {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
}

.slide-up {
    animation: slideUp 0.6s ease-out;
}

@keyframes slideUp {
    from { transform: translateY(20px); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
}

.empty-state {
    padding: 40px 20px;
    text-align: center;
}

.empty-state i {
    color: #6c757d;
    margin-bottom: 15px;
}

.empty-state h5 {
    color: #495057;
    margin-bottom: 10px;
}

.empty-state p {
    color: #6c757d;
    font-size: 14px;
}
`;

// Inject enhanced CSS if it doesn't exist
if (!document.getElementById('enhanced-pagination-css')) {
  const style = document.createElement('style');
  style.id = 'enhanced-pagination-css';
  style.textContent = enhancedPaginationCSS;
  document.head.appendChild(style);
}

// Enhanced page navigation with URL state management
function navigateToPage(pageNumber) {
  if (pageNumber === currentPage || pageNumber < 1) {
    return;
  }

  console.log(`Navigating from page ${currentPage} to page ${pageNumber}`);
  currentPage = pageNumber;

  // Update URL immediately to reflect navigation
  const params = buildQueryParams();
  const newURL = `${window.location.pathname}?${params.toString()}`;
  window.history.pushState(
    {
      page: currentPage,
      filters: { ...currentFilters },
      perPage: currentPerPage,
    },
    '',
    newURL,
  );

  // Load new data
  loadEquipmentData();
}

// Enhanced filter change handlers that preserve pagination state
function handleFilterChange(filterType, value) {
  console.log(`Filter changed: ${filterType} = ${value}`);

  // Update the specific filter
  currentFilters[filterType] = value;

  // Reset to first page when filters change (except for per_page changes)
  if (filterType !== 'per_page') {
    resetToFirstPage();
  }

  // Reload data with new filters
  loadEquipmentData();
}

// Enhanced search handler with better debouncing
const createDebouncedSearch = () => {
  let timeoutId;
  return function (searchValue) {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => {
      currentFilters.search = searchValue.trim();
      resetToFirstPage();
      loadEquipmentData();
    }, 500);
  };
};

const debouncedSearch = createDebouncedSearch();

// Enhanced table update with better row numbering across pages
function updateEquipmentTableEnhanced(equipments, paginationData) {
  const tableBody =
    document.getElementById('equipmentTableBody') ||
    document.querySelector('.equipment-table tbody') ||
    document.querySelector('#equipmentTable tbody');

  if (!tableBody) {
    console.warn('Equipment table body not found');
    return;
  }

  if (!equipments || equipments.length === 0) {
    showEmptyTable('No equipment found matching your criteria.');
    return;
  }

  // Clear existing rows
  tableBody.innerHTML = '';

  // Calculate starting row number based on pagination
  const startIndex = paginationData?.start_index || (currentPage - 1) * currentPerPage + 1;

  equipments.forEach((equipment, index) => {
    const row = document.createElement('tr');
    row.style.animationDelay = `${index * 0.05}s`;
    row.classList.add('fade-in');

    const rowNumber = startIndex + index;
    const statusClass = getStatusClass(equipment.status);
    const statusBadge = `<span class="badge ${statusClass}">${escapeHtml(equipment.status)}</span>`;

    row.innerHTML = `
            <td><strong>${rowNumber}</strong></td>
            <td>${escapeHtml(equipment.description || 'Unknown')}</td>
            <td>${escapeHtml(equipment.manufacturer || 'N/A')}</td>
            <td>${escapeHtml(equipment.model || 'N/A')}</td>
            <td>${escapeHtml(equipment.serial || 'N/A')}</td>
            <td>${escapeHtml(equipment.department || 'N/A')}</td>
            <td>${statusBadge}</td>
            <td>
                <div class="btn-group" role="group">
                    <button type="button"
                            class="btn btn-sm btn-outline-primary edit-button"
                            data-id="${equipment.id}"
                            data-description="${equipment.description_id || ''}"
                            data-manufacturer="${escapeHtml(equipment.manufacturer || '')}"
                            data-model="${escapeHtml(equipment.model || '')}"
                            data-serial="${escapeHtml(equipment.serial || '')}"
                            data-department="${equipment.department_id || ''}"
                            data-status="${escapeHtml(equipment.status || '')}"
                            title="Edit Equipment">
                        <i class="bx bx-edit"></i>
                    </button>
                    <button type="button"
                            class="btn btn-sm btn-outline-danger delete-button"
                            data-url="/Inventory/delete_equipment/${equipment.id}/"
                            title="Delete Equipment">
                        <i class="bx fas fa-trash"></i>
                    </button>
                </div>
            </td>
        `;

    tableBody.appendChild(row);
  });
}

function loadEquipmentDataEnhanced() {
  if (!djangoData.urls?.inventory) {
    console.error('Inventory URL not found in Django data');
    showNotification('Configuration error: Inventory URL not found', 'error');
    return Promise.reject(new Error('Inventory URL not found'));
  }

  console.log(`Loading equipment data - Page: ${currentPage}, PerPage: ${currentPerPage}`);
  console.log('Active filters:', currentFilters);

  // Show loading state
  showLoading();

  // Build query parameters
  const params = buildQueryParams();
  const url = `${djangoData.urls.inventory}?${params.toString()}`;

  console.log('Fetching from URL:', url);

  // Create abort controller for request cancellation
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000); // 30 second timeout

  return fetch(url, {
    method: 'GET',
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'Cache-Control': 'no-cache',
    },
    signal: controller.signal,
  })
    .then(response => {
      clearTimeout(timeoutId);

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error('Equipment data not found. Please check your filters.');
        } else if (response.status >= 500) {
          throw new Error('Server error occurred. Please try again later.');
        } else {
          throw new Error(`Request failed with status ${response.status}`);
        }
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        throw new Error('Invalid response format received from server');
      }

      return response.json();
    })
    .then(data => {
      console.log('Equipment data response:', data);

      if (!data || typeof data !== 'object') {
        throw new Error('Invalid data format received');
      }

      if (data.success === false) {
        throw new Error(data.error || data.message || 'Failed to load equipment data');
      }

      // Handle successful response
      const equipments = data.equipments || data.equipment || data.results || [];
      const paginationData = data.pagination || {};
      const summaryData = data.summary || {};
      const filtersData = data.filters || {};

      // Update all components
      updateEquipmentTableEnhanced(equipments, paginationData);
      updatePaginationSystem(paginationData);
      updateSummaryInfo(summaryData);
      updateFiltersDisplay(filtersData);

      // Update browser URL and state
      updateBrowserURL(params);

      // Log successful load
      console.log(
        `Successfully loaded ${equipments.length} equipment items for page ${currentPage}`,
      );

      // Show success message if no results
      if (equipments.length === 0) {
        const hasActiveFilters = Object.values(currentFilters).some(
          filter => filter && filter.trim(),
        );
        if (hasActiveFilters) {
          showNotification('No equipment found matching your search criteria.', 'info');
        }
      }

      return data; // Return the data for chaining
    })
    .catch(error => {
      clearTimeout(timeoutId);

      console.error('Error loading equipment data:', error);

      // Handle different error types
      if (error.name === 'AbortError') {
        showNotification(
          'Request timed out. Please check your connection and try again.',
          'warning',
        );
      } else if (
        error.message.includes('Failed to fetch') ||
        error.message.includes('NetworkError')
      ) {
        showNotification('Network error. Please check your internet connection.', 'error');
      } else {
        showNotification(`Failed to load equipment data: ${error.message}`, 'error');
      }

      // Show error state in table
      showEmptyTable('Error loading data. Please refresh the page and try again.');

      // Hide pagination on error
      hidePaginationControls();

      // Re-throw error for error recovery system
      throw error;
    })
    .finally(() => {
      hideLoading();
    });
}
// Attach click listeners for AJAX pagination
document.addEventListener('DOMContentLoaded', function () {
  const paginationWrapper = document.getElementById('paginationWrapper');

  if (paginationWrapper) {
    paginationWrapper.addEventListener('click', function (e) {
      const btn = e.target.closest('.ajax-page-btn');
      if (btn) {
        e.preventDefault();
        const page = parseInt(btn.dataset.page);

        if (!isNaN(page)) {
          currentPage = page; // keep global state in sync
          loadEquipmentDataEnhanced(); // this will reload table + pagination
        }
      }
    });
  }
});

// Build pagination HTML dynamically from backend JSON
function updatePaginationSystem(paginationData) {
  const wrapper = document.getElementById('paginationWrapper');
  if (!wrapper) return;

  if (!paginationData || paginationData.total_count === 0) {
    wrapper.innerHTML = '';
    return;
  }

  let html = `
    <div class="pagination-wrapper d-flex justify-content-between align-items-center mt-4 p-3 bg-light rounded">
      <div class="pagination-info">
        <i class="bx fas fa-info-circle me-1"></i>
        Showing <strong>${paginationData.start_index}</strong> to
        <strong>${paginationData.end_index}</strong>
        of <strong>${paginationData.total_count}</strong> entries
      </div>

      <nav aria-label="Equipment pagination">
        <div class="pagination-controls d-flex gap-1">
  `;

  // Previous buttons
  if (paginationData.has_previous) {
    html += `
      <button class="pagination-btn ajax-page-btn" data-page="1" title="First page">
        <i class="bx bx-chevrons-left"></i>
      </button>
      <button class="pagination-btn ajax-page-btn" data-page="${paginationData.previous_page_number}" title="Previous page">
        <i class="bx bx-chevron-left"></i>
      </button>
    `;
  }

  // Page numbers
  paginationData.page_range.forEach(num => {
    if (num === paginationData.current_page) {
      html += `<span class="pagination-btn active" title="Current page">${num}</span>`;
    } else if (num === '…' || num === '...') {
      html += `<span class="pagination-ellipsis">…</span>`;
    } else {
      html += `<button class="pagination-btn ajax-page-btn" data-page="${num}" title="Go to page ${num}">${num}</button>`;
    }
  });

  // Next buttons
  if (paginationData.has_next) {
    html += `
      <button class="pagination-btn ajax-page-btn" data-page="${paginationData.next_page_number}" title="Next page">
        <i class="bx bx-chevron-right"></i>
      </button>
      <button class="pagination-btn ajax-page-btn" data-page="${paginationData.num_pages}" title="Last page">
        <i class="bx bx-chevrons-right"></i>
      </button>
    `;
  }

  html += `
        </div>
      </nav>
    </div>
  `;

  wrapper.innerHTML = html;
}

// Enhanced pagination system with better responsiveness
function initializeResponsivePagination() {
  // Add resize listener for responsive pagination
  let resizeTimeout;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
      // Refresh pagination display on significant size changes
      const paginationContainer = document.getElementById('paginationWrapper');
      if (paginationContainer && paginationContainer.innerHTML.trim()) {
        // Re-render pagination with current data
        const currentPaginationData = {
          current_page: currentPage,
          total_pages: Math.ceil(
            (document.querySelectorAll('#equipmentTableBody tr').length || currentPerPage) /
              currentPerPage,
          ),
          has_previous: currentPage > 1,
          has_next:
            currentPage <
            Math.ceil(
              (document.querySelectorAll('#equipmentTableBody tr').length || currentPerPage) /
                currentPerPage,
            ),
          previous_page_number: currentPage - 1,
          next_page_number: currentPage + 1,
        };
        updatePaginationControls(currentPaginationData);
      }
    }, 250);
  });
}

// Initialize responsive pagination
document.addEventListener('DOMContentLoaded', function () {
  setTimeout(initializeResponsivePagination, 1000);
});
function initializeErrorRecovery() {
  let retryCount = 0;
  const maxRetries = 3;

  window.retryLoadEquipmentData = function () {
    if (retryCount < maxRetries) {
      retryCount++;
      console.log(`Retrying equipment data load (attempt ${retryCount}/${maxRetries})`);
      showNotification(`Retrying... (${retryCount}/${maxRetries})`, 'info', 2000);

      setTimeout(() => {
        loadEquipmentData()
          .then(() => {
            retryCount = 0; // Reset on success
            showNotification('Data loaded successfully!', 'success');
          })
          .catch(error => {
            console.error('Retry failed:', error);
            if (retryCount >= maxRetries) {
              showNotification('Maximum retry attempts reached. Please refresh the page.', 'error');

              // Offer manual refresh option
              const refreshBtn = document.createElement('button');
              refreshBtn.textContent = 'Refresh Page';
              refreshBtn.className = 'btn btn-primary mt-2';
              refreshBtn.onclick = () => location.reload();

              const tableContainer =
                document.querySelector('.table-container') ||
                document.querySelector('#equipmentTableContainer');
              if (tableContainer) {
                const existingBtn = tableContainer.querySelector('button');
                if (!existingBtn) {
                  tableContainer.appendChild(refreshBtn);
                }
              }
            }
          });
      }, 1000 * retryCount); // Exponential backoff
    }
  };
}

// Performance monitoring
function initializePerformanceMonitoring() {
  let loadStartTime;

  const originalShowLoading = showLoading;
  const originalHideLoading = hideLoading;

  showLoading = function () {
    loadStartTime = performance.now();
    originalShowLoading();
  };

  hideLoading = function () {
    if (loadStartTime) {
      const loadTime = performance.now() - loadStartTime;
      console.log(`Data load completed in ${loadTime.toFixed(2)}ms`);

      // Show performance warning if load takes too long
      if (loadTime > 5000) {
        console.warn('Slow data load detected:', loadTime + 'ms');
        showNotification(
          'Data loading is slower than expected. This might be due to network conditions.',
          'warning',
          3000,
        );
      }
    }
    originalHideLoading();
  };
}

// Initialize all enhanced features
document.addEventListener('DOMContentLoaded', function () {
  setTimeout(() => {
    initializeErrorRecovery();
    initializePerformanceMonitoring();
    console.log('Enhanced pagination features initialized');
  }, 500);
});

// Final exports with enhanced functionality
window.EnhancedInventoryManager = {
  ...window.InventoryManager,
  navigateToPage,
  handleFilterChange,
  debouncedSearch,
  updateEquipmentTableEnhanced,
  loadEquipmentDataEnhanced,
  retryLoadEquipmentData: () => window.retryLoadEquipmentData?.(),
};

console.log('Enhanced inventory management system with robust pagination loaded successfully');
