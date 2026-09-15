// Enhanced Auto-fill current period (week, month, quarter, year) - ALWAYS CURRENT
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const reportType = document.querySelector('#id_report_type');
  const weekField = document.querySelector('#id_week');
  const monthField = document.querySelector('#id_month');
  const quarterField = document.querySelector('#id_quarter');
  const yearField = document.querySelector('#id_year');

  // Function to get current week number
  function getCurrentWeekNumber() {
    const today = new Date();
    const currentYear = today.getFullYear();
    const start = new Date(currentYear, 0, 1);
    const diffDays = Math.floor((today - start) / (1000 * 60 * 60 * 24));
    return Math.ceil((diffDays + start.getDay() + 1) / 7);
  }

  // Function to get current quarter
  function getCurrentQuarter() {
    const today = new Date();
    const currentMonth = today.getMonth() + 1;
    return Math.ceil(currentMonth / 3);
  }

  // Function to ALWAYS set current period values
  function setCurrentPeriod(type = null) {
    const today = new Date();
    const currentYear = today.getFullYear();
    const currentMonth = today.getMonth() + 1;
    const currentQuarter = getCurrentQuarter();
    const currentWeek = getCurrentWeekNumber();

    // Always set current year
    if (yearField) {
      yearField.value = currentYear;
    }

    // If no type specified, use the current report type
    if (!type && reportType) {
      type = reportType.value;
    }

    // Always set current values for ALL fields initially
    if (weekField) weekField.value = currentWeek;
    if (monthField) monthField.value = currentMonth;
    if (quarterField) quarterField.value = currentQuarter;
  }

  // Set current period on page load
  setCurrentPeriod();

  // Handle report type changes
  if (reportType) {
    reportType.addEventListener('change', function () {
      setCurrentPeriod(this.value);
    });
  }
});

// Show/hide filter fields by report type
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const reportType = document.querySelector('#id_report_type');
  const weeklyFields = document.querySelectorAll('.weekly-fields');
  const monthlyFields = document.querySelectorAll('.monthly-fields');
  const quarterlyFields = document.querySelectorAll('.quarterly-fields');

  function toggleFields() {
    const type = reportType ? reportType.value : '';

    // Hide all fields first
    weeklyFields.forEach(f => (f.style.display = 'none'));
    monthlyFields.forEach(f => (f.style.display = 'none'));
    quarterlyFields.forEach(f => (f.style.display = 'none'));

    // Show relevant fields
    if (type === 'weekly') {
      weeklyFields.forEach(f => (f.style.display = 'block'));
    } else if (type === 'monthly') {
      monthlyFields.forEach(f => (f.style.display = 'block'));
    } else if (type === 'quarterly') {
      quarterlyFields.forEach(f => (f.style.display = 'block'));
    }
    // Annual shows no additional fields
  }

  if (reportType) {
    toggleFields();
    reportType.addEventListener('change', toggleFields);
  }
});

// -------------------------------
// Add Filter Button and Handle Form Submission
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const filterForm = document.getElementById('hod-filter-form');
  if (!filterForm) return;

  // Find the clear button container and add filter button
  const clearButtonContainer = filterForm.querySelector('.mt-3');
  if (clearButtonContainer && !document.querySelector('#filter-btn')) {
    const filterButton = document.createElement('button');
    filterButton.type = 'submit';
    filterButton.id = 'filter-btn';
    filterButton.className = 'btn btn-primary me-2';
    filterButton.innerHTML = '<i class="bx bx-filter"></i> Apply Filter';

    // Insert filter button before clear button
    const clearButton = clearButtonContainer.querySelector('a');
    clearButtonContainer.insertBefore(filterButton, clearButton);
  }

  // Add "Reset to Current Period" button
  const clearButton = filterForm.querySelector('a[href*="hod_reports_landing"]');
  if (clearButton && !document.querySelector('#reset-current-btn')) {
    const resetCurrentBtn = document.createElement('button');
    resetCurrentBtn.type = 'button';
    resetCurrentBtn.id = 'reset-current-btn';
    resetCurrentBtn.className = 'btn btn-secondary me-2';
    resetCurrentBtn.innerHTML = '<i class="fas fa-sync-alt"></i> Current period';

    resetCurrentBtn.addEventListener('click', function () {
      // Reset to current period
      const today = new Date();
      const currentYear = today.getFullYear();
      const currentMonth = today.getMonth() + 1;
      const currentQuarter = Math.ceil(currentMonth / 3);
      const start = new Date(currentYear, 0, 1);
      const diffDays = Math.floor((today - start) / (1000 * 60 * 60 * 24));
      const currentWeek = Math.ceil((diffDays + start.getDay() + 1) / 7);

      const yearField = document.getElementById('id_year');
      const monthField = document.getElementById('id_month');
      const quarterField = document.getElementById('id_quarter');
      const weekField = document.getElementById('id_week');
      const reportTypeField = document.getElementById('id_report_type');

      if (yearField) yearField.value = currentYear;
      if (monthField) monthField.value = currentMonth;
      if (quarterField) quarterField.value = currentQuarter;
      if (weekField) weekField.value = currentWeek;
      if (reportTypeField && !reportTypeField.value) reportTypeField.value = 'monthly';

      // Submit form to refresh with current period
      filterForm.submit();
    });

    clearButton.parentNode.insertBefore(resetCurrentBtn, clearButton);
  }

  // Form validation
  filterForm.addEventListener('submit', function (e) {
    const year = document.getElementById('id_year').value;
    if (!year) {
      e.preventDefault();
      alert('Please select a year.');
      return false;
    }
  });
});

// -------------------------------
// Optional: Auto-submit on change (can be enabled/disabled)
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const enableAutoSubmit = false; // Set to true if you want auto-submit

  if (!enableAutoSubmit) return;

  const filterForm = document.getElementById('hod-filter-form');
  if (!filterForm) return;

  // Add change listeners to all form fields for auto-submit
  const formFields = filterForm.querySelectorAll('select:not(#id_report_type)');

  formFields.forEach(field => {
    field.addEventListener('change', function () {
      setTimeout(() => {
        filterForm.submit();
      }, 300);
    });
  });

  // Special handling for report type
  const reportType = document.querySelector('#id_report_type');
  if (reportType) {
    reportType.addEventListener('change', function () {
      setTimeout(() => {
        filterForm.submit();
      }, 500);
    });
  }
});

// -------------------------------
// Status Chart (Waiting Approval vs Approved)
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const cfg = window.reportConfig || {};
  if (cfg.totalWaitingApproval > 0 || cfg.totalApproved > 0) {
    const ctx = document.getElementById('statusChart');
    if (ctx) {
      new Chart(ctx, {
        type: 'bar',
        data: {
          labels: ['Waiting Approval', 'Approved'],
          datasets: [
            {
              label: 'Job Cards',
              data: [cfg.totalWaitingApproval, cfg.totalApproved],
              backgroundColor: ['#e55353', '#27c24c'],
              borderColor: ['#b32d2d', '#1e8e3e'],
              borderWidth: 1,
              barThickness: 30,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          plugins: { legend: { display: false } },
          scales: {
            y: { beginAtZero: true, title: { display: true, text: 'Number of Job Cards' } },
            x: { title: { display: true, text: 'Status' } },
          },
        },
      });
    }
  }
});

// -------------------------------
// Annual Trend Chart
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const cfg = window.reportConfig || {};
  if (cfg.reportType === 'annual' && cfg.annualData) {
    const trendCtx = document.getElementById('annualTrendChart');
    if (trendCtx) {
      const months = [
        'Jan',
        'Feb',
        'Mar',
        'Apr',
        'May',
        'Jun',
        'Jul',
        'Aug',
        'Sep',
        'Oct',
        'Nov',
        'Dec',
      ];
      const waitingData = [],
        approvedData = [],
        totalData = [];

      for (let i = 1; i <= 12; i++) {
        const m = cfg.annualData[i] || { waiting: 0, approved: 0, total: 0 };
        waitingData.push(m.waiting);
        approvedData.push(m.approved);
        totalData.push(m.total);
      }

      new Chart(trendCtx, {
        type: 'line',
        data: {
          labels: months,
          datasets: [
            { label: 'Waiting Approval', data: waitingData, borderColor: '#e55353', fill: true },
            { label: 'Approved', data: approvedData, borderColor: '#27c24c', fill: true },
            { label: 'Total Activity', data: totalData, borderColor: '#6366f1', fill: false },
          ],
        },
      });
    }
  }
});

// -------------------------------
// Weekly dropdown reload when year changes
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const yearField = document.getElementById('id_year');
  const weekField = document.getElementById('id_week');

  if (yearField && weekField) {
    yearField.addEventListener('change', function () {
      const year = this.value;
      if (year) {
        fetch(`/report-hub/get-weekly-choices-json/?year=${year}`)
          .then(res => res.json())
          .then(data => {
            weekField.innerHTML = '<option value="">Select Week</option>';
            data.forEach(([value, label]) => {
              const opt = document.createElement('option');
              opt.value = value;
              opt.textContent = label;
              weekField.appendChild(opt);
            });

            // Auto-select current week if current year
            const today = new Date();
            const currentYear = today.getFullYear();
            if (parseInt(year) === currentYear) {
              const start = new Date(currentYear, 0, 1);
              const diffDays = Math.floor((today - start) / (1000 * 60 * 60 * 24));
              const currentWeek = Math.ceil((diffDays + start.getDay() + 1) / 7);
              weekField.value = currentWeek;
            }
          })
          .catch(err => console.error('Error fetching weekly choices:', err));
      }
    });
  }
});

// -------------------------------
// Print Button
// -------------------------------
function printReport() {
  window.print();
}

document.addEventListener('DOMContentLoaded', function () {
  const downloadSection = document.querySelector('.download-section .btn-group');
  if (downloadSection) {
    const printBtn = document.createElement('button');
    printBtn.className = 'btn btn-secondary';
    printBtn.innerHTML = '<i class="bx bx-printer"></i> Print';
    printBtn.onclick = printReport;
    downloadSection.appendChild(printBtn);
  }
});

// -------------------------------
// AJAX Form Updates with Chart Refresh (Optional)
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const filterForm = document.getElementById('hod-filter-form');
  const pdfBtn = document.getElementById('download-pdf');
  const csvBtn = document.getElementById('export-csv');
  const enableAjax = false; // Set to true if you want AJAX updates

  if (!filterForm || !enableAjax) return;

  let statusChart = null;

  async function fetchReport() {
    const formData = new FormData(filterForm);
    const params = new URLSearchParams(formData).toString();

    try {
      const response = await fetch(filterForm.action + '?' + params, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });

      if (!response.ok) return;
      const data = await response.json();

      // Update sections if they exist
      const tableContainer = document.querySelector('.table-responsive');
      const summaryCard = document.querySelector('.card.mb-4');

      if (tableContainer && data.table_html) {
        tableContainer.outerHTML = data.table_html;
      }
      if (summaryCard && data.summary_html) {
        summaryCard.outerHTML = data.summary_html;
      }

      // Update download buttons
      if (pdfBtn && data.download_pdf) pdfBtn.href = data.download_pdf;
      if (csvBtn && data.export_csv) csvBtn.href = data.export_csv;

      // Update status chart
      const ctx = document.getElementById('statusChart');
      if (ctx && data.chart_data) {
        if (statusChart) statusChart.destroy();
        statusChart = new Chart(ctx, {
          type: 'doughnut',
          data: {
            labels: ['Waiting Approval', 'Approved'],
            datasets: [
              {
                data: [data.chart_data.total_waiting || 0, data.chart_data.total_approved || 0],
                backgroundColor: ['#f39c12', '#27ae60'],
              },
            ],
          },
          options: { responsive: true, maintainAspectRatio: false },
        });
      }

      // Update annual trend chart
      if (data.chart_data && data.chart_data.annual_breakdown) {
        const annualCtx = document.getElementById('annualTrendChart');
        if (annualCtx) {
          if (window.annualChart) window.annualChart.destroy();
          const months = Object.keys(data.chart_data.annual_breakdown);
          const approved = months.map(m => data.chart_data.annual_breakdown[m].approved);
          const waiting = months.map(m => data.chart_data.annual_breakdown[m].waiting);

          window.annualChart = new Chart(annualCtx, {
            type: 'line',
            data: {
              labels: months,
              datasets: [
                { label: 'Approved', data: approved, borderColor: '#27ae60', fill: false },
                { label: 'Waiting', data: waiting, borderColor: '#f39c12', fill: false },
              ],
            },
            options: { responsive: true, maintainAspectRatio: false },
          });
        }
      }
    } catch (error) {
      console.error('Error fetching report:', error);
    }
  }

  // Auto-fetch on filter change (only if AJAX enabled)
  const inputs = filterForm.querySelectorAll('select, input');
  inputs.forEach(input => {
    input.addEventListener('change', fetchReport);
  });
});

// -------------------------------
// Display Current Period Info
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const today = new Date();
  const currentYear = today.getFullYear();
  const currentMonth = today.getMonth() + 1;
  const currentQuarter = Math.ceil(currentMonth / 3);
  const start = new Date(currentYear, 0, 1);
  const diffDays = Math.floor((today - start) / (1000 * 60 * 60 * 24));
  const currentWeek = Math.ceil((diffDays + start.getDay() + 1) / 7);
  const monthName = today.toLocaleString('default', { month: 'long' });

  // Add current period indicator
  const filterTitle = document.querySelector('.filter-form-container h5');
  if (filterTitle && !document.querySelector('.current-period-info')) {
    const currentInfo = document.createElement('small');
    currentInfo.className = 'text-muted ms-2 current-period-info';
    currentInfo.innerHTML = `<br><i class="bx fas fa-info-circle"></i> Current: Week ${currentWeek}, ${monthName} ${currentYear}, Q${currentQuarter}`;
    filterTitle.appendChild(currentInfo);
  }
});

// -------------------------------
// Initialize DataTable for reports table
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
  const reportsTable = document.getElementById('reportsTable');
  if (reportsTable && typeof $ !== 'undefined' && $.fn.DataTable) {
    $(reportsTable).DataTable({
      responsive: true,
      pageLength: 25,
      order: [[0, 'desc']], // Sort by ID descending
      columnDefs: [
        { targets: -1, orderable: false }, // Disable sorting on Actions column
      ],
      searching: false, // Disable search functionality
      lengthChange: false, // Disable entries per page dropdown
    });
  }
});
