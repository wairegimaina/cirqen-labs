// CMMS Dashboard JavaScript
// All logic extracted from inline script

/* ── Chart defaults — read from the active theme ── */
const cssVar = (name, fallback) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

Chart.defaults.color = cssVar("--text-muted", "#6b7280");
Chart.defaults.borderColor = cssVar("--border-color", "#e5e7eb");
Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

const C_SUCCESS = cssVar("--success-color", "#047857");
const C_WARNING = cssVar("--warning-color", "#b45309");
const C_DANGER = cssVar("--danger-color", "#dc2626");
const C_INFO = cssVar("--info-color", "#0369a1");
const C_EMPTY = cssVar("--border-mid", "#d1d5db");
const C_GRID = cssVar("--border-color", "#e5e7eb");
const CATEGORY_PALETTE = [
  cssVar("--primary-color", "#15803d"),
  C_INFO,
  C_WARNING,
  "#6d28d9",
  "#0f766e",
  C_DANGER,
  "#c2410c",
];

/* ── Utility ── */
const $ = (id) => document.getElementById(id);

function setKPI(id, val) {
  const el = $(id);
  if (!el) return;
  el.classList.remove("loading");
  el.textContent = val ?? "—";
}

function setMeta(id, text, cls) {
  const el = $(id);
  if (!el) return;
  el.className = "kpi-meta" + (cls ? " " + cls : "");
  el.textContent = text;
}

/* ── Live clock ── */
function updateClock() {
  const now = new Date();
  const opts = {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  };
  const el = $("live-time");
  if (el) el.textContent = now.toLocaleString("en-KE", opts);
}
setInterval(updateClock, 1000);
updateClock();

/* ── Chart instances ── */
let chartTrend = null,
  chartCat = null,
  chartDonut = null;

function destroyCharts() {
  [chartTrend, chartCat, chartDonut].forEach((c) => c && c.destroy());
}

/* ═══════════════════════════════════════════
   LOAD ALL DATA
═══════════════════════════════════════════ */
async function loadAll() {
  destroyCharts();
  // Use embedded data if available, otherwise fetch
  if (window.analyticsData) {
    loadEquipmentAnalyticsFromData(window.analyticsData);
  } else {
    await loadEquipmentAnalytics();
  }
  if (window.ppmData) {
    loadPPMSummaryFromData(window.ppmData);
  } else {
    await loadPPMSummary();
  }
  if (window.inventoryData) {
    loadInventorySummaryFromData(window.inventoryData);
  } else {
    await loadInventorySummary();
  }
}

/* ── 1. Equipment analytics ─────────────────── */

// New function using embedded data
function loadEquipmentAnalyticsFromData(d) {
  setKPI("kpi-equipment", d.total_equipment);
  setKPI("kpi-active", d.active_equipment);
  setKPI("kpi-departments", d.total_departments);

  const activeRate = d.total_equipment
    ? Math.round((d.active_equipment / d.total_equipment) * 100)
    : 0;
  setMeta("kpi-active-meta", `↑ ${activeRate}% active rate`, "up");
  setMeta("kpi-departments-meta", `${d.total_departments} departments`, "");

  const labels = Object.keys(d.by_category);
  const values = Object.values(d.by_category);
  if (labels.length) {
    const palette = CATEGORY_PALETTE;
    chartCat = new Chart($("chart-equipment-cat"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Equipment",
            data: values,
            backgroundColor: labels.map(
              (_, i) => palette[i % palette.length],
            ),
            borderColor: labels.map((_, i) => palette[i % palette.length]),
            borderWidth: 0,
            borderRadius: 4,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxRotation: 30, font: { size: 10 } },
          },
          y: { grid: { color: C_GRID }, ticks: { precision: 0 } },
        },
      },
    });
  } else {
    fallbackCategoryChart();
  }
  renderActivityTable(d.top_equipment);
}

// Fetch fallback
async function loadEquipmentAnalytics() {
  try {
    const dash = document.querySelector(".cmms-dash");
    const url = dash ? dash.dataset.apiEquipment : null;
    if (!url) throw new Error("No API URL configured");
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();

    setKPI("kpi-equipment", d.total_equipment);
    setKPI("kpi-active", d.active_equipment);
    setKPI("kpi-departments", d.total_departments);

    const activeRate = d.total_equipment
      ? Math.round((d.active_equipment / d.total_equipment) * 100)
      : 0;
    setMeta("kpi-active-meta", `↑ ${activeRate}% active rate`, "up");
    setMeta("kpi-departments-meta", `${d.total_departments} departments`, "");

    const labels = Object.keys(d.by_category);
    const values = Object.values(d.by_category);
    if (labels.length) {
      const palette = CATEGORY_PALETTE;
      chartCat = new Chart($("chart-equipment-cat"), {
        type: "bar",
        data: {
          labels,
          datasets: [
            {
              label: "Equipment",
              data: values,
              backgroundColor: labels.map(
                (_, i) => palette[i % palette.length],
              ),
              borderColor: labels.map((_, i) => palette[i % palette.length]),
              borderWidth: 0,
              borderRadius: 4,
            },
          ],
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: {
            x: {
              grid: { display: false },
              ticks: { maxRotation: 30, font: { size: 10 } },
            },
            y: { grid: { color: C_GRID }, ticks: { precision: 0 } },
          },
        },
      });
    } else {
      fallbackCategoryChart();
    }
    renderActivityTable(d.top_equipment);
  } catch (e) {
    console.warn("Equipment analytics error", e);
    fallbackCategoryChart();
    setKPI("kpi-equipment", "—");
    setKPI("kpi-active", "—");
    setKPI("kpi-departments", "—");
    renderActivityTable([]);
  }
}

function fallbackCategoryChart() {
  const ctx = $("chart-equipment-cat");
  if (!ctx) return;
  chartCat = new Chart(ctx, {
    type: "bar",
    data: {
      labels: ["No data"],
      datasets: [{ data: [0], backgroundColor: C_EMPTY }],
    },
    options: { responsive: true, plugins: { legend: { display: false } } },
  });
}

function renderActivityTable(top) {
  const tbody = $("activity-table");
  if (!tbody) return;
  if (!top || !top.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="dash-empty">No data available</td></tr>`;
    return;
  }
  tbody.innerHTML = top
    .slice(0, 8)
    .map((eq) => {
      const jc = eq.job_cards || eq.jobcard_count || eq.count || 0;
      const status = eq.active_status ?? true;
      const badge = status
        ? '<span class="badge bg-success">Active</span>'
        : '<span class="badge bg-danger">Inactive</span>';
      return `<tr>
      <td>${eq.name || eq.description || eq.equipment_name || "—"}</td>
      <td class="text-muted">${eq.department || eq.department_name || "—"}</td>
      <td class="tabular">${jc}</td>
      <td>${badge}</td>
    </tr>`;
    })
    .join("");
}

/* ── 2. PPM summary ─────────────────────────── */

// New function using embedded data
function loadPPMSummaryFromData(d) {
  const total = d.total || 0;
  const completed = d.completed || 0;
  const overdue = d.overdue || 0;
  const pending = d.pending || 0;
  const upcoming = d.upcoming || 0;

  if (overdue > 0) {
    const banner = $("alert-banner");
    if (banner) {
      banner.style.display = "flex";
      $("alert-text").textContent =
        `${overdue} PPM schedule${overdue > 1 ? "s are" : " is"} overdue and require immediate attention.`;
    }
  }

  const statuses = [
    { label: "Completed", count: completed, color: C_SUCCESS },
    { label: "Pending", count: pending, color: C_WARNING },
    { label: "Overdue", count: overdue, color: C_DANGER },
    { label: "Upcoming", count: upcoming, color: C_INFO },
  ];

  const rows = document.getElementById("ppm-status-rows");
  if (rows) {
    rows.innerHTML = statuses
      .map((s) => {
        const pct = total > 0 ? Math.round((s.count / total) * 100) : 0;
        return `<div class="status-row">
          <span class="status-label">
            <span class="status-dot" style="background:${s.color}"></span>${s.label}
          </span>
          <div class="status-bar-wrap">
            <div class="status-bar" style="width:${pct}%;background:${s.color}"></div>
          </div>
          <span class="status-count">${s.count}</span>
        </div>`;
      })
      .join("");
  }

  const monthly = d.monthly_breakdown || d.monthly || d.trend || null;
  if (monthly && typeof monthly === "object") {
    const labels = Object.keys(monthly);
    const vals = Object.values(monthly).map((v) =>
      typeof v === "object" ? (v.completed ?? v.count ?? 0) : v,
    );
    renderTrendChart(labels, vals);
  } else {
    renderTrendChart(null, null);
  }
}

// Fetch fallback for PPM (identical to original)
async function loadPPMSummary() {
  try {
    const dash = document.querySelector(".cmms-dash");
    const url = dash ? dash.dataset.apiPpm : null;
    if (!url) throw new Error("No PPM API URL configured");
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();

    const total = d.total || 0;
    const completed = d.completed || 0;
    const overdue = d.overdue || 0;
    const pending = d.pending || 0;
    const upcoming = d.upcoming || 0;

    if (overdue > 0) {
      const banner = $("alert-banner");
      if (banner) {
        banner.style.display = "flex";
        $("alert-text").textContent =
          `${overdue} PPM schedule${overdue > 1 ? "s are" : " is"} overdue and require immediate attention.`;
      }
    }

    const statuses = [
      { label: "Completed", count: completed, color: C_SUCCESS },
      { label: "Pending", count: pending, color: C_WARNING },
      { label: "Overdue", count: overdue, color: C_DANGER },
      { label: "Upcoming", count: upcoming, color: C_INFO },
    ];

    const rows = document.getElementById("ppm-status-rows");
    if (rows) {
      rows.innerHTML = statuses
        .map((s) => {
          const pct = total > 0 ? Math.round((s.count / total) * 100) : 0;
          return `<div class="status-row">
            <span class="status-label">
              <span class="status-dot" style="background:${s.color}"></span>${s.label}
            </span>
            <div class="status-bar-wrap">
              <div class="status-bar" style="width:${pct}%;background:${s.color}"></div>
            </div>
            <span class="status-count">${s.count}</span>
          </div>`;
        })
        .join("");
    }

    const monthly = d.monthly_breakdown || d.monthly || d.trend || null;
    if (monthly && typeof monthly === "object") {
      const labels = Object.keys(monthly);
      const vals = Object.values(monthly).map((v) =>
        typeof v === "object" ? (v.completed ?? v.count ?? 0) : v,
      );
      renderTrendChart(labels, vals);
    } else {
      renderTrendChart(null, null);
    }
  } catch (e) {
    console.warn("PPM summary error", e);
    renderTrendChart(null, null);
    const rows = $("ppm-status-rows");
    if (rows)
      rows.innerHTML = `<p class="dash-empty">Unable to load PPM data</p>`;
  }
}

function renderTrendChart(labels, vals) {
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const now = new Date();
  const defLabels = Array.from({ length: 6 }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() - 5 + i, 1);
    return months[d.getMonth()];
  });
  const defVals = defLabels.map(() => 0);

  chartTrend = new Chart($("chart-ppm-trend"), {
    type: "line",
    data: {
      labels: labels ?? defLabels,
      datasets: [
        {
          label: "Completed PPMs",
          data: vals ?? defVals,
          borderColor: C_SUCCESS,
          backgroundColor: "transparent",
          borderWidth: 2,
          pointBackgroundColor: C_SUCCESS,
          pointRadius: 4,
          tension: 0.4,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false } },
        y: {
          grid: { color: C_GRID },
          ticks: { precision: 0 },
          beginAtZero: true,
        },
      },
    },
  });
}

/* ── 3. Inventory summary ───────────────────── */

function loadInventorySummaryFromData(d) {
  const equipment = d.equipment || 0;
  const accessories = d.accessories || 0;
  const tools = d.tools || 0;
  const working = d.equipment_working || 0;
  const notWorking = d.equipment_not_working || 0;
  const underRepair = d.equipment_under_repair || 0;

  const accessoriesStock =
    d.accessories_stock_count ?? d.accessories_in_stock ?? null;

  setKPI("kpi-accessories", accessoriesStock ?? accessories);
  setKPI("kpi-tools", tools);

  if (accessoriesStock !== null) {
    setMeta("kpi-accessories-meta", `${accessories} item types`, "");
  } else {
    setMeta("kpi-accessories-meta", "units in stock", "");
  }
  setMeta("kpi-tools-meta", `${tools} registered`, "");

  $("donut-total").textContent = equipment || "—";

  const segments = [
    { label: "Working", count: working, color: C_SUCCESS },
    { label: "Not Working", count: notWorking, color: C_DANGER },
    { label: "Under Repair", count: underRepair, color: C_WARNING },
  ].filter((s) => s.count > 0);

  if (!segments.length) {
    segments.push({ label: "No equipment", count: 1, color: C_EMPTY });
  }

chartDonut = new Chart($("chart-inventory-donut"), {
     type: "doughnut",
     data: {
       labels: segments.map((s) => s.label),
       datasets: [
         {
           data: segments.map((s) => s.count),
           backgroundColor: segments.map((s) => s.color),
           borderColor: segments.map((s) => s.color),
           borderWidth: 2,
           hoverOffset: 4,
         },
       ],
     },
     options: {
       responsive: true,
       cutout: "72%",
       plugins: {
         legend: { display: false },
         tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${ctx.raw}` } },
       },
     },
   });

  const legend = $("inventory-legend");
  if (legend) {
    legend.innerHTML = segments
      .map(
        (s) => `
        <div class="legend-item">
          <span class="legend-dot" style="background:${s.color}"></span>
          <span>${s.label}</span>
          <span class="legend-count">${s.count}</span>
        </div>
      `,
      )
      .join("");
  }
}

// Fetch fallback
async function loadInventorySummary() {
  try {
    const dash = document.querySelector(".cmms-dash");
    const url = dash ? dash.dataset.apiInventory : null;
    if (!url) throw new Error("No inventory API URL configured");
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();

    const equipment = d.equipment || 0;
    const accessories = d.accessories || 0;
    const tools = d.tools || 0;
    const working = d.equipment_working || 0;
    const notWorking = d.equipment_not_working || 0;
    const underRepair = d.equipment_under_repair || 0;

    const accessoriesStock =
      d.accessories_stock_count ?? d.accessories_in_stock ?? null;

    setKPI("kpi-accessories", accessoriesStock ?? accessories);
    setKPI("kpi-tools", tools);

    if (accessoriesStock !== null) {
      setMeta("kpi-accessories-meta", `${accessories} item types`, "");
    } else {
      setMeta("kpi-accessories-meta", "units in stock", "");
    }
    setMeta("kpi-tools-meta", `${tools} registered`, "");

    $("donut-total").textContent = equipment || "—";

    const segments = [
      { label: "Working", count: working, color: C_SUCCESS },
      { label: "Not Working", count: notWorking, color: C_DANGER },
      { label: "Under Repair", count: underRepair, color: C_WARNING },
    ].filter((s) => s.count > 0);

    if (!segments.length) {
      segments.push({ label: "No equipment", count: 1, color: C_EMPTY });
    }

    chartDonut = new Chart($("chart-inventory-donut"), {
      type: "doughnut",
      data: {
        labels: segments.map((s) => s.label),
        datasets: [
          {
            data: segments.map((s) => s.count),
            backgroundColor: segments.map((s) => s.color),
            borderColor: segments.map((s) => s.color),
            borderWidth: 2,
            hoverOffset: 4,
          },
        ],
      },
      options: {
        responsive: true,
        cutout: "72%",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: { label: (ctx) => ` ${ctx.label}: ${ctx.raw}` },
          },
        },
      },
    });

    const legend = $("inventory-legend");
    if (legend) {
      legend.innerHTML = segments
        .map(
          (s) => `
          <div class="legend-item">
            <span class="legend-dot" style="background:${s.color}"></span>
            <span>${s.label}</span>
            <span class="legend-count">${s.count}</span>
          </div>
        `,
        )
        .join("");
    }
  } catch (e) {
    console.warn("Inventory summary error", e);
    setKPI("kpi-accessories", "—");
    setKPI("kpi-tools", "—");
    chartDonut = new Chart($("chart-inventory-donut"), {
      type: "doughnut",
      data: {
        labels: ["No data"],
        datasets: [
          {
            data: [1],
            backgroundColor: [C_EMPTY],
            borderColor: [C_EMPTY],
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        cutout: "72%",
        plugins: { legend: { display: false } },
      },
    });
    $("donut-total").textContent = "—";
  }
}

/* ── Boot ── */
document.addEventListener("DOMContentLoaded", () => {
  loadAll();
});
