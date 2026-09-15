(function () {
  "use strict";

  const CONFIG = {
    refreshInterval: 200000,
  };

  let refreshTimer = null;
  let statusChart = null;
  let lastCounts = null;

  // ============================================================
  // ICONS (inline local SVGs — no emoji, no external requests)
  // ============================================================
  const ICONS = {
    clipboard: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="3" width="12" height="4" rx="1"></rect><path d="M6 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-1"></path><path d="M9 12h6"></path><path d="M9 16h6"></path></svg>`,
    check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"></path></svg>`,
    chart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"></path><rect x="7" y="13" width="3" height="5"></rect><rect x="12" y="9" width="3" height="9"></rect><rect x="17" y="5" width="3" height="13"></rect></svg>`,
    badge: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="6"></circle><path d="M9.5 13.5 7 22l5-3 5 3-2.5-8.5"></path></svg>`,
    flask: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2h6"></path><path d="M10 2v7.31a2 2 0 0 1-.5 1.32L4.6 16.5a2 2 0 0 0 1.5 3.5h11.8a2 2 0 0 0 1.5-3.5l-4.9-5.87A2 2 0 0 1 14 9.31V2"></path><path d="M6.5 14h11"></path></svg>`,
    note: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z"></path></svg>`,
  };

  function icon(name) {
    return ICONS[name] || "";
  }

  function escapeHTML(value) {
    return String(value ?? "").replace(
      /[&<>"']/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[char],
    );
  }

  function pct(value, total) {
    return total > 0 ? Math.round((value / total) * 100) : 0;
  }

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value || fallback;
  }

  function pluralize(count, singular, plural = `${singular}s`) {
    return count === 1 ? singular : plural;
  }

  function shortDate(isoDate) {
    if (!isoDate) return "—";
    return new Date(isoDate).toLocaleDateString("en-GB", {
      day: "2-digit",
      month: "short",
    });
  }

  function mergeScheduleCounts(data, scheduleData) {
    const local = data.schedule_counts || {};
    return {
      pending: Number(scheduleData.pending ?? local.pending ?? 0),
      pushed: Number(local.pushed ?? 0),
      overdue: Number(scheduleData.overdue ?? local.overdue ?? 0),
      completed: Number(scheduleData.completed ?? local.completed ?? 0),
      total: Number(scheduleData.total ?? 0),
      warnings: Number(scheduleData.warnings ?? 0),
    };
  }

  // ============================================================
  // KPI CARDS — shared .kpi-card component (components.css)
  // ============================================================
  function renderKpiCards(data) {
    const cards = [
      {
        variant: "kpi-pending",
        icon: "clipboard",
        value: data.pending_approval_count || 0,
        label: "Pending approval",
        sub: "Sessions awaiting review",
      },
      {
        variant: "kpi-approved",
        icon: "check",
        value: data.month_total || 0,
        label: "Approved this month",
        sub: data.current_month || "This month",
      },
      {
        variant: "kpi-primary",
        icon: "chart",
        value: data.total_sessions || 0,
        label: "Total approved",
        sub: "All time",
      },
      {
        variant: "kpi-info",
        icon: "badge",
        value: data.awaiting_certificates || 0,
        label: "Awaiting certificates",
        sub: "Ready for certification",
      },
    ];

    return `
      <section class="kpi-grid">
        ${cards
          .map(
            (card) => `
          <article class="kpi-card ${card.variant}">
            <div class="kpi-icon">${icon(card.icon)}</div>
            <div class="kpi-num">${escapeHTML(card.value)}</div>
            <div class="kpi-label">${escapeHTML(card.label)}</div>
            <div class="kpi-sub">${escapeHTML(card.sub)}</div>
          </article>`,
          )
          .join("")}
      </section>`;
  }

  // ============================================================
  // APPROVAL BANNER
  // ============================================================
  function renderBanner(pendingApproval, urls) {
    if (!pendingApproval) return "";
    return `
      <div class="alert alert-warning cs-banner" role="status">
        <span><strong>${pendingApproval}</strong> ${pluralize(pendingApproval, "session")} waiting for your review</span>
        <a class="btn btn-secondary btn-sm" href="${escapeHTML(urls.sessionsPendingApproval)}">Review sessions</a>
      </div>`;
  }

  // ============================================================
  // SCHEDULE SNAPSHOT
  // ============================================================
  function renderSchedulePanel(counts, month) {
    const hasData = counts.pending + counts.overdue + counts.completed > 0;
    const total = counts.pending + counts.overdue + counts.completed || 1;
    const donePct = pct(counts.completed, total);
    const pendingPct = pct(counts.pending, total);
    const overduePct = pct(counts.overdue, total);

    const stats = [
      { key: "completed", value: counts.completed, label: "Completed", sub: `${donePct}% of total` },
      { key: "pending", value: counts.pending, label: "Pending", sub: `${pendingPct}% of total` },
      { key: "overdue", value: counts.overdue, label: "Overdue", sub: `${overduePct}% of total` },
      { key: "warning", value: counts.warnings, label: "Warnings", sub: "Logic-change flags" },
    ];

    return `
      <section class="section-card">
        <div class="section-card-head">
          <h2 class="section-card-title">Schedule snapshot</h2>
          <span class="badge">${escapeHTML(month)}</span>
        </div>
        <div class="section-card-body">
          <div class="cs-schedule-grid${hasData ? "" : " no-chart"}">
            <div class="cs-stat-list">
              ${stats
                .map(
                  (stat) => `
                <div class="cs-stat">
                  <strong>${stat.value}</strong>
                  <span><i class="dot is-${stat.key}"></i>${stat.label}</span>
                  <small>${stat.sub}</small>
                </div>`,
                )
                .join("")}
            </div>

            ${hasData ? `<div class="cs-chart">
              <canvas id="cs-status-chart" role="img" aria-label="Schedule status distribution: ${donePct}% completed, ${pendingPct}% pending, ${overduePct}% overdue"></canvas>
            </div>` : ""}

            <div class="cs-summary">
              <div>
                <div class="cs-progress-labels">
                  <span>Completion</span>
                  <strong>${donePct}%</strong>
                </div>
                <div class="progress" role="progressbar" aria-valuenow="${donePct}" aria-valuemin="0" aria-valuemax="100">
                  <div class="progress-bar" style="width: ${donePct}%"></div>
                </div>
              </div>
              <p>Counts come from the calibration schedule statistics.</p>
              <a href="${escapeHTML(window.DashboardURLs.scheduleList || "#")}">View all schedules</a>
            </div>
          </div>
        </div>
      </section>`;
  }

  // ============================================================
  // RECENT SESSIONS TABLE
  // ============================================================
  function renderRecentSessions(sessions, urls) {
    if (!sessions.length) {
      return `
        <tr>
          <td colspan="5">
            <div class="cs-empty">
              No approved sessions yet.<br>
              <a href="${escapeHTML(urls.performCalibration || urls.pendingCalibrations || "#")}">Start a calibration</a>
            </div>
          </td>
        </tr>`;
    }

    return sessions
      .map(
        (session) => `
      <tr>
        <td class="cell-title">${escapeHTML(session.device_model || "—")}</td>
        <td><span class="mono-chip">${escapeHTML(session.device_serial || "—")}</span></td>
        <td>${escapeHTML(session.procedure_name || "—")}</td>
        <td class="text-nowrap">${escapeHTML(shortDate(session.timestamp))}</td>
        <td><span class="badge ${session.overall_pass ? "bg-success" : "bg-danger"}">${session.overall_pass ? "Pass" : "Fail"}</span></td>
      </tr>`,
      )
      .join("");
  }

  // ============================================================
  // QUICK ACTIONS
  // ============================================================
  function renderQuickActions(urls) {
    const actions = [
      { href: urls.pendingCalibrations || "#", icon: "flask", label: "Perform calibration" },
      { href: urls.scheduleDashboard || "#", icon: "clipboard", label: "Schedules" },
      { href: urls.sessionsPendingApproval || "#", icon: "note", label: "Pending approval" },
      { href: urls.certificates || "#", icon: "badge", label: "Certificates" },
    ];

    return `
      <nav class="cs-qa-list">
        ${actions
          .map(
            (action) => `
          <a class="cs-qa-item" href="${escapeHTML(action.href)}">
            <span class="cs-qa-icon">${icon(action.icon)}</span>
            <span>${escapeHTML(action.label)}</span>
            <i class="fas fa-chevron-right" aria-hidden="true"></i>
          </a>`,
          )
          .join("")}
      </nav>`;
  }

  // ============================================================
  // BOTTOM ROW
  // ============================================================
  function renderBottomRow(data, urls) {
    return `
      <section class="cs-bottom-grid">
        <article class="section-card">
          <div class="section-card-head">
            <h2 class="section-card-title">Recently approved sessions</h2>
          </div>
          <div class="table-wrapper">
            <table class="table cs-sessions-table">
              <thead>
                <tr>
                  <th>Device</th>
                  <th>Serial</th>
                  <th>Procedure</th>
                  <th>Date</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>${renderRecentSessions(data.recent_sessions || [], urls)}</tbody>
            </table>
          </div>
        </article>

        <article class="section-card">
          <div class="section-card-head">
            <h2 class="section-card-title">Quick actions</h2>
          </div>
          ${renderQuickActions(urls)}
        </article>
      </section>`;
  }

  // ============================================================
  // STATUS DISTRIBUTION CHART
  // ============================================================
  function renderStatusChartInstance(counts) {
    if (!counts || typeof window.Chart === "undefined") return;

    const canvas = document.getElementById("cs-status-chart");
    if (!canvas) return;

    if (statusChart) {
      statusChart.destroy();
      statusChart = null;
    }

    statusChart = new window.Chart(canvas.getContext("2d"), {
      type: "doughnut",
      data: {
        labels: ["Completed", "Pending", "Overdue"],
        datasets: [
          {
            data: [counts.completed, counts.pending, counts.overdue],
            backgroundColor: [
              cssVar("--success-color", "#047857"),
              cssVar("--warning-color", "#b45309"),
              cssVar("--danger-color", "#dc2626"),
            ],
            borderColor: cssVar("--bg-card", "#ffffff"),
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "72%",
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              color: cssVar("--text-secondary", "#4b5563"),
              boxWidth: 8,
              boxHeight: 8,
              usePointStyle: true,
              padding: 12,
              font: { size: 12, family: getComputedStyle(document.body).fontFamily },
            },
          },
          tooltip: { enabled: true },
        },
      },
    });
  }

  // ============================================================
  // LOAD DASHBOARD
  // ============================================================
  async function loadDashboard() {
    const content = document.getElementById("dashboardContent");
    if (!content) return;

    const urls = window.DashboardURLs || {};
    if (!urls.dashboardData || !urls.scheduleStats) {
      content.innerHTML =
        '<div class="alert alert-danger">Dashboard configuration is missing.</div>';
      return;
    }

    try {
      const [dashResponse, scheduleResponse] = await Promise.all([
        fetch(urls.dashboardData, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
        }),
        fetch(urls.scheduleStats, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
        }),
      ]);

      if (!dashResponse.ok) {
        throw new Error(
          `Dashboard data request failed with status ${dashResponse.status}`,
        );
      }

      const data = await dashResponse.json();
      const scheduleData = scheduleResponse.ok
        ? await scheduleResponse.json()
        : {};
      const counts = mergeScheduleCounts(data, scheduleData);
      const month = data.current_month || "This month";
      const pendingApproval = Number(data.pending_approval_count || 0);

      content.innerHTML = `
        ${renderKpiCards(data)}
        ${renderBanner(pendingApproval, urls)}
        ${renderSchedulePanel(counts, month)}
        ${renderBottomRow(data, urls)}`;

      lastCounts = counts;
      renderStatusChartInstance(counts);
    } catch (error) {
      console.error("[calsoft_dashboard.js] load error:", error);
      content.innerHTML =
        '<div class="alert alert-danger">Error loading dashboard. Please refresh.</div>';
    }
  }

  // ============================================================
  // AUTO REFRESH
  // ============================================================
  function setupAutoRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
    }

    refreshTimer = setInterval(() => {
      loadDashboard();
    }, CONFIG.refreshInterval);
  }

  // ============================================================
  // BOOT
  // ============================================================
  function boot() {
    window.CalSoftDashboard = {
      load: loadDashboard,
      refresh: loadDashboard,
      showChart: () => renderStatusChartInstance(lastCounts),
      filterSchedules: (type) => {
        const base = "/calibration/schedules/";
        const params = {
          pending: "?status=pending",
          overdue: "?overdue=true",
          in_progress: "?status=in_progress",
          completed: "?status=completed",
        };
        window.location.href = `${base}${params[type] || ""}`;
      },
      refreshActivities: loadDashboard,
    };
    loadDashboard();
    setupAutoRefresh();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
