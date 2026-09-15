// ==========================================
// PPM DASHBOARD JAVASCRIPT
// Version: 5.0 - Fully Standalone
// All logic lives here — no inline JS in HTML
// ==========================================

// ============================================
// CHART.JS v3 COMPATIBILITY PATCH
// Chart.js v3 removed "horizontalBar" type.
// Intercept any Chart() call using it and
// transparently rewrite to bar + indexAxis:'y'
// ============================================
(function () {
  if (typeof Chart === 'undefined') return;

  var _OrigChart = Chart;

  function PatchedChart(ctx, config) {
    if (config && config.type === 'horizontalBar') {
      config.type = 'bar';
      config.options = config.options || {};
      config.options.indexAxis = 'y';
      config.options.responsive = config.options.responsive !== false;
    }
    return new _OrigChart(ctx, config);
  }

  // Copy static members so Chart.register, Chart.defaults etc. still work
  Object.keys(_OrigChart).forEach(function (k) {
    PatchedChart[k] = _OrigChart[k];
  });
  PatchedChart.prototype = _OrigChart.prototype;

  window.Chart = PatchedChart;
})();

// ============================================
// MODERN DEPARTMENT DROPDOWN
// Global functions called via onclick in HTML
// ============================================

/**
 * Toggle the custom department dropdown open/closed.
 * Called via: onclick="toggleDeptDropdown(event)"
 */
window.toggleDeptDropdown = function (e) {
  e.stopPropagation();
  var dd = document.getElementById('deptDropdown');
  if (!dd) return;

  var opening = !dd.classList.contains('open');
  dd.classList.toggle('open', opening);

  if (opening) {
    setTimeout(function () {
      var inp = document.getElementById('deptSearchInput');
      if (inp) inp.focus();
    }, 60);
  }
};

/**
 * Live-filter department list items by name.
 * Called via: oninput="filterDepts(this.value)"
 */
window.filterDepts = function (q) {
  var items = document.querySelectorAll('#deptList .ppm-dept-item');
  var term = q.toLowerCase().trim();
  var anyVisible = false;

  items.forEach(function (item) {
    var name = item.dataset.name || '';
    var visible = !term || name.includes(term);
    item.style.display = visible ? '' : 'none';
    if (visible) anyVisible = true;
  });

  var noRes = document.getElementById('deptNoResults');
  if (!anyVisible) {
    if (!noRes) {
      noRes = document.createElement('div');
      noRes.id = 'deptNoResults';
      noRes.className = 'ppm-dept-no-results';
      noRes.innerHTML = '<i class="fas fa-search me-2 opacity-50"></i>No departments found';
      document.getElementById('deptList').appendChild(noRes);
    }
  } else if (noRes) {
    noRes.remove();
  }
};

// Close dropdown when clicking outside
document.addEventListener('click', function (e) {
  var dd = document.getElementById('deptDropdown');
  if (dd && !dd.contains(e.target)) {
    dd.classList.remove('open');
  }
});

// Close dropdown on Escape key
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    var dd = document.getElementById('deptDropdown');
    if (dd) dd.classList.remove('open');
  }
});

// ============================================
// MAIN DASHBOARD — jQuery document ready
// ============================================

$(document).ready(function () {
  console.log('🚀 PPM Dashboard initializing...');

  // ============================================
  // 1. SPA NAVIGATION SYSTEM
  // ============================================

  function initSPANavigation() {
    $('.spa-nav-item').on('click', function (e) {
      e.preventDefault();

      var viewName = $(this).data('view');

      $('.spa-nav-item').removeClass('active');
      $(this).addClass('active');

      $('.view-section').removeClass('active').hide();

      var $targetView = $('#' + viewName + 'View');
      if ($targetView.length) {
        $targetView.addClass('active').fadeIn(300);
      }

      console.log('📄 Navigated to view:', viewName);
    });

    console.log('✅ SPA Navigation initialized');
  }

  // ============================================
  // 2. YEAR FILTER INITIALIZATION
  // ============================================

  function populateYearFilter() {
    var $yearFilter = $('#yearFilter');
    if ($yearFilter.length === 0) return;

    var currentYear  = new Date().getFullYear();
    var selectedYear = $yearFilter.data('selected') || currentYear;

    $yearFilter.empty();

    for (var year = 2020; year <= currentYear + 2; year++) {
      var selected = year === parseInt(selectedYear) ? 'selected' : '';
      $yearFilter.append('<option value="' + year + '" ' + selected + '>' + year + '</option>');
    }

    console.log('✅ Year filter populated');
  }

  // ============================================
  // 3. PLANNING MODAL FUNCTIONALITY
  // ============================================

  function initPlanningModal() {
    console.log('Setting up planning modal handlers...');

    $(document).on('click', '.planning-option-card', function (e) {
      e.preventDefault();
      $('.planning-option-card').removeClass('selected');
      $(this).addClass('selected');
      var logic = $(this).data('logic');
      $('#planningLogic').val(logic);
      console.log('✅ Selected planning logic:', logic);
    });

    $(document).on('click', '.period-btn', function (e) {
      e.preventDefault();
      $('.period-btn').removeClass('active');
      $(this).addClass('active');
      var period = $(this).data('period');
      $('#maintenancePeriod').val(period);
      console.log('✅ Selected maintenance period:', period, 'months');
    });

    $('#initializationForm').on('submit', function (e) {
      var selectedLogic  = $('#planningLogic').val();
      var selectedPeriod = $('#maintenancePeriod').val();

      if (!selectedLogic) {
        alert('Please select a planning logic (Department or Description)');
        e.preventDefault();
        return false;
      }

      if (!selectedPeriod) {
        alert('Please select a maintenance period');
        e.preventDefault();
        return false;
      }

      showLoadingOverlay();
      console.log('🚀 Submitting initialization with logic:', selectedLogic, 'period:', selectedPeriod);
    });

    console.log('✅ Planning modal initialized');
  }

  // ============================================
  // 4. SEARCH FUNCTIONALITY
  // ============================================

  function initSearchFilter() {
    var $searchInput = $('#searchInput');
    if ($searchInput.length === 0) return;

    $searchInput.on('keyup', function () {
      var searchTerm = $(this).val().toLowerCase();

      $('.equipment-row').each(function () {
        var searchData = $(this).data('search');
        if (searchData) {
          $(this).toggle(searchData.toLowerCase().includes(searchTerm));
        } else {
          $(this).toggle($(this).text().toLowerCase().includes(searchTerm));
        }
      });

      var visibleCount = $('.equipment-row:visible').length;
      var totalCount   = $('.equipment-row').length;
      console.log('🔍 Search results:', visibleCount, 'of', totalCount, 'items');

      if (visibleCount === 0 && searchTerm.length > 0) {
        if ($('#noSearchResults').length === 0) {
          $('#schedulesTable tbody').append(
            '<tr id="noSearchResults">' +
              '<td colspan="8" class="text-center py-4 text-muted">' +
                '<i class="fas fa-search fa-2x mb-2 opacity-50"></i>' +
                '<p class="mb-0">No equipment found matching "' + searchTerm + '"</p>' +
              '</td>' +
            '</tr>'
          );
        }
      } else {
        $('#noSearchResults').remove();
      }
    });

    console.log('✅ Search filter initialized');
  }

  // ============================================
  // 5. UNSCHEDULED EQUIPMENT HANDLING
  // ============================================

  function initUnscheduledEquipment() {
    $('#selectAllUnscheduled').on('change', function () {
      $('.unscheduled-checkbox').prop('checked', $(this).is(':checked'));
      updateScheduleSelectedButton();
    });

    $(document).on('change', '.unscheduled-checkbox', function () {
      updateScheduleSelectedButton();
      var total   = $('.unscheduled-checkbox').length;
      var checked = $('.unscheduled-checkbox:checked').length;
      $('#selectAllUnscheduled').prop('checked', total === checked);
    });

    $('#scheduleSelectedBtn').on('click', function () {
      var selectedCount = $('.unscheduled-checkbox:checked').length;
      if (selectedCount > 0 && confirm('Schedule ' + selectedCount + ' equipment item(s)?')) {
        $('#unscheduledForm').submit();
      }
    });

    $(document).on('click', '.schedule-single-btn', function () {
      var equipmentId = $(this).data('equipment-id');
      $('.unscheduled-checkbox[value="' + equipmentId + '"]').prop('checked', true);

      if (confirm('Schedule this equipment?')) {
        $('#unscheduledForm').submit();
      } else {
        $('.unscheduled-checkbox[value="' + equipmentId + '"]').prop('checked', false);
      }
    });

    console.log('✅ Unscheduled equipment handlers initialized');
  }

  function updateScheduleSelectedButton() {
    var checkedCount = $('.unscheduled-checkbox:checked').length;
    var $btn = $('#scheduleSelectedBtn');

    if (checkedCount > 0) {
      $btn.prop('disabled', false)
        .removeClass('btn-secondary').addClass('btn-primary')
        .html('<i class="fas fa-calendar-plus me-1"></i>Schedule Selected (' + checkedCount + ')');
    } else {
      $btn.prop('disabled', true)
        .removeClass('btn-primary').addClass('btn-secondary')
        .html('<i class="fas fa-calendar-plus me-1"></i>Schedule Selected');
    }
  }

  // ============================================
  // 6. MONTH / YEAR FILTER NAVIGATION
  // ============================================

  function initMonthYearFilter() {
    // When month or year changes → reload page and reset the week filter
    $('#monthFilter, #yearFilter').on('change', function () {
      var month = $('#monthFilter').val();
      var year  = $('#yearFilter').val();

      var url = new URL(window.location.href);
      url.searchParams.set('month', month);
      url.searchParams.set('year', year);
      url.searchParams.delete('week');  // reset week when month/year changes

      showLoadingOverlay();
      window.location.href = url.toString();
    });

    // Week filter changes → preserve month/year and apply the week
    $('#weekFilter').on('change', function () {
      var month = $('#monthFilter').val();
      var year  = $('#yearFilter').val();
      var week  = $(this).val();

      var url = new URL(window.location.href);
      url.searchParams.set('month', month);
      url.searchParams.set('year', year);
      if (week) {
        url.searchParams.set('week', week);
      } else {
        url.searchParams.delete('week');
      }

      showLoadingOverlay();
      window.location.href = url.toString();
    });

    console.log('✅ Month/Year/Week filters initialized');
  }

  // ============================================
  // 7. BULK ACTIONS
  // ============================================

  function initBulkActions() {
    $('#selectAll').on('change', function () {
      $('.schedule-checkbox').prop('checked', $(this).is(':checked'));
      updateBulkActionButtons();
    });

    $(document).on('change', '.schedule-checkbox', function () {
      updateBulkActionButtons();
      var total   = $('.schedule-checkbox').length;
      var checked = $('.schedule-checkbox:checked').length;
      $('#selectAll').prop('checked', total === checked);
    });

    $('#bulkMarkCompleted').on('click', function () {
      var count = $('.schedule-checkbox:checked').length;
      if (count > 0 && confirm('Mark ' + count + ' schedule(s) as completed?')) {
        $('#bulkAction').val('mark_completed');
        $('#bulkActionForm').submit();
      }
    });

    $('#bulkPushSchedules').on('click', function () {
      var count = $('.schedule-checkbox:checked').length;
      if (count > 0 && confirm('Push ' + count + ' schedule(s) to next period?')) {
        $('#bulkAction').val('push');
        $('#bulkActionForm').submit();
      }
    });

    $('#bulkDeleteSchedules').on('click', function () {
      var count = $('.schedule-checkbox:checked').length;
      if (count > 0 && confirm('Delete ' + count + ' schedule(s)? This cannot be undone.')) {
        $('#bulkAction').val('delete');
        $('#bulkActionForm').submit();
      }
    });

    console.log('✅ Bulk actions initialized');
  }

  function updateBulkActionButtons() {
    var checkedCount = $('.schedule-checkbox:checked').length;
    var disabled = checkedCount === 0;

    $('#bulkMarkCompleted, #bulkPushSchedules, #bulkDeleteSchedules').prop('disabled', disabled);

    if (checkedCount > 0) {
      $('#bulkMarkCompleted').html('<i class="fas fa-check me-1"></i>Mark Completed (' + checkedCount + ')');
      $('#bulkPushSchedules').html('<i class="fas fa-arrow-right me-1"></i>Push (' + checkedCount + ')');
      $('#bulkDeleteSchedules').html('<i class="fas fa-trash me-1"></i>Delete (' + checkedCount + ')');
    } else {
      $('#bulkMarkCompleted').html('<i class="fas fa-check me-1"></i>Mark Completed');
      $('#bulkPushSchedules').html('<i class="fas fa-arrow-right me-1"></i>Push');
      $('#bulkDeleteSchedules').html('<i class="fas fa-trash me-1"></i>Delete');
    }
  }

  // ============================================
  // 8. LOADING OVERLAY
  // ============================================

  function showLoadingOverlay() {
    var $overlay = $('#loadingOverlay');
    if ($overlay.length === 0) return;

    $overlay.removeClass('d-none');

    var progress = 0;
    var interval = setInterval(function () {
      progress += Math.random() * 15;
      if (progress >= 100) {
        progress = 100;
        clearInterval(interval);
      }

      $('#initProgress').css('width', progress + '%');

      if (progress < 30) {
        $('#statusMessage').text('Analyzing equipment...');
      } else if (progress < 60) {
        $('#statusMessage').text('Creating schedules...');
      } else if (progress < 90) {
        $('#statusMessage').text('Finalizing...');
      } else {
        $('#statusMessage').text('Complete!');
      }
    }, 200);
  }

  // ============================================
  // 9. CURRENT MONTH NAVIGATION (global)
  // ============================================

  window.goToCurrentMonth = function () {
    var now = new Date();
    var url = new URL(window.location.href);
    url.searchParams.set('month', now.getMonth() + 1);
    url.searchParams.set('year', now.getFullYear());
    url.searchParams.delete('week');
    showLoadingOverlay();
    window.location.href = url.toString();
  };

  // ============================================
  // 10. ANALYTICS CHARTS
  // ============================================

  var analyticsLoaded = false;
  var monthlyChart    = null;
  var equipTypeChart  = null;

  function initAnalyticsCharts() {
    if ($('#analyticsView').length === 0) return;

    $('.spa-nav-item[data-view="analytics"]').on('click', function () {
      if (!analyticsLoaded) loadAnalyticsData();
    });

    console.log('📊 Analytics charts ready (will load on tab click)');
  }

  function loadAnalyticsData() {
    $('#complianceRate').html('<span class="spinner-border spinner-border-sm text-primary"></span>');
    $('#activityTimeline').html(
      '<div class="text-center py-4">' +
        '<div class="spinner-border text-primary" role="status"></div>' +
        '<p class="text-muted mt-3">Loading activity...</p>' +
      '</div>'
    );

    var csrfToken    = $('meta[name="csrf-token"]').attr('content');
    var analyticsUrl = (window.PPM_URLS && window.PPM_URLS.analytics) || '/ppm/api/analytics/';

    console.log('📡 Fetching analytics from:', analyticsUrl);

    $.ajax({
      url: analyticsUrl,
      method: 'GET',
      headers: { 'X-CSRFToken': csrfToken },
      success: function (data) {
        analyticsLoaded = true;
        renderAnalytics(data);
        console.log('✅ Analytics data loaded');
      },
      error: function (xhr) {
        console.error('❌ Failed to load analytics:', xhr.status, xhr.responseText);
        $('#complianceRate').text('Error');
        $('#activityTimeline').html(
          '<div class="alert alert-danger m-3">' +
            '<i class="fas fa-exclamation-triangle me-2"></i>' +
            'Failed to load analytics data (' + xhr.status + '). Please refresh and try again.' +
          '</div>'
        );
      }
    });
  }

  function renderAnalytics(data) {
    var summary        = data.summary              || {};
    var monthlyTrend   = data.monthly_trend        || [];
    var equipmentTypes = data.equipment_types      || [];
    var overdue        = data.overdue_list         || [];
    var upcoming       = data.upcoming_maintenance || [];
    var recentActs     = data.recent_activities    || [];

    // --- Compliance Rate ---
    var rate      = summary.compliance_rate || 0;
    var rateColor = rate >= 80 ? 'text-success' : rate >= 50 ? 'text-warning' : 'text-danger';
    $('#complianceRate').html('<span class="' + rateColor + '">' + rate + '%</span>');
    $('#overdueCount').text(summary.overdue || 0);

    // --- Monthly Trend Chart ---
    var trendCtx = document.getElementById('monthlyTrendChart');
    if (trendCtx && typeof Chart !== 'undefined') {
      if (monthlyChart) monthlyChart.destroy();
      monthlyChart = new Chart(trendCtx.getContext('2d'), {
        type: 'bar',
        data: {
          labels: monthlyTrend.map(function (m) { return m.month; }),
          datasets: [
            {
              label: 'Completed',
              data: monthlyTrend.map(function (m) { return m.completed; }),
              backgroundColor: 'rgba(16, 185, 129, 0.7)',
              borderColor: '#10b981',
              borderWidth: 1
            },
            {
              label: 'Pending',
              data: monthlyTrend.map(function (m) { return m.pending; }),
              backgroundColor: 'rgba(245, 158, 11, 0.7)',
              borderColor: '#f59e0b',
              borderWidth: 1
            },
            {
              label: 'Pushed',
              data: monthlyTrend.map(function (m) { return m.pushed; }),
              backgroundColor: 'rgba(6, 182, 212, 0.7)',
              borderColor: '#06b6d4',
              borderWidth: 1
            },
            {
              label: 'Overdue',
              data: monthlyTrend.map(function (m) { return m.overdue; }),
              backgroundColor: 'rgba(239, 68, 68, 0.7)',
              borderColor: '#ef4444',
              borderWidth: 1
            }
          ]
        },
        options: {
          responsive: true,
          plugins: {
            legend: { position: 'top' },
            tooltip: { mode: 'index', intersect: false }
          },
          scales: {
            x: { stacked: false },
            y: { beginAtZero: true, ticks: { stepSize: 1 } }
          }
        }
      });
    } else if (trendCtx && typeof Chart === 'undefined') {
      $(trendCtx).closest('.theme-card-body').html(
        '<div class="alert alert-warning">Chart.js not loaded.</div>'
      );
    }

    // --- Equipment Type Chart (horizontal bars via indexAxis) ---
    var typeCtx = document.getElementById('equipmentTypeChart');
    if (typeCtx && typeof Chart !== 'undefined' && equipmentTypes.length > 0) {
      if (equipTypeChart) equipTypeChart.destroy();
      equipTypeChart = new Chart(typeCtx.getContext('2d'), {
        type: 'bar',                // Chart.js v3 — no more 'horizontalBar'
        data: {
          labels: equipmentTypes.map(function (t) { return t.name; }),
          datasets: [
            {
              label: 'Completed',
              data: equipmentTypes.map(function (t) { return t.completed; }),
              backgroundColor: 'rgba(16, 185, 129, 0.7)'
            },
            {
              label: 'Pending',
              data: equipmentTypes.map(function (t) { return t.pending; }),
              backgroundColor: 'rgba(245, 158, 11, 0.7)'
            }
          ]
        },
        options: {
          indexAxis: 'y',           // makes bars horizontal
          responsive: true,
          plugins: { legend: { position: 'top' } },
          scales: { x: { beginAtZero: true, ticks: { stepSize: 1 } } }
        }
      });
    } else if (typeCtx && equipmentTypes.length === 0) {
      $(typeCtx).closest('.theme-card-body').html(
        '<p class="text-muted text-center py-3">No equipment type data available.</p>'
      );
    }

    // --- Activity Timeline ---
    renderActivityTimeline(overdue, upcoming, recentActs);
  }

  /**
   * Render the full activity timeline panel into #activityTimeline.
   * Three sections: Overdue · Due This Month · Recent Updates
   */
  function renderActivityTimeline(overdue, upcoming, recentActs) {
    var html = '';

    // ── Overdue ───────────────────────────────────────
    if (overdue.length > 0) {
      html += '<div class="mb-4">' +
        '<h6 class="fw-bold text-danger mb-3">' +
          '<i class="fas fa-exclamation-circle me-2"></i>Overdue (' + overdue.length + ')' +
        '</h6>';

      overdue.forEach(function (item) {
        html +=
          '<div class="activity-item">' +
            '<div class="activity-dot" style="background:#dc3545"><i class="fas fa-clock"></i></div>' +
            '<div class="activity-body">' +
              '<div class="activity-title">' + item.equipment + '</div>' +
              '<div class="activity-meta">' +
                '<i class="fas fa-building me-1"></i>' + item.department +
                ' &nbsp;·&nbsp; <i class="far fa-calendar me-1"></i>' + item.scheduled_date +
              '</div>' +
            '</div>' +
            '<span class="badge bg-danger activity-badge">' + item.days_overdue + 'd overdue</span>' +
          '</div>';
      });

      html += '</div>';
    }

    // ── Due This Month ────────────────────────────────
    if (upcoming.length > 0) {
      html += '<div class="mb-4">' +
        '<h6 class="fw-bold text-primary mb-3">' +
          '<i class="fas fa-calendar-check me-2"></i>Due This Month (' + upcoming.length + ')' +
        '</h6>';

      upcoming.forEach(function (item) {
        var urgencyCls = item.days_remaining <= 7 ? 'bg-warning text-dark' : 'bg-success';
        html +=
          '<div class="activity-item">' +
            '<div class="activity-dot" style="background:#0d6efd"><i class="fas fa-tools"></i></div>' +
            '<div class="activity-body">' +
              '<div class="activity-title">' + item.equipment + '</div>' +
              '<div class="activity-meta">' +
                '<i class="fas fa-building me-1"></i>' + item.department +
                ' &nbsp;·&nbsp; <i class="far fa-calendar me-1"></i>' + item.scheduled_date +
              '</div>' +
            '</div>' +
            '<span class="badge ' + urgencyCls + ' activity-badge">' + item.days_remaining + 'd left</span>' +
          '</div>';
      });

      html += '</div>';
    }

    // ── Recent Activity Feed ──────────────────────────
    if (recentActs.length > 0) {
      var STATUS_META = {
        completed : { icon: 'fa-check',      bg: '#198754', badgeCls: 'bg-success' },
        pending   : { icon: 'fa-clock',       bg: '#ffc107', badgeCls: 'bg-warning text-dark', dark: true },
        pushed    : { icon: 'fa-arrow-right', bg: '#0dcaf0', badgeCls: 'bg-info text-dark',    dark: true }
      };

      html += '<div class="mb-2">' +
        '<h6 class="fw-bold text-secondary mb-3">' +
          '<i class="fas fa-history me-2"></i>Recent Updates (' + recentActs.length + ')' +
        '</h6>';

      recentActs.forEach(function (a) {
        var meta       = STATUS_META[a.status] || { icon: 'fa-circle', bg: '#6c757d', badgeCls: 'bg-secondary' };
        var colorStyle = 'background:' + meta.bg + (meta.dark ? ';color:#212529' : '');
        var statusLabel = a.status.charAt(0).toUpperCase() + a.status.slice(1);

        html +=
          '<div class="activity-item">' +
            '<div class="activity-dot" style="' + colorStyle + '">' +
              '<i class="fas ' + meta.icon + '"></i>' +
            '</div>' +
            '<div class="activity-body">' +
              '<div class="activity-title">' + a.equipment + '</div>' +
              '<div class="activity-meta">' +
                '<i class="fas fa-building me-1"></i>' + a.department +
                ' &nbsp;·&nbsp; <i class="far fa-calendar me-1"></i>' + a.scheduled_date +
                ' &nbsp;·&nbsp; <i class="far fa-clock me-1"></i>' + a.updated_at +
              '</div>' +
            '</div>' +
            '<span class="badge ' + meta.badgeCls + ' activity-badge">' + statusLabel + '</span>' +
          '</div>';
      });

      html += '</div>';
    }

    // ── Empty State ───────────────────────────────────
    if (!html) {
      html =
        '<div class="text-center py-5 text-muted">' +
          '<i class="fas fa-check-circle text-success fa-3x mb-3 d-block opacity-75"></i>' +
          '<p class="mb-0 fw-medium">All clear — no overdue, upcoming, or recent activity.</p>' +
        '</div>';
    }

    $('#activityTimeline').html(html);
  }

  // ============================================
  // 11. REPORTS FUNCTIONALITY
  // ============================================

  function initReportsView() {
    $('#generateReportBtn').on('click', function () {
      $('#customReportModal').modal('show');
    });

    console.log('✅ Reports view initialized');
  }

  // ============================================
  // 12. TOOLTIPS
  // ============================================

  function initTooltips() {
    if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
      $('[data-bs-toggle="tooltip"]').each(function () {
        new bootstrap.Tooltip(this);
      });
      console.log('✅ Tooltips initialized');
    }
  }

  // ============================================
  // 13. MODAL HANDLERS
  // ============================================

  function initModalHandlers() {
    $('.modal form').on('submit', function () {
      var $modal = $(this).closest('.modal');
      setTimeout(function () { $modal.modal('hide'); }, 500);
    });

    console.log('✅ Modal handlers initialized');
  }

  // ============================================
  // 14. DEPARTMENT FILTER — loading overlay
  // Works with the new .ppm-dept-item links and
  // any remaining Bootstrap .dropdown-item links
  // ============================================

  function initDepartmentFilter() {
    // New custom dropdown items
    $(document).on('click', '.ppm-dept-item', function () {
      var href = $(this).attr('href');
      if (href && href !== '#') showLoadingOverlay();
    });

    // Fallback for any Bootstrap dropdown-item links
    $(document).on('click', '.dropdown-item[href]', function () {
      if ($(this).attr('href').includes('ppm')) showLoadingOverlay();
    });

    console.log('✅ Department filter initialized');
  }

  // ============================================
  // MAIN INITIALIZATION
  // ============================================

  function initialize() {
    console.log('🎬 Starting PPM Dashboard initialization...');

    try {
      populateYearFilter();
      initSPANavigation();
      initPlanningModal();
      initSearchFilter();
      initUnscheduledEquipment();
      initMonthYearFilter();
      initBulkActions();
      initDepartmentFilter();

      initAnalyticsCharts();
      initReportsView();
      initTooltips();
      initModalHandlers();

      console.log('✅ PPM Dashboard initialization complete!');
      console.log('✅ All features ready to use');

    } catch (error) {
      console.error('❌ Initialization error:', error);
      console.error('Stack:', error.stack);
    }
  }

  initialize();
});

// ============================================
// GLOBAL UTILITY FUNCTIONS
// ============================================

function formatCurrency(amount) {
  return new Intl.NumberFormat('en-KE', {
    style: 'currency',
    currency: 'KES'
  }).format(amount);
}

function formatDate(dateString) {
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric'
  });
}

function showNotification(message, type) {
  // Delegates to the shared toast system (static/js/notify.js, loaded globally in base.html)
  window.notify(message, type || 'info');
}

// ==========================================
// END OF PPM DASHBOARD JAVASCRIPT
// ==========================================
