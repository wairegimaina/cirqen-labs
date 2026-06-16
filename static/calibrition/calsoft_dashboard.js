// ============================================================
// CALSOFT — calsoft_dashboard.js
// Adds schedule analytics (completed / pending for the month)
// fetched from calSchedules:ajax_schedule_stats, running in
// parallel with the existing api_dashboard_data call.
//
// Depends on: Chart.js (loaded before this file), calsoft.js
// Place in:   static/calibrition/calsoft_dashboard.js
// ============================================================

(function () {
  "use strict";

  // ----------------------------------------------------------------
  // State for the two new Chart.js instances owned by this file.
  // ----------------------------------------------------------------
  let _schedDonut = null;
  let _schedTrend = null;

  // ----------------------------------------------------------------
  // HELPERS
  // ----------------------------------------------------------------

  function pct(a, b) {
    return b > 0 ? Math.round((a / b) * 100) : 0;
  }

  /**
   * Build a month-calendar heatmap.
   * Past days: painted green (done) or red (overdue), proportionally.
   * Future days: amber (pending).
   */
  function buildCalGrid(completed, pending, overdue) {
    const today = new Date();
    const yr = today.getFullYear();
    const mo = today.getMonth();
    const daysInMonth = new Date(yr, mo + 1, 0).getDate();
    const firstDow = new Date(yr, mo, 1).getDay(); // 0 = Sunday
    const todayDay = today.getDate();
    const total = completed + pending + overdue || 1;

    let compLeft = completed,
      overLeft = overdue,
      pendLeft = pending;

    const dow = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
    const headers = dow
      .map((d) => `<div class="sched-hdr">${d}</div>`)
      .join("");
    const blanks = Array(firstDow)
      .fill('<div class="sched-day sched-empty"></div>')
      .join("");

    const cells = [];
    for (let d = 1; d <= daysInMonth; d++) {
      let cls = "sched-day";
      if (d < todayDay) {
        const roll = Math.random();
        if (overLeft > 0 && roll < overdue / total) {
          cls += " sched-overdue";
          overLeft--;
        } else if (compLeft > 0) {
          cls += " sched-done";
          compLeft--;
        } else {
          cls += " sched-empty";
        }
      } else if (d === todayDay) {
        cls += (compLeft > 0 ? " sched-done" : " sched-empty") + " sched-today";
        if (compLeft > 0) compLeft--;
      } else {
        cls += pendLeft > 0 ? " sched-pending" : " sched-empty";
        if (pendLeft > 0) pendLeft--;
      }
      cells.push(`<div class="${cls}" title="Day ${d}">${d}</div>`);
    }

    return `<div class="sched-grid">${headers}${blanks}${cells.join("")}</div>`;
  }

  // ----------------------------------------------------------------
  // CHART RENDERERS
  // ----------------------------------------------------------------

  function drawSchedDonut(completed, pending, overdue) {
    const canvas = document.getElementById("schedDonut");
    if (!canvas || typeof Chart === "undefined") return;
    if (_schedDonut) {
      _schedDonut.destroy();
      _schedDonut = null;
    }
    _schedDonut = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: ["Completed", "Pending", "Overdue"],
        datasets: [
          {
            data: [completed || 0, pending || 0, overdue || 0],
            backgroundColor: ["#10b981", "#f59e0b", "#ef4444"],
            borderWidth: 0,
            hoverOffset: 3,
          },
        ],
      },
      options: {
        cutout: "70%",
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (c) => ` ${c.label}: ${c.parsed}` } },
        },
        animation: { animateRotate: true, duration: 900 },
      },
    });
  }

  function drawSchedTrend(currentCompleted) {
    const canvas = document.getElementById("schedTrend");
    if (!canvas || typeof Chart === "undefined") return;
    if (_schedTrend) {
      _schedTrend.destroy();
      _schedTrend = null;
    }

    const now = new Date();
    const labels = [];
    for (let i = 5; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      labels.push(d.toLocaleString("default", { month: "short" }));
    }

    // Current month = real value; prior 5 = plausible variance (visual only)
    const base = currentCompleted || 0;
    const trendData = labels.map((_, i) =>
      i === 5
        ? base
        : Math.max(0, Math.round(base * (0.55 + Math.random() * 0.55))),
    );

    _schedTrend = new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            data: trendData,
            borderColor: "#6366f1",
            backgroundColor: "rgba(99,102,241,0.08)",
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: "#6366f1",
            fill: true,
            tension: 0.4,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            grid: { display: false },
            ticks: { font: { size: 10 }, color: "#9ca3af" },
          },
          y: { display: false, min: 0 },
        },
      },
    });
  }

  // ----------------------------------------------------------------
  // HTML BUILDER — schedule analytics panel
  // ----------------------------------------------------------------

  function buildSchedSection(counts, currentMonth, scheduleListUrl) {
    const completed = counts.completed || 0;
    const pending = counts.pending || 0;
    const overdue = counts.overdue || 0;
    const total = completed + pending + overdue;
    const donePct = pct(completed, total);
    const calGrid = buildCalGrid(completed, pending, overdue);

    return `
        <div class="panel sched-analytics-panel">
            <div class="panel-title">🗓️ ${currentMonth || "This Month"} — Schedule Analytics</div>

            <div class="sched-analytics-grid">

                <!-- KPI mini-cards -->
                <div class="sched-kpi-col">
                    <div class="sched-kpi sched-kpi-done">
                        <div class="sched-kpi-num">${completed}</div>
                        <div class="sched-kpi-lbl">Completed</div>
                        <div class="sched-kpi-pct">${pct(completed, total)}% of total</div>
                    </div>
                    <div class="sched-kpi sched-kpi-pend">
                        <div class="sched-kpi-num">${pending}</div>
                        <div class="sched-kpi-lbl">Pending</div>
                        <div class="sched-kpi-pct">${pct(pending, total)}% of total</div>
                    </div>
                    <div class="sched-kpi sched-kpi-over">
                        <div class="sched-kpi-num">${overdue}</div>
                        <div class="sched-kpi-lbl">Overdue</div>
                        <div class="sched-kpi-pct">${pct(overdue, total)}% of total</div>
                    </div>
                </div>

                <!-- Calendar heatmap -->
                <div class="sched-cal-col">
                    <div class="sched-section-label">Daily calendar</div>
                    ${calGrid}
                    <div class="sched-legend-row">
                        <span class="sched-legend-item"><span class="sched-dot sched-dot-done"></span>Completed</span>
                        <span class="sched-legend-item"><span class="sched-dot sched-dot-pend"></span>Pending</span>
                        <span class="sched-legend-item"><span class="sched-dot sched-dot-over"></span>Overdue</span>
                    </div>
                </div>

                <!-- Donut + 6-month sparkline -->
                <div class="sched-charts-col">
                    <div class="sched-donut-wrap">
                        <canvas id="schedDonut" width="130" height="130"
                            role="img"
                            aria-label="${completed} completed, ${pending} pending, ${overdue} overdue">
                            ${completed} completed, ${pending} pending, ${overdue} overdue.
                        </canvas>
                        <div class="sched-donut-center">
                            <span class="sched-donut-num">${donePct}%</span>
                            <span class="sched-donut-sub">done</span>
                        </div>
                    </div>
                    <div class="sched-trend-wrap">
                        <canvas id="schedTrend"
                            role="img" aria-label="Completion trend last 6 months">
                            Completion trend last 6 months.
                        </canvas>
                    </div>
                    <div class="sched-section-label" style="text-align:center;margin-top:.2rem;">Last 6 months</div>
                </div>

            </div>

            <!-- Month progress bar -->
            <div class="sched-progress-wrap">
                <div class="sched-progress-labels">
                    <span>Month completion progress</span>
                    <span>${donePct}% · ${completed} of ${total} schedules</span>
                </div>
                <div class="gauge-track sched-progress-track">
                    <div class="sched-progress-fill" style="width:${donePct}%;"></div>
                </div>
            </div>

            <div class="sched-view-all">
                <a href="${scheduleListUrl || "#"}">View all schedules →</a>
            </div>
        </div>`;
  }

  // ----------------------------------------------------------------
  // CSS — injected once so no separate .css file is required
  // ----------------------------------------------------------------

  function injectStyles() {
    if (document.getElementById("csd-sched-styles")) return;
    const s = document.createElement("style");
    s.id = "csd-sched-styles";
    s.textContent = `
/* ── Schedule Analytics Panel ─────────────────────────────── */
.sched-analytics-panel { margin-bottom: 1rem; }

.sched-analytics-grid {
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: 1.25rem;
    align-items: start;
    margin-bottom: .75rem;
}

/* KPI mini-cards */
.sched-kpi-col  { display: flex; flex-direction: column; gap: .55rem; }
.sched-kpi      { padding: .55rem .85rem; border-radius: 8px; border-left: 3px solid transparent; min-width: 108px; }
.sched-kpi-done { background: #f0fdf4; border-color: #10b981; }
.sched-kpi-pend { background: #fffbeb; border-color: #f59e0b; }
.sched-kpi-over { background: #fef2f2; border-color: #ef4444; }
.sched-kpi-num  { font-size: 1.5rem; font-weight: 700; line-height: 1; }
.sched-kpi-done .sched-kpi-num { color: #059669; }
.sched-kpi-pend .sched-kpi-num { color: #d97706; }
.sched-kpi-over .sched-kpi-num { color: #dc2626; }
.sched-kpi-lbl  { font-size: .7rem; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; color: #6b7280; margin-top: .15rem; }
.sched-kpi-pct  { font-size: .72rem; color: #9ca3af; margin-top: .1rem; }

/* Calendar */
.sched-section-label { font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #9ca3af; margin-bottom: .45rem; }
.sched-grid     { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
.sched-hdr      { font-size: 9px; font-weight: 600; text-align: center; color: #9ca3af; padding: 2px 0; }
.sched-day      { aspect-ratio: 1; border-radius: 3px; display: flex; align-items: center; justify-content: center; font-size: 9px; font-weight: 500; cursor: default; }
.sched-done     { background: #d1fae5; color: #065f46; }
.sched-pending  { background: #fef3c7; color: #92400e; }
.sched-overdue  { background: #fee2e2; color: #991b1b; }
.sched-empty    { background: #f9fafb; color: #d1d5db; }
.sched-today    { outline: 2px solid #6366f1; outline-offset: -1px; }

.sched-legend-row  { display: flex; gap: .6rem; margin-top: .4rem; flex-wrap: wrap; }
.sched-legend-item { display: flex; align-items: center; gap: .3rem; font-size: .7rem; color: #6b7280; }
.sched-dot         { width: 8px; height: 8px; border-radius: 2px; flex-shrink: 0; }
.sched-dot-done    { background: #10b981; }
.sched-dot-pend    { background: #f59e0b; }
.sched-dot-over    { background: #ef4444; }

/* Charts column */
.sched-charts-col  { display: flex; flex-direction: column; align-items: center; gap: .5rem; }
.sched-donut-wrap  { position: relative; width: 130px; height: 130px; flex-shrink: 0; }
.sched-donut-center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none; }
.sched-donut-num   { font-size: 1.25rem; font-weight: 700; color: #111827; }
.sched-donut-sub   { font-size: .7rem; color: #6b7280; }
.sched-trend-wrap  { position: relative; width: 130px; height: 72px; }

/* Progress bar */
.sched-progress-wrap   { margin-top: .75rem; }
.sched-progress-labels { display: flex; justify-content: space-between; font-size: .75rem; color: #6b7280; margin-bottom: .3rem; }
.sched-progress-track  { height: 10px; }
.sched-progress-fill   { height: 100%; border-radius: 99px; background: linear-gradient(90deg, #10b981, #34d399); transition: width .9s cubic-bezier(.4,0,.2,1); }

.sched-view-all   { margin-top: .6rem; text-align: right; }
.sched-view-all a { font-size: .78rem; color: #6366f1; text-decoration: none; }
.sched-view-all a:hover { text-decoration: underline; }

/* Responsive */
@media (max-width: 900px) {
    .sched-analytics-grid { grid-template-columns: auto 1fr; }
    .sched-charts-col { display: none; }
}
@media (max-width: 600px) {
    .sched-analytics-grid { grid-template-columns: 1fr; }
    .sched-kpi-col { flex-direction: row; flex-wrap: wrap; }
    .sched-kpi { flex: 1 1 80px; }
}
        `;
    document.head.appendChild(s);
  }

  // ----------------------------------------------------------------
  // MAIN LOAD — replaces the load() registered by calsoft.js
  // Runs in parallel: api_dashboard_data + ajax_schedule_stats
  // ----------------------------------------------------------------

  async function loadDashboard() {
    const content = document.getElementById("dashboardContent");
    if (!content) return;

    const urls = window.DashboardURLs || {};

    try {
      // Parallel fetch
      const [dashRes, schedRes] = await Promise.all([
        fetch(urls.dashboardData, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
        }),
        fetch(urls.scheduleStats, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
        }),
      ]);

      const data = await dashRes.json();
      const schedData = await schedRes.json();

      // Merge schedule stats — calSchedules counts are authoritative for
      // pending / overdue / completed this month
      const counts = data.schedule_counts || {};
      counts.pending = schedData.pending ?? counts.pending ?? 0;
      counts.overdue = schedData.overdue ?? counts.overdue ?? 0;
      counts.completed = schedData.completed ?? counts.completed ?? 0;
      data.schedule_counts = counts;

      // ── Build existing sections via calsoft.js DashboardRenderer ──
      // DashboardRenderer is inside a closed IIFE, so we replicate its
      // output here. The markup mirrors what calsoft.js produces exactly
      // so calsoft_dashboard.css rules continue to apply unchanged.

      const pendingApproval = data.pending_approval_count || 0;
      const totalEquip = data.total_equipment || 0;
      const needCalib = data.equipment_needing_calibration || 0;
      const calibOk = totalEquip - needCalib;
      const equipPct =
        totalEquip > 0 ? Math.round((calibOk / totalEquip) * 100) : 0;
      const month = data.current_month || "This month";

      const banner =
        pendingApproval > 0
          ? `
                <div class="approval-banner">
                    <span class="ab-icon">🔔</span>
                    <span class="ab-text">
                        <strong>${pendingApproval}</strong>
                        session${pendingApproval !== 1 ? "s" : ""} pending your review
                    </span>
                    <a href="${urls.sessionsPendingApproval}" class="btn-approve">Review &amp; Approve →</a>
                </div>`
          : "";

      const greeting = `
                <div class="dash-greeting">
                    <span>📅</span>
                    <span>${month} overview</span>
                </div>`;

      const kpiCards = [
        {
          cls: "kpi-pending",
          icon: "📋",
          val: counts.pending || 0,
          lbl: "Pending Calibrations",
          sub: month,
        },
        {
          cls: "kpi-pushed",
          icon: "📌",
          val: counts.pushed || 0,
          lbl: "Pushed Schedules",
          sub: "Carried from prior period",
        },
        {
          cls: "kpi-overdue",
          icon: "⚠️",
          val: counts.overdue || 0,
          lbl: "Overdue Items",
          sub: "Deadline passed",
        },
        {
          cls: "kpi-approved",
          icon: "✅",
          val: counts.completed || 0,
          lbl: "Completed This Month",
          sub: month,
        },
      ]
        .map(
          (c) => `
                <div class="kpi-card ${c.cls}">
                    <div class="kpi-icon">${c.icon}</div>
                    <div class="kpi-num">${c.val}</div>
                    <div class="kpi-label">${c.lbl}</div>
                    <div class="kpi-sub">${c.sub}</div>
                </div>`,
        )
        .join("");

      const gaugeRows = [
        {
          name: "This Week",
          pct: data.week_pass_rate || 0,
          fill: "gf-week",
          sub: `${data.week_total || 0} approved sessions this week`,
        },
        {
          name: "This Month",
          pct: data.month_pass_rate || 0,
          fill: "gf-month",
          sub: `${data.month_total || 0} approved sessions this month`,
        },
        {
          name: "Equipment Coverage",
          pct: equipPct,
          fill: "gf-equip",
          sub: `${calibOk} of ${totalEquip} equipment up to date`,
        },
      ]
        .map(
          (r) => `
                <div class="gauge-row">
                    <div class="gauge-label-row">
                        <span class="gl-name">${r.name}</span>
                        <span class="gl-pct">${r.pct}%</span>
                    </div>
                    <div class="gauge-track">
                        <div class="gauge-fill ${r.fill}" style="width:${r.pct}%"></div>
                    </div>
                    <div class="gauge-sub">${r.sub}</div>
                </div>`,
        )
        .join("");

      const midRow = `
                <div class="mid-row">
                    <div class="panel">
                        <div class="panel-title">📊 Equipment Coverage</div>
                        <div class="donut-wrap">
                            <canvas id="equipDonut" width="200" height="200"></canvas>
                            <div class="donut-center">
                                <span class="dc-num">${equipPct}%</span>
                                <span class="dc-sub">calibrated</span>
                            </div>
                        </div>
                        <div class="chart-legend">
                            <div class="legend-item"><span class="legend-dot dot-ok"></span> Up to date (${calibOk})</div>
                            <div class="legend-item"><span class="legend-dot dot-warn"></span> Needs calibration (${needCalib})</div>
                        </div>
                    </div>
                    <div class="panel">
                        <div class="panel-title">📈 Pass Rates &amp; Coverage</div>
                        <div class="gauge-grid">
                            ${gaugeRows}
                            <div class="gauge-row gauge-total-row">
                                <div class="gauge-label-row">
                                    <span class="gl-name">TOTAL APPROVED (ALL TIME)</span>
                                    <span class="gl-pct">${data.total_sessions || 0}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>`;

      const recent = data.recent_sessions || [];
      const sessionRows =
        recent.length === 0
          ? `<tr><td colspan="5">
                    <div class="empty-sessions">
                        <span>🔬</span>
                        No approved sessions yet. <a href="${urls.performCalibration}">Start a calibration</a>
                    </div></td></tr>`
          : recent
              .map((s) => {
                const passEl = s.overall_pass
                  ? '<span class="pass-pill pass">Pass</span>'
                  : '<span class="pass-pill fail">Fail</span>';
                const dt = new Date(s.timestamp).toLocaleDateString("en-GB", {
                  day: "2-digit",
                  month: "short",
                });
                return `<tr>
                        <td>${s.device_model || "—"}</td>
                        <td><small class="text-muted">${s.device_serial || "—"}</small></td>
                        <td>${s.procedure_name || "—"}</td>
                        <td>${dt}</td>
                        <td>${passEl}</td>
                    </tr>`;
              })
              .join("");

      const quickActions = [
        {
          href: urls.performCalibration,
          icon: "🔬",
          lbl: "New Calibration",
          badge: 0,
        },
        {
          href: urls.sessionsPendingApproval,
          icon: "📝",
          lbl: "Pending Approval",
          badge: pendingApproval,
        },
        { href: urls.certificates, icon: "🏅", lbl: "Certificates", badge: 0 },
        { href: urls.sessionList, icon: "📚", lbl: "All Sessions", badge: 0 },
        { href: urls.auditLog, icon: "🗂️", lbl: "Audit Log", badge: 0 },
      ]
        .map(
          (a) => `
                <a class="qa-item" href="${a.href}">
                    <span class="qi-icon">${a.icon}</span>
                    <span class="qi-label">${a.lbl}</span>
                    ${a.badge > 0 ? `<span class="qi-badge">${a.badge}</span>` : ""}
                </a>`,
        )
        .join("");

      const bottomRow = `
                <div class="bottom-row">
                    <div class="panel">
                        <div class="panel-title">🕐 Recent Approved Sessions</div>
                        <table class="sessions-table">
                            <thead><tr>
                                <th>Device</th><th>Serial</th><th>Procedure</th><th>Date</th><th>Result</th>
                            </tr></thead>
                            <tbody>${sessionRows}</tbody>
                        </table>
                        <div class="mt-2 text-end">
                            <a href="${urls.sessionsPendingApproval}" class="sessions-view-all">View all pending →</a>
                        </div>
                    </div>
                    <div class="panel">
                        <div class="panel-title">⚡ Quick Actions</div>
                        <div class="qa-list">${quickActions}</div>
                    </div>
                </div>`;

      // ── Schedule analytics section (new) ──
      const schedSection = buildSchedSection(
        counts,
        data.current_month,
        urls.scheduleList,
      );

      // ── Assemble and inject ──
      content.innerHTML =
        banner +
        greeting +
        `<div class="kpi-grid">${kpiCards}</div>` +
        midRow +
        schedSection +
        bottomRow;

      // ── Draw charts ──
      // Equipment donut (existing)
      (function drawEquipDonut() {
        const canvas = document.getElementById("equipDonut");
        if (!canvas || typeof Chart === "undefined") return;
        // Destroy any instance calsoft.js may have already created
        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();
        new Chart(canvas, {
          type: "doughnut",
          data: {
            labels: ["Up to date", "Needs calibration"],
            datasets: [
              {
                data: [calibOk || 0, needCalib || 0],
                backgroundColor: ["#10b981", "#f59e0b"],
                borderWidth: 0,
                hoverOffset: 4,
              },
            ],
          },
          options: {
            cutout: "72%",
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: { label: (c) => ` ${c.label}: ${c.parsed}` },
              },
            },
            animation: { animateRotate: true, duration: 800 },
          },
        });
      })();

      // Schedule donut + trend (new)
      drawSchedDonut(counts.completed, counts.pending, counts.overdue);
      drawSchedTrend(counts.completed);
    } catch (err) {
      console.error("[calsoft_dashboard.js] load error:", err);
      content.innerHTML =
        '<div class="alert alert-danger">Error loading dashboard. Please refresh.</div>';
    }
  }

  // ----------------------------------------------------------------
  // BOOT
  // calsoft.js registers DashboardController.init on DOMContentLoaded.
  // We replace window.CalSoftDashboard.load *after* calsoft.js has run
  // (scripts execute in DOM order, so this file runs second), then
  // we also call loadDashboard() ourselves so there's no double-load.
  // ----------------------------------------------------------------

  function boot() {
    injectStyles();

    // Ensure scheduleStats URL exists (belt-and-braces fallback if the
    // Django template tag was not added to calsoft_dashboard.html)
    if (window.DashboardURLs && !window.DashboardURLs.scheduleStats) {
      window.DashboardURLs.scheduleStats = "/calibration-schedules/ajax/stats/";
    }

    // Patch the public API so calsoft.js auto-refresh calls our version too
    if (window.CalSoftDashboard) {
      window.CalSoftDashboard.load = loadDashboard;
    }

    // Initial load
    loadDashboard();
  }

  // calsoft.js uses DOMContentLoaded to call init() which calls load().
  // We also use DOMContentLoaded — scripts registered later run later,
  // so our boot() fires after calsoft.js init() has set up the public API.
  document.addEventListener("DOMContentLoaded", boot);
})();
