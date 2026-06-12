
// Show/hide filter fields by report type
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
    const reportType = document.querySelector('#id_report_type');
    const weeklyFields = document.querySelectorAll('.weekly-fields');
    const monthlyFields = document.querySelectorAll('.monthly-fields');
    const quarterlyFields = document.querySelectorAll('.quarterly-fields');

    function toggleFields() {
        const type = reportType.value;
        weeklyFields.forEach(f => f.style.display = type === 'weekly' ? 'block' : 'none');
        monthlyFields.forEach(f => f.style.display = type === 'monthly' ? 'block' : 'none');
        quarterlyFields.forEach(f => f.style.display = type === 'quarterly' ? 'block' : 'none');

        if (type === 'annual') {
            weeklyFields.forEach(f => f.style.display = 'none');
            monthlyFields.forEach(f => f.style.display = 'none');
            quarterlyFields.forEach(f => f.style.display = 'none');
        }
    }

    toggleFields();
    reportType.addEventListener('change', toggleFields);
});

// -------------------------------
// Auto-fill current period (week, month, quarter, year)
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
    const reportType = document.querySelector('#id_report_type');
    const weekField = document.querySelector('#id_week');
    const monthField = document.querySelector('#id_month');
    const quarterField = document.querySelector('#id_quarter');
    const yearField = document.querySelector('#id_year');

    function setCurrentPeriod(type) {
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

    setCurrentPeriod(reportType.value);
    reportType.addEventListener('change', function () {
        setCurrentPeriod(this.value);
    });
});

// -------------------------------
// Status Chart (Waiting Approval vs Approved)
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
    const cfg = window.reportConfig || {};
    if ((cfg.totalWaitingApproval > 0) || (cfg.totalApproved > 0)) {
        const ctx = document.getElementById('statusChart');
        if (ctx) {
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: ['Waiting Approval', 'Approved'],
                    datasets: [{
                        label: 'Job Cards',
                        data: [cfg.totalWaitingApproval, cfg.totalApproved],
                        backgroundColor: ['#e55353', '#27c24c'],
                        borderColor: ['#b32d2d', '#1e8e3e'],
                        borderWidth: 1,
                        barThickness: 30
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { beginAtZero: true, title: { display: true, text: 'Number of Job Cards' } },
                        x: { title: { display: true, text: 'Status' } }
                    }
                }
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
            const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            const waitingData = [], approvedData = [], totalData = [];

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
                        { label: 'Total Activity', data: totalData, borderColor: '#6366f1', fill: false }
                    ]
                }
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
                    })
                    .catch(err => console.error('Error fetching weekly choices:', err));
            }
        });
    }
});

// -------------------------------
// Basic form validation
// -------------------------------
document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('filter-form');
    if (!form) return;

    form.addEventListener('submit', function (e) {
        const year = document.getElementById('id_year').value;
        if (!year) {
            e.preventDefault();
            alert('Please select a year.');
        }
    });
});

// -------------------------------


document.addEventListener("DOMContentLoaded", function () {
  const filterForm = document.getElementById("filter-form");
  const pdfBtn = document.getElementById("download-pdf");
  const csvBtn = document.getElementById("export-csv");

  async function fetchReport() {
    const formData = new FormData(filterForm);
    const params = new URLSearchParams(formData).toString();

    const response = await fetch(filterForm.action + "?" + params, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });

    if (!response.ok) return;
    const data = await response.json();

    // Update sections
    document.querySelector(".table-container").outerHTML = data.table_html;
    document.querySelector(".card.mb-4").outerHTML = data.summary_html;

    // Update download buttons
    pdfBtn.href = data.download_pdf;
    csvBtn.href = data.export_csv;
  }

  // Auto-fetch on filter change
  const inputs = filterForm.querySelectorAll("select, input");
  inputs.forEach((input) => {
    input.addEventListener("change", fetchReport);
  });

  // Initial load
  fetchReport();
});


let statusChart = null;

async function fetchReport() {
  const formData = new FormData(filterForm);
  const params = new URLSearchParams(formData).toString();

  const response = await fetch(filterForm.action + "?" + params, {
    headers: { "X-Requested-With": "XMLHttpRequest" },
  });

  if (!response.ok) return;
  const data = await response.json();

  // Update sections
  document.querySelector(".table-container").outerHTML = data.table_html;
  document.querySelector(".card.mb-4").outerHTML = data.summary_html;

  // Update download buttons
  pdfBtn.href = data.download_pdf;
  csvBtn.href = data.export_csv;

  // --- Chart update ---
  const ctx = document.getElementById("statusChart");
  if (ctx) {
    if (statusChart) statusChart.destroy(); // Clear old chart
    statusChart = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["Waiting Approval", "Approved"],
        datasets: [{
          data: [data.chart_data.total_waiting, data.chart_data.total_approved],
          backgroundColor: ["#f39c12", "#27ae60"]
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });
  }

  // --- Annual trend chart update ---
  if (data.chart_data.annual_breakdown) {
    const annualCtx = document.getElementById("annualTrendChart");
    if (annualCtx) {
      if (window.annualChart) window.annualChart.destroy();
      const months = Object.keys(data.chart_data.annual_breakdown);
      const approved = months.map(m => data.chart_data.annual_breakdown[m].approved);
      const waiting = months.map(m => data.chart_data.annual_breakdown[m].waiting);

      window.annualChart = new Chart(annualCtx, {
        type: "line",
        data: {
          labels: months,
          datasets: [
            { label: "Approved", data: approved, borderColor: "#27ae60", fill: false },
            { label: "Waiting", data: waiting, borderColor: "#f39c12", fill: false }
          ]
        },
        options: { responsive: true, maintainAspectRatio: false }
      });
    }
  }
}
