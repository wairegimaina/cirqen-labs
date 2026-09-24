// Global chart instances
let statusChart = null;
let annualTrendChart = null;

// Toggle chart/metrics column visibility
function toggleChartVisibility(reportType) {
  const annualColumn = document.getElementById('annual-chart-column');
  const metricsColumn = document.getElementById('metrics-column');

  if (annualColumn && metricsColumn) {
    if (reportType === 'annual') {
      annualColumn.style.display = 'block';
      metricsColumn.style.display = 'none';
    } else {
      annualColumn.style.display = 'none';
      metricsColumn.style.display = 'block';
    }
  }
}

// Show/hide filter fields by report type
function toggleFields() {
  const reportType = document.querySelector('#id_report_type');
  if (!reportType) return;

  const type = reportType.value;
  const weeklyFields = document.querySelectorAll('.weekly-fields');
  const monthlyFields = document.querySelectorAll('.monthly-fields');
  const quarterlyFields = document.querySelectorAll('.quarterly-fields');

  weeklyFields.forEach(f => (f.style.display = type === 'weekly' ? 'block' : 'none'));
  monthlyFields.forEach(f => (f.style.display = type === 'monthly' ? 'block' : 'none'));
  quarterlyFields.forEach(f => (f.style.display = type === 'quarterly' ? 'block' : 'none'));

  if (type === 'annual') {
    weeklyFields.forEach(f => (f.style.display = 'none'));
    monthlyFields.forEach(f => (f.style.display = 'none'));
    quarterlyFields.forEach(f => (f.style.display = 'none'));
  }

  // Update chart visibility
  toggleChartVisibility(type);
}

// Auto-fill current period (week, month, quarter, year)
function setCurrentPeriod(type) {
  const weekField = document.querySelector('#id_week');
  const monthField = document.querySelector('#id_month');
  const quarterField = document.querySelector('#id_quarter');
  const yearField = document.querySelector('#id_year');

  const today = new Date();
  const currentYear = today.getFullYear();
  const currentMonth = today.getMonth() + 1;
  const currentQuarter = Math.floor((currentMonth - 1) / 3) + 1;

  if (yearField) yearField.value = currentYear;

  if (type === 'weekly' && weekField) {
    const start = new Date(currentYear, 0, 1);
    const diffDays = Math.floor((today - start) / (1000 * 60 * 60 * 24));
    const weekNum = Math.ceil((diffDays + start.getDay() + 1) / 7);
    weekField.value = weekNum;
  }
  if (type === 'monthly' && monthField) {
    monthField.value = currentMonth;
  }
  if (type === 'quarterly' && quarterField) {
    quarterField.value = currentQuarter;
  }
  if (type === 'annual' && yearField) {
    yearField.value = currentYear;
  }
}

// Initialize filter fields
function initializeFilters() {
  const reportType = document.querySelector('#id_report_type');

  if (reportType) {
    toggleFields();
    reportType.addEventListener('change', function () {
      toggleFields();
      setCurrentPeriod(this.value);
    });
    setCurrentPeriod(reportType.value);
  }
}

// Status Chart (Waiting Approval vs Approved vs Declined) - Initial Load
document.addEventListener('DOMContentLoaded', function () {
  const cfg = window.reportConfig || {};
  const totalWaiting = cfg.totalWaitingApproval || 0;
  const totalApproved = cfg.totalApproved || 0;
  const totalDeclined = cfg.totalDeclined || 0;

  if (totalWaiting > 0 || totalApproved > 0 || totalDeclined > 0) {
    const ctx = document.getElementById('statusChart');
    if (ctx) {
      statusChart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: ['Waiting Approval', 'Approved', 'Declined'],
          datasets: [
            {
              label: 'Work Orders',
              data: [totalWaiting, totalApproved, totalDeclined],
              backgroundColor: ['#fbbf24', '#27c24c', '#e55353'],
              borderColor: ['#f59e0b', '#1e8e3e', '#b32d2d'],
              borderWidth: 2,
              barThickness: 50,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: 'rgba(0, 0, 0, 0.8)',
              padding: 12,
              titleFont: { size: 14 },
              bodyFont: { size: 13 },
              callbacks: {
                label: function (context) {
                  const total = totalWaiting + totalApproved + totalDeclined;
                  const percentage = total > 0 ? ((context.parsed.y / total) * 100).toFixed(1) : 0;
                  return `${context.parsed.y} cards (${percentage}%)`;
                },
              },
            },
          },
          scales: {
            y: {
              beginAtZero: true,
              title: {
                display: true,
                text: 'Number of Work Orders',
                font: { size: 12, weight: 'bold' },
              },
              ticks: { precision: 0 },
            },
            x: {
              title: { display: true, text: 'Status', font: { size: 12, weight: 'bold' } },
            },
          },
        },
      });
    }
  }

  // Initialize chart visibility
  const reportType = cfg.reportType || 'weekly';
  toggleChartVisibility(reportType);
});

// Annual Trend Chart - Initial Load with DECLINED data
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
      const waitingData = [];
      const approvedData = [];
      const declinedData = [];
      const totalData = [];

      for (let i = 1; i <= 12; i++) {
        const m = cfg.annualData[i] || { waiting: 0, approved: 0, declined: 0, total: 0 };
        waitingData.push(m.waiting || 0);
        approvedData.push(m.approved || 0);
        declinedData.push(m.declined || 0);
        totalData.push(m.total || 0);
      }

      annualTrendChart = new Chart(trendCtx, {
        type: 'line',
        data: {
          labels: months,
          datasets: [
            {
              label: 'Waiting Approval',
              data: waitingData,
              borderColor: '#fbbf24',
              backgroundColor: 'rgba(251, 191, 36, 0.15)',
              fill: true,
              tension: 0.4,
              borderWidth: 3,
              pointRadius: 4,
              pointHoverRadius: 6,
            },
            {
              label: 'Approved',
              data: approvedData,
              borderColor: '#27c24c',
              backgroundColor: 'rgba(39, 194, 76, 0.15)',
              fill: true,
              tension: 0.4,
              borderWidth: 3,
              pointRadius: 4,
              pointHoverRadius: 6,
            },
            {
              label: 'Declined',
              data: declinedData,
              borderColor: '#e55353',
              backgroundColor: 'rgba(229, 83, 83, 0.15)',
              fill: true,
              tension: 0.4,
              borderWidth: 3,
              pointRadius: 4,
              pointHoverRadius: 6,
            },
            {
              label: 'Total Activity',
              data: totalData,
              borderColor: '#6366f1',
              backgroundColor: 'transparent',
              fill: false,
              tension: 0.4,
              borderWidth: 3,
              borderDash: [8, 4],
              pointRadius: 5,
              pointHoverRadius: 7,
              pointStyle: 'circle',
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: {
            mode: 'index',
            intersect: false,
          },
          plugins: {
            legend: {
              display: true,
              position: 'top',
              labels: {
                usePointStyle: true,
                padding: 15,
                font: { size: 11, weight: '600' },
                boxWidth: 8,
                boxHeight: 8,
              },
            },
            tooltip: {
              backgroundColor: 'rgba(0, 0, 0, 0.85)',
              padding: 12,
              titleFont: { size: 13, weight: 'bold' },
              bodyFont: { size: 12 },
              borderColor: '#cbd5e1',
              borderWidth: 1,
              callbacks: {
                label: function (context) {
                  return `${context.dataset.label}: ${context.parsed.y} cards`;
                },
                footer: function (tooltipItems) {
                  let total = 0;
                  tooltipItems.forEach(item => {
                    if (item.dataset.label !== 'Total Activity') {
                      total += item.parsed.y;
                    }
                  });
                  return `Month Total: ${total} cards`;
                },
              },
            },
          },
          scales: {
            y: {
              beginAtZero: true,
              title: {
                display: true,
                text: 'Number of Work Orders',
                font: { size: 12, weight: 'bold' },
              },
              ticks: {
                precision: 0,
                font: { size: 11 },
              },
              grid: {
                color: 'rgba(0, 0, 0, 0.05)',
              },
            },
            x: {
              title: {
                display: true,
                text: 'Month',
                font: { size: 12, weight: 'bold' },
              },
              ticks: {
                font: { size: 11 },
              },
              grid: {
                display: false,
              },
            },
          },
        },
      });
    }
  }
});

// Weekly dropdown reload when year changes
function initializeYearWeekHandler() {
  const yearField = document.getElementById('id_year');
  const weekField = document.getElementById('id_week');

  if (yearField && weekField) {
    const newYearField = yearField.cloneNode(true);
    yearField.parentNode.replaceChild(newYearField, yearField);

    newYearField.addEventListener('change', function () {
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
          })
          .catch(err => console.error('Error fetching weekly choices:', err));
      }
    });
  }
}

// Basic form validation
function initializeFormValidation() {
  const form = document.getElementById('filter-form');
  if (!form) return;

  form.addEventListener('submit', function (e) {
    const yearInput = document.getElementById('id_year');
    if (yearInput && !yearInput.value) {
      e.preventDefault();
      alert('Please select a year.');
    }
  });
}

// AJAX Report Fetching
document.addEventListener('DOMContentLoaded', function () {
  const filterForm = document.getElementById('filter-form');
  const pdfBtn = document.getElementById('download-pdf');
  const csvBtn = document.getElementById('export-csv');

  if (!filterForm) return;

  initializeFilters();
  initializeYearWeekHandler();
  initializeFormValidation();
  attachFilterListeners();

  // Update download button URLs with current filter parameters
  function updateDownloadLinks() {
    const formData = new FormData(filterForm);
    const params = new URLSearchParams(formData).toString();

    if (pdfBtn) {
      const pdfBaseUrl = pdfBtn.getAttribute('data-base-url') || pdfBtn.href.split('?')[0];
      pdfBtn.href = pdfBaseUrl + '?' + params;
    }
    if (csvBtn) {
      const csvBaseUrl = csvBtn.getAttribute('data-base-url') || csvBtn.href.split('?')[0];
      csvBtn.href = csvBaseUrl + '?' + params;
    }
  }

  async function fetchReport() {
    try {
      const formData = new FormData(filterForm);
      const params = new URLSearchParams(formData).toString();

      // Update download links immediately when filters change
      updateDownloadLinks();

      const response = await fetch(filterForm.action + '?' + params, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });

      if (!response.ok) {
        console.error('Report fetch failed:', response.status, response.statusText);
        const errorText = await response.text();
        console.error('Error details:', errorText);
        return;
      }

      const data = await response.json();

      if (data.error) {
        console.error('Server error:', data.error);
        alert('Error loading report: ' + data.error);
        return;
      }

      // Update table section
      const tableContainer = document.getElementById('table-container');
      if (tableContainer && data.table_html) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(data.table_html, 'text/html');
        const newTable = doc.querySelector('.table-container');
        if (newTable) {
          tableContainer.innerHTML = newTable.innerHTML;
        }
      }

      // Update charts section
      const chartContainer = document.getElementById('chart-container');
      if (chartContainer && data.chart_html) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(data.chart_html, 'text/html');
        const newCharts = doc.querySelector('.row');
        if (newCharts) {
          chartContainer.innerHTML = newCharts.innerHTML;

          // Re-initialize charts after DOM update
          if (data.chart_data) {
            updateStatusChart(data.chart_data);
            if (data.chart_data.annual_breakdown) {
              updateAnnualTrendChart(data.chart_data.annual_breakdown);
            }
          }
        }
      }

      // Update summary section
      const summaryContainer = document.getElementById('summary-container');
      if (summaryContainer && data.summary_html) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(data.summary_html, 'text/html');
        const newSummary = doc.querySelector('.card');
        if (newSummary) {
          summaryContainer.innerHTML = newSummary.innerHTML;
        }
      }

      // Update download buttons (fallback in case AJAX doesn't provide them)
      if (data.download_pdf && pdfBtn) {
        pdfBtn.href = data.download_pdf;
      } else {
        updateDownloadLinks(); // Ensure links are updated even if not in response
      }
      if (data.export_csv && csvBtn) {
        csvBtn.href = data.export_csv;
      } else {
        updateDownloadLinks(); // Ensure links are updated even if not in response
      }

      // Update chart visibility based on report type
      const reportTypeField = document.getElementById('id_report_type');
      if (reportTypeField) {
        toggleChartVisibility(reportTypeField.value);
      }
    } catch (error) {
      console.error('Error fetching report:', error);
    }
  }

  function updateStatusChart(chartData) {
    const ctx = document.getElementById('statusChart');
    if (!ctx) return;

    if (statusChart) {
      statusChart.destroy();
      statusChart = null;
    }

    const totalWaiting = chartData.total_waiting || 0;
    const totalApproved = chartData.total_approved || 0;
    const totalDeclined = chartData.total_declined || 0;

    if (totalWaiting > 0 || totalApproved > 0 || totalDeclined > 0) {
      statusChart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: ['Waiting Approval', 'Approved', 'Declined'],
          datasets: [
            {
              label: 'Work Orders',
              data: [totalWaiting, totalApproved, totalDeclined],
              backgroundColor: ['#fbbf24', '#27c24c', '#e55353'],
              borderColor: ['#f59e0b', '#1e8e3e', '#b32d2d'],
              borderWidth: 2,
              barThickness: 50,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: 'rgba(0, 0, 0, 0.8)',
              padding: 12,
              titleFont: { size: 14 },
              bodyFont: { size: 13 },
              callbacks: {
                label: function (context) {
                  const total = totalWaiting + totalApproved + totalDeclined;
                  const percentage = total > 0 ? ((context.parsed.y / total) * 100).toFixed(1) : 0;
                  return `${context.parsed.y} cards (${percentage}%)`;
                },
              },
            },
          },
          scales: {
            y: {
              beginAtZero: true,
              title: {
                display: true,
                text: 'Number of Work Orders',
                font: { size: 12, weight: 'bold' },
              },
              ticks: { precision: 0 },
            },
            x: {
              title: { display: true, text: 'Status', font: { size: 12, weight: 'bold' } },
            },
          },
        },
      });
    }
  }

  function updateAnnualTrendChart(annualBreakdown) {
    const annualCtx = document.getElementById('annualTrendChart');
    if (!annualCtx) return;

    if (annualTrendChart) {
      annualTrendChart.destroy();
      annualTrendChart = null;
    }

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
    const waitingData = [];
    const approvedData = [];
    const declinedData = [];
    const totalData = [];

    for (let i = 1; i <= 12; i++) {
      const m = annualBreakdown[i] || { waiting: 0, approved: 0, declined: 0, total: 0 };
      waitingData.push(m.waiting || 0);
      approvedData.push(m.approved || 0);
      declinedData.push(m.declined || 0);
      totalData.push(m.total || 0);
    }

    annualTrendChart = new Chart(annualCtx, {
      type: 'line',
      data: {
        labels: months,
        datasets: [
          {
            label: 'Waiting Approval',
            data: waitingData,
            borderColor: '#fbbf24',
            backgroundColor: 'rgba(251, 191, 36, 0.15)',
            fill: true,
            tension: 0.4,
            borderWidth: 3,
            pointRadius: 4,
            pointHoverRadius: 6,
          },
          {
            label: 'Approved',
            data: approvedData,
            borderColor: '#27c24c',
            backgroundColor: 'rgba(39, 194, 76, 0.15)',
            fill: true,
            tension: 0.4,
            borderWidth: 3,
            pointRadius: 4,
            pointHoverRadius: 6,
          },
          {
            label: 'Declined',
            data: declinedData,
            borderColor: '#e55353',
            backgroundColor: 'rgba(229, 83, 83, 0.15)',
            fill: true,
            tension: 0.4,
            borderWidth: 3,
            pointRadius: 4,
            pointHoverRadius: 6,
          },
          {
            label: 'Total Activity',
            data: totalData,
            borderColor: '#6366f1',
            backgroundColor: 'transparent',
            fill: false,
            tension: 0.4,
            borderWidth: 3,
            borderDash: [8, 4],
            pointRadius: 5,
            pointHoverRadius: 7,
            pointStyle: 'circle',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false,
        },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: {
              usePointStyle: true,
              padding: 15,
              font: { size: 11, weight: '600' },
              boxWidth: 8,
              boxHeight: 8,
            },
          },
          tooltip: {
            backgroundColor: 'rgba(0, 0, 0, 0.85)',
            padding: 12,
            titleFont: { size: 13, weight: 'bold' },
            bodyFont: { size: 12 },
            borderColor: '#cbd5e1',
            borderWidth: 1,
            callbacks: {
              label: function (context) {
                return `${context.dataset.label}: ${context.parsed.y} cards`;
              },
              footer: function (tooltipItems) {
                let total = 0;
                tooltipItems.forEach(item => {
                  if (item.dataset.label !== 'Total Activity') {
                    total += item.parsed.y;
                  }
                });
                return `Month Total: ${total} cards`;
              },
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            title: {
              display: true,
              text: 'Number of Work Orders',
              font: { size: 12, weight: 'bold' },
            },
            ticks: {
              precision: 0,
              font: { size: 11 },
            },
            grid: {
              color: 'rgba(0, 0, 0, 0.05)',
            },
          },
          x: {
            title: {
              display: true,
              text: 'Month',
              font: { size: 12, weight: 'bold' },
            },
            ticks: {
              font: { size: 11 },
            },
            grid: {
              display: false,
            },
          },
        },
      },
    });
  }

  let debounceTimer;
  function attachFilterListeners() {
    const inputs = filterForm.querySelectorAll('select, input');
    inputs.forEach(input => {
      input.addEventListener('change', function () {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          fetchReport();
          updateDownloadLinks(); // Also update links on any filter change
        }, 300);
      });
    });
  }

  // Initialize download links on page load
  updateDownloadLinks();
});
