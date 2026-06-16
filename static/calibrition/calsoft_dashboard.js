(function () {
  "use strict";

  const CONFIG = {
    refreshInterval: 200000,
  };

  let refreshTimer = null;

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

  function clamp(value, min, max) {
    return Math.min(Math.max(Number(value) || 0, min), max);
  }

  function formatPercent(value) {
    const number = Number(value) || 0;
    return `${Number.isInteger(number) ? number.toFixed(0) : number.toFixed(1)}%`;
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
  // KPI CARDS (small, below hero)
  // ============================================================
  function renderKpiCards(data) {
    const totalSessions = data.total_sessions || 0;
    const monthTotal = data.month_total || 0;
    const pendingApproval = data.pending_approval_count || 0;
    const awaitingCerts = data.awaiting_certificates || 0;

    const cards = [
      {
        variant: "pending",
        icon: "📋",
        value: pendingApproval,
        label: "Pending Approval",
        sub: "Sessions awaiting review",
      },
      {
        variant: "approved",
        icon: "✅",
        value: monthTotal,
        label: "Approved This Month",
        sub: data.current_month || "This month",
      },
      {
        variant: "total",
        icon: "📊",
        value: totalSessions,
        label: "Total Approved",
        sub: "All time sessions",
      },
      {
        variant: "certs",
        icon: "🏅",
        value: awaitingCerts,
        label: "Awaiting Certificates",
        sub: "Ready for certification",
      },
    ];

    return `
      <section class="cs-kpi-grid">
        ${cards
          .map(
            (card) => `
          <article class="cs-kpi-card cs-kpi-${card.variant}">
            <div class="cs-kpi-top">
              <span>${escapeHTML(card.icon)}</span>
              <strong>${escapeHTML(card.value)}</strong>
            </div>
            <div class="cs-kpi-label">${escapeHTML(card.label)}</div>
            <div class="cs-kpi-sub">${escapeHTML(card.sub)}</div>
          </article>`,
          )
          .join("")}
      </section>`;
  }

  // ============================================================
  // PASS RATE HERO (big card, now below KPI)
  // ============================================================
  function renderHero(data) {
    const monthRate = clamp(data.month_pass_rate ?? 0, 0, 100);
    const weekRate = clamp(data.week_pass_rate ?? 0, 0, 100);
    const monthTotal = Number(data.month_total || 0);
    const weekTotal = Number(data.week_total || 0);
    const monthLabel = data.current_month || "This month";

    return `
      <section class="cs-hero">
        <div class="cs-hero-copy">
          <span class="cs-eyebrow">Pass Rate</span>
          <h1>${formatPercent(monthRate)}</h1>
          <p>${escapeHTML(monthLabel)} pass rate from ${monthTotal} approved ${pluralize(monthTotal, "session")}</p>
          <div class="cs-hero-meta">
            <span>Week: <strong>${formatPercent(weekRate)}</strong> (${weekTotal} sessions)</span>
          </div>
        </div>
        <div class="cs-pass-orb" style="--pass-rate: ${monthRate}">
          <span>${formatPercent(monthRate)}</span>
          <small>monthly pass rate</small>
        </div>
      </section>`;
  }

  // ============================================================
  // APPROVAL BANNER
  // ============================================================
  function renderBanner(pendingApproval, urls) {
    if (!pendingApproval) return "";
    return `
      <section class="cs-approval-banner">
        <div>
          <strong>${pendingApproval}</strong>
          ${pluralize(pendingApproval, "session")} pending your review
        </div>
        <a class="cs-approve-btn" href="${escapeHTML(urls.sessionsPendingApproval)}">Review &amp; Approve</a>
      </section>`;
  }

  // ============================================================
  // SCHEDULE SNAPSHOT (without donut)
  // ============================================================
  function renderSchedulePanel(counts, month) {
    const total = counts.pending + counts.overdue + counts.completed || 1;
    const donePct = pct(counts.completed, total);
    const pendingPct = pct(counts.pending, total);
    const overduePct = pct(counts.overdue, total);

    return `
      <section class="cs-panel cs-schedule-panel">
        <div class="cs-panel-heading">
          <div>
            <span class="cs-eyebrow">calSchedules</span>
            <h2>Schedule Snapshot</h2>
          </div>
          <span class="cs-panel-period">${escapeHTML(month)}</span>
        </div>

        <div class="cs-schedule-grid">
          <div class="cs-schedule-kpis">
            <article>
              <strong>${counts.completed}</strong>
              <span>Completed</span>
              <small>${donePct}% of tracked total</small>
            </article>
            <article>
              <strong>${counts.pending}</strong>
              <span>Pending</span>
              <small>${pendingPct}% of tracked total</small>
            </article>
            <article>
              <strong>${counts.overdue}</strong>
              <span>Overdue</span>
              <small>${overduePct}% of tracked total</small>
            </article>
            <article>
              <strong>${counts.warnings}</strong>
              <span>Warnings</span>
              <small>Logic-change flags</small>
            </article>
          </div>

          <div class="cs-schedule-summary">
            <div class="cs-progress-block">
              <div class="cs-progress-labels">
                <span>Completion progress</span>
                <strong>${donePct}%</strong>
              </div>
              <div class="cs-progress-track">
                <div class="cs-progress-fill" style="width: ${donePct}%"></div>
              </div>
            </div>
            <p>Completed, pending, and overdue counts are read from the calSchedules statistics endpoint.</p>
            <a class="cs-link" href="${escapeHTML(window.DashboardURLs.scheduleList || "#")}">View all schedules</a>
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
              <span>No approved sessions yet.</span>
              <a href="${escapeHTML(urls.performCalibration)}">Start a calibration</a>
            </div>
          </td>
        </tr>`;
    }

    return sessions
      .map(
        (session) => `
      <tr>
        <td>${escapeHTML(session.device_model || "—")}</td>
        <td><small>${escapeHTML(session.device_serial || "—")}</small></td>
        <td>${escapeHTML(session.procedure_name || "—")}</td>
        <td>${escapeHTML(shortDate(session.timestamp))}</td>
        <td><span class="cs-result ${session.overall_pass ? "pass" : "fail"}">${session.overall_pass ? "Pass" : "Fail"}</span></td>
      </tr>`,
      )
      .join("");
  }

  // ============================================================
  // QUICK ACTIONS (with all requested URLs)
  // ============================================================
  function renderQuickActions(urls) {
    const actions = [
      {
        href: urls.pendingCalibrations || "#",
        icon: "🔬",
        label: "Perform Calibration",
      },
      { href: urls.scheduleDashboard || "#", icon: "📋", label: "Schedules" },
      {
        href: urls.sessionsPendingApproval || "#",
        icon: "📝",
        label: "Pending Approval",
      },
      { href: urls.certificates || "#", icon: "🏅", label: "Certificates" },
    ];

    return `
      <div class="cs-qa-list">
        ${actions
          .map(
            (action) => `
          <a class="cs-qa-item" href="${escapeHTML(action.href)}">
            <span class="cs-qa-code">${escapeHTML(action.icon)}</span>
            <span>${escapeHTML(action.label)}</span>
          </a>`,
          )
          .join("")}
      </div>`;
  }

  // ============================================================
  // BOTTOM ROW
  // ============================================================
  function renderBottomRow(data, urls) {
    return `
      <section class="cs-bottom-grid">
        <article class="cs-panel">
          <div class="cs-panel-heading compact">
            <div>
              <span class="cs-eyebrow">Recent</span>
              <h2>Approved Sessions</h2>
            </div>
          </div>
          <table class="cs-sessions-table">
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
        </article>

        <article class="cs-panel">
          <div class="cs-panel-heading compact">
            <div>
              <span class="cs-eyebrow">Quick</span>
              <h2>Actions</h2>
            </div>
          </div>
          ${renderQuickActions(urls)}
        </article>
      </section>`;
  }

  // ============================================================
  // STYLES (no donut styles, hero below KPI)
  // ============================================================
  function injectStyles() {
    if (document.getElementById("calsoft-dashboard-redesign")) return;

    const style = document.createElement("style");
    style.id = "calsoft-dashboard-redesign";
    style.textContent = `
      /* Hero - now below KPI */
      .cs-hero {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 200px;
        gap: 1.25rem;
        align-items: center;
        padding: 1.35rem;
        border-radius: 24px;
        background: var(--bg-card);
        border: 1px solid var(--border-color);
        box-shadow: var(--shadow-sm);
        margin-bottom: 0.75rem;
      }

      .cs-hero-copy { min-width: 0; }

      .cs-eyebrow {
        display: inline-block;
        margin-bottom: 0.4rem;
        color: var(--primary-color);
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.12em;
        text-transform: uppercase;
      }

      .cs-hero h1 {
        font-size: clamp(2.5rem, 7vw, 4.5rem);
        letter-spacing: -0.07em;
        margin: 0;
        color: var(--text-primary);
        line-height: 1.05;
      }

      .cs-hero p {
        margin: 0.55rem 0 0;
        color: var(--text-secondary);
        font-size: 1rem;
      }

      .cs-hero-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 0.65rem;
        margin-top: 1rem;
      }

      .cs-hero-meta span {
        padding: 0.45rem 0.7rem;
        border-radius: 999px;
        background: var(--bg-tertiary);
        color: var(--text-secondary);
        font-size: 0.8rem;
      }

      .cs-pass-orb {
        width: 170px;
        height: 170px;
        margin: 0 auto;
        border-radius: 50%;
        display: grid;
        place-items: center;
        background:
          conic-gradient(var(--success-color) calc(var(--pass-rate) * 1%), var(--bg-tertiary) 0);
        position: relative;
        box-shadow: inset 0 0 0 1px var(--border-color);
      }

      .cs-pass-orb::before {
        content: "";
        position: absolute;
        inset: 16px;
        border-radius: 50%;
        background: var(--bg-card);
        box-shadow: inset 0 0 0 1px var(--border-color);
      }

      .cs-pass-orb span,
      .cs-pass-orb small {
        position: relative;
        z-index: 1;
        display: block;
        text-align: center;
      }

      .cs-pass-orb span {
        font-size: 2rem;
        font-weight: 900;
        color: var(--text-primary);
        letter-spacing: -0.05em;
      }

      .cs-pass-orb small {
        margin-top: 0.25rem;
        color: var(--text-secondary);
        font-size: 0.68rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }

      /* KPI Cards - small */
      .cs-kpi-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.75rem;
        margin-bottom: 0.75rem;
      }

      .cs-kpi-card {
        min-height: 85px;
        padding: 0.7rem 0.85rem;
        border-radius: 14px;
        border: 1px solid var(--border-color);
        background: var(--bg-card);
        box-shadow: var(--shadow-sm);
        position: relative;
        overflow: hidden;
      }

      .cs-kpi-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 3px;
        background: var(--kpi-accent);
      }

      .cs-kpi-pending { --kpi-accent: var(--warning-color); }
      .cs-kpi-approved { --kpi-accent: var(--success-color); }
      .cs-kpi-total { --kpi-accent: var(--primary-color); }
      .cs-kpi-certs { --kpi-accent: #8b5cf6; }

      .cs-kpi-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
      }

      .cs-kpi-top span {
        padding: 0.15rem 0.4rem;
        border-radius: 999px;
        background: color-mix(in srgb, var(--kpi-accent) 14%, var(--bg-card));
        color: var(--kpi-accent);
        font-size: 0.75rem;
        font-weight: 700;
      }

      .cs-kpi-top strong {
        font-size: 1.4rem;
        line-height: 1;
        color: var(--kpi-accent);
      }

      .cs-kpi-label {
        margin-top: 0.3rem;
        color: var(--text-primary);
        font-weight: 700;
        font-size: 0.82rem;
      }

      .cs-kpi-sub {
        margin-top: 0.1rem;
        color: var(--text-secondary);
        font-size: 0.68rem;
      }

      /* Approval Banner */
      .cs-approval-banner {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        padding: 0.75rem 1rem;
        border-radius: 16px;
        margin-bottom: 0.75rem;
        background: color-mix(in srgb, var(--warning-color) 12%, var(--bg-card));
        border: 1px solid color-mix(in srgb, var(--warning-color) 38%, var(--border-color));
        color: var(--text-primary);
      }

      .cs-approve-btn {
        flex: 0 0 auto;
        padding: 0.45rem 0.8rem;
        border-radius: 999px;
        background: var(--warning-color);
        color: var(--text-white);
        font-size: 0.78rem;
        font-weight: 800;
        text-decoration: none;
      }

      /* Panels */
      .cs-panel,
      .cs-schedule-panel {
        border-radius: 20px;
        border: 1px solid var(--border-color);
        background: var(--bg-card);
        box-shadow: var(--shadow-sm);
        padding: 1rem;
      }

      .cs-panel-heading {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 1rem;
        margin-bottom: 0.85rem;
      }

      .cs-panel-heading.compact { margin-bottom: 0.65rem; }

      .cs-panel-heading h2 {
        font-size: 0.95rem;
        margin: 0;
        color: var(--text-primary);
        line-height: 1.05;
      }

      .cs-panel-period {
        padding: 0.3rem 0.6rem;
        border-radius: 999px;
        background: var(--bg-tertiary);
        color: var(--text-secondary);
        font-size: 0.7rem;
        font-weight: 700;
        white-space: nowrap;
      }

      /* Schedule grid without donut */
      .cs-schedule-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.85rem;
        align-items: stretch;
      }

      .cs-schedule-kpis {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.5rem;
      }

      .cs-schedule-kpis article {
        padding: 0.7rem;
        border-radius: 14px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border-color);
      }

      .cs-schedule-kpis strong {
        display: block;
        font-size: 1.4rem;
        line-height: 1;
        color: var(--text-primary);
      }

      .cs-schedule-kpis span {
        display: block;
        margin-top: 0.3rem;
        color: var(--text-secondary);
        font-weight: 700;
        font-size: 0.68rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
      }

      .cs-schedule-kpis small {
        display: block;
        margin-top: 0.15rem;
        color: var(--text-muted);
        font-size: 0.65rem;
      }

      .cs-schedule-summary {
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        gap: 0.85rem;
        padding: 0.85rem;
        border-radius: 18px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border-color);
      }

      .cs-progress-labels {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        color: var(--text-secondary);
        font-size: 0.78rem;
        font-weight: 700;
      }

      .cs-progress-track {
        height: 8px;
        margin-top: 0.4rem;
        border-radius: 999px;
        overflow: hidden;
        background: var(--bg-primary);
      }

      .cs-progress-fill {
        height: 100%;
        border-radius: inherit;
        background: var(--success-color);
        transition: width 0.9s cubic-bezier(0.4, 0, 0.2, 1);
      }

      .cs-schedule-summary p {
        margin: 0;
        color: var(--text-secondary);
        font-size: 0.78rem;
        line-height: 1.5;
      }

      .cs-link {
        color: var(--primary-color);
        font-weight: 800;
        text-decoration: none;
        font-size: 0.82rem;
      }

      .cs-link:hover { text-decoration: underline; }

      /* Bottom Grid */
      .cs-bottom-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 240px;
        gap: 0.85rem;
        margin-top: 0.85rem;
      }

      .cs-sessions-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.8rem;
      }

      .cs-sessions-table th {
        padding: 0.5rem 0.65rem;
        color: var(--text-muted);
        font-size: 0.65rem;
        font-weight: 900;
        letter-spacing: 0.08em;
        text-align: left;
        text-transform: uppercase;
        border-bottom: 1px solid var(--border-color);
      }

      .cs-sessions-table td {
        padding: 0.55rem 0.65rem;
        color: var(--text-secondary);
        border-bottom: 1px solid var(--border-color);
      }

      .cs-sessions-table tr:last-child td { border-bottom: 0; }

      .cs-sessions-table small { color: var(--text-muted); }

      .cs-result {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 50px;
        padding: 0.15rem 0.45rem;
        border-radius: 999px;
        font-size: 0.68rem;
        font-weight: 900;
      }

      .cs-result.pass {
        background: color-mix(in srgb, var(--success-color) 16%, var(--bg-card));
        color: var(--success-color);
      }

      .cs-result.fail {
        background: color-mix(in srgb, var(--danger-color) 16%, var(--bg-card));
        color: var(--danger-color);
      }

      .cs-empty {
        padding: 1.5rem 0.5rem;
        text-align: center;
        color: var(--text-secondary);
      }

      .cs-empty span {
        display: block;
        margin-bottom: 0.4rem;
      }

      .cs-empty a { color: var(--primary-color); }

      /* Quick Actions */
      .cs-qa-list {
        display: grid;
        gap: 0.5rem;
      }

      .cs-qa-item {
        display: flex;
        align-items: center;
        gap: 0.65rem;
        padding: 0.6rem 0.75rem;
        border-radius: 14px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        color: var(--text-primary);
        text-decoration: none;
        transition: transform 0.15s ease, border-color 0.15s ease;
      }

      .cs-qa-item:hover {
        transform: translateY(-2px);
        border-color: var(--primary-color);
      }

      .cs-qa-code {
        display: inline-grid;
        place-items: center;
        width: 36px;
        height: 36px;
        flex: 0 0 auto;
        border-radius: 10px;
        background: var(--primary-gradient);
        color: var(--bg-card);
        font-size: 0.75rem;
        font-weight: 700;
      }

      /* Responsive */
      @media (max-width: 1100px) {
        .cs-hero,
        .cs-schedule-grid,
        .cs-bottom-grid {
          grid-template-columns: 1fr;
        }

        .cs-pass-orb {
          width: 150px;
          height: 150px;
        }
      }

      @media (max-width: 760px) {
        .cs-kpi-grid,
        .cs-schedule-kpis {
          grid-template-columns: 1fr;
        }

        .cs-hero {
          padding: 1rem;
        }

        .cs-approval-banner {
          align-items: flex-start;
          flex-direction: column;
        }
      }
    `;
    document.head.appendChild(style);
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
        ${renderHero(data)}
        ${renderBanner(pendingApproval, urls)}
        ${renderSchedulePanel(counts, month)}
        ${renderBottomRow(data, urls)}`;
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
    injectStyles();
    window.CalSoftDashboard = {
      load: loadDashboard,
      refresh: loadDashboard,
      showChart: () => {},
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
