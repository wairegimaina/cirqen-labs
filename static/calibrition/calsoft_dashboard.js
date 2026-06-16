(function () {
  "use strict";

  const CONFIG = {
    refreshInterval: 200000,
  };

  let scheduleDonut = null;
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

  function renderHero(data) {
    const monthRate = clamp(data.month_pass_rate ?? 0, 0, 100);
    const weekRate = clamp(data.week_pass_rate ?? 0, 0, 100);
    const monthTotal = Number(data.month_total || 0);
    const weekTotal = Number(data.week_total || 0);
    const monthLabel = data.current_month || "This month";

    return `
      <section class="cs-hero">
        <div class="cs-hero-copy">
          <span class="cs-eyebrow">Pass Rate Hero</span>
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

  function renderSchedulePanel(counts, month) {
    const donutTotal = counts.pending + counts.overdue + counts.completed || 1;
    const donePct = pct(counts.completed, donutTotal);
    const pendingPct = pct(counts.pending, donutTotal);
    const overduePct = pct(counts.overdue, donutTotal);

    return `
      <section class="cs-panel cs-schedule-panel">
        <div class="cs-panel-heading">
          <div>
            <span class="cs-eyebrow">calSchedules</span>
            <h2>Schedule Snapshot</h2>
          </div>
          <span class="cs-panel-period">${escapeHTML(month)}</span>
        </div>

        <div class="cs-schedule-layout">
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

          <div class="cs-schedule-donut">
            <canvas id="scheduleDonut" width="210" height="210" role="img" aria-label="Schedule completion donut"></canvas>
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

  // Quick Actions from calsoft.js - using the same structure
  function renderQuickActions(urls, pendingApproval) {
    const actions = [
      {
        href: urls.performCalibration || "#",
        icon: "🔬",
        label: "New Calibration",
      },
      {
        href: urls.sessionsPendingApproval || "#",
        icon: "📝",
        label: "Pending Approval",
        badge: pendingApproval,
      },
      { href: urls.certificates || "#", icon: "🏅", label: "Certificates" },
      { href: urls.scheduleList || "#", icon: "📚", label: "All Sessions" },
      { href: urls.auditLog || "#", icon: "🗂️", label: "Audit Log" },
    ];

    return `
      <div class="cs-qa-list">
        ${actions
          .map(
            (action) => `
          <a class="cs-qa-item" href="${escapeHTML(action.href)}">
            <span class="cs-qa-code">${escapeHTML(action.icon)}</span>
            <span>${escapeHTML(action.label)}</span>
            ${action.badge > 0 ? `<span class="cs-qa-badge">${action.badge}</span>` : ""}
          </a>`,
          )
          .join("")}
      </div>`;
  }

  function renderBottomRow(data, urls) {
    const pendingApproval = Number(data.pending_approval_count || 0);

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
          ${renderQuickActions(urls, pendingApproval)}
        </article>
      </section>`;
  }

  function drawScheduleDonut(counts) {
    const canvas = document.getElementById("scheduleDonut");
    if (!canvas || typeof Chart === "undefined") return;

    const existing =
      typeof Chart.getChart === "function" ? Chart.getChart(canvas) : null;
    if (existing) existing.destroy();

    scheduleDonut = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: ["Completed", "Pending", "Overdue"],
        datasets: [
          {
            data: [
              counts.completed || 0,
              counts.pending || 0,
              counts.overdue || 0,
            ],
            backgroundColor: ["#10b981", "#f59e0b", "#ef4444"],
            borderWidth: 0,
            hoverOffset: 4,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (context) => ` ${context.label}: ${context.parsed}`,
            },
          },
        },
        animation: {
          animateRotate: true,
          duration: 800,
        },
      },
    });
  }

  function injectStyles() {
    if (document.getElementById("calsoft-dashboard-redesign")) return;

    const style = document.createElement("style");
    style.id = "calsoft-dashboard-redesign";
    style.textContent = `
      .cs-hero {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 200px;
        gap: 1.25rem;
        align-items: center;
        padding: 1.35rem;
        border-radius: 24px;
        background:
          radial-gradient(circle at top right, rgba(99, 102, 241, 0.22), transparent 34%),
          linear-gradient(135deg, var(--bg-card), var(--bg-tertiary));
        border: 1px solid var(--border-color);
        box-shadow: var(--shadow-sm);
        margin-bottom: 1rem;
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

      .cs-hero h1,
      .cs-schedule-panel h2,
      .cs-bottom-grid h2 {
        margin: 0;
        color: var(--text-primary);
        line-height: 1.05;
      }

      .cs-hero h1 {
        font-size: clamp(2.5rem, 7vw, 4.5rem);
        letter-spacing: -0.07em;
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
          conic-gradient(var(--success-color) calc(var(--pass-rate) * 1%), rgba(148, 163, 184, 0.18) 0),
          var(--bg-card);
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

      .cs-kpi-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.85rem;
        margin-bottom: 1rem;
      }

      .cs-kpi-card {
        min-height: 100px;
        padding: 0.85rem 1rem;
        border-radius: 16px;
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
        width: 4px;
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
        gap: 1rem;
      }

      .cs-kpi-top span {
        padding: 0.2rem 0.45rem;
        border-radius: 999px;
        background: color-mix(in srgb, var(--kpi-accent) 14%, var(--bg-card));
        color: var(--kpi-accent);
        font-size: 0.85rem;
        font-weight: 700;
      }

      .cs-kpi-top strong {
        font-size: 1.6rem;
        line-height: 1;
        color: var(--kpi-accent);
      }

      .cs-kpi-label {
        margin-top: 0.4rem;
        color: var(--text-primary);
        font-weight: 800;
        font-size: 0.9rem;
      }

      .cs-kpi-sub {
        margin-top: 0.15rem;
        color: var(--text-secondary);
        font-size: 0.72rem;
      }

      .cs-approval-banner {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        padding: 0.9rem 1rem;
        border-radius: 18px;
        margin-bottom: 1rem;
        background: color-mix(in srgb, var(--warning-color) 12%, var(--bg-card));
        border: 1px solid color-mix(in srgb, var(--warning-color) 38%, var(--border-color));
        color: var(--text-primary);
      }

      .cs-approve-btn {
        flex: 0 0 auto;
        padding: 0.55rem 0.85rem;
        border-radius: 999px;
        background: var(--warning-color);
        color: var(--text-white);
        font-size: 0.82rem;
        font-weight: 800;
        text-decoration: none;
      }

      .cs-panel,
      .cs-schedule-panel {
        border-radius: 24px;
        border: 1px solid var(--border-color);
        background: var(--bg-card);
        box-shadow: var(--shadow-sm);
        padding: 1.15rem;
      }

      .cs-panel-heading {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 1rem;
        margin-bottom: 1rem;
      }

      .cs-panel-heading.compact { margin-bottom: 0.8rem; }

      .cs-panel-heading h2 {
        font-size: 1.05rem;
      }

      .cs-panel-period {
        padding: 0.35rem 0.65rem;
        border-radius: 999px;
        background: var(--bg-tertiary);
        color: var(--text-secondary);
        font-size: 0.75rem;
        font-weight: 700;
        white-space: nowrap;
      }

      .cs-schedule-layout {
        display: grid;
        grid-template-columns: minmax(180px, 0.85fr) 200px minmax(200px, 1fr);
        gap: 1rem;
        align-items: stretch;
      }

      .cs-schedule-kpis {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.65rem;
      }

      .cs-schedule-kpis article {
        padding: 0.85rem;
        border-radius: 16px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border-color);
      }

      .cs-schedule-kpis strong {
        display: block;
        font-size: 1.6rem;
        line-height: 1;
        color: var(--text-primary);
      }

      .cs-schedule-kpis span {
        display: block;
        margin-top: 0.35rem;
        color: var(--text-secondary);
        font-weight: 800;
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
      }

      .cs-schedule-kpis small {
        display: block;
        margin-top: 0.2rem;
        color: var(--text-muted);
        font-size: 0.7rem;
      }

      .cs-schedule-donut {
        min-height: 200px;
        display: grid;
        place-items: center;
        border-radius: 20px;
        background:
          radial-gradient(circle at center, var(--bg-tertiary), transparent 62%);
        border: 1px solid var(--border-color);
      }

      .cs-schedule-donut canvas {
        width: 180px !important;
        height: 180px !important;
      }

      .cs-schedule-summary {
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        gap: 1rem;
        padding: 1rem;
        border-radius: 20px;
        background: linear-gradient(135deg, var(--bg-tertiary), var(--bg-card));
        border: 1px solid var(--border-color);
      }

      .cs-progress-labels {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        color: var(--text-secondary);
        font-size: 0.82rem;
        font-weight: 700;
      }

      .cs-progress-track {
        height: 10px;
        margin-top: 0.55rem;
        border-radius: 999px;
        overflow: hidden;
        background: var(--bg-primary);
      }

      .cs-progress-fill {
        height: 100%;
        border-radius: inherit;
        background: linear-gradient(90deg, var(--success-color), #34d399);
        transition: width 0.9s cubic-bezier(0.4, 0, 0.2, 1);
      }

      .cs-schedule-summary p {
        margin: 0;
        color: var(--text-secondary);
        font-size: 0.82rem;
        line-height: 1.5;
      }

      .cs-link {
        color: var(--primary-color);
        font-weight: 800;
        text-decoration: none;
      }

      .cs-link:hover { text-decoration: underline; }

      .cs-bottom-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 280px;
        gap: 1rem;
        margin-top: 1rem;
      }

      .cs-sessions-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.84rem;
      }

      .cs-sessions-table th {
        padding: 0.65rem 0.75rem;
        color: var(--text-muted);
        font-size: 0.7rem;
        font-weight: 900;
        letter-spacing: 0.08em;
        text-align: left;
        text-transform: uppercase;
        border-bottom: 1px solid var(--border-color);
      }

      .cs-sessions-table td {
        padding: 0.7rem 0.75rem;
        color: var(--text-secondary);
        border-bottom: 1px solid var(--border-color);
      }

      .cs-sessions-table tr:last-child td { border-bottom: 0; }

      .cs-sessions-table small { color: var(--text-muted); }

      .cs-result {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 58px;
        padding: 0.2rem 0.55rem;
        border-radius: 999px;
        font-size: 0.72rem;
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
        padding: 2rem 1rem;
        text-align: center;
        color: var(--text-secondary);
      }

      .cs-empty span {
        display: block;
        margin-bottom: 0.5rem;
      }

      .cs-empty a { color: var(--primary-color); }

      .cs-qa-list {
        display: grid;
        gap: 0.6rem;
      }

      .cs-qa-item {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.7rem 0.85rem;
        border-radius: 16px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        color: var(--text-primary);
        text-decoration: none;
        transition: transform 0.15s ease, border-color 0.15s ease;
        position: relative;
      }

      .cs-qa-item:hover {
        transform: translateY(-2px);
        border-color: var(--primary-color);
      }

      .cs-qa-code {
        display: inline-grid;
        place-items: center;
        width: 40px;
        height: 40px;
        flex: 0 0 auto;
        border-radius: 12px;
        background: var(--primary-gradient);
        color: var(--bg-card);
        font-size: 0.85rem;
        font-weight: 700;
      }

      .cs-qa-badge {
        position: absolute;
        right: 0.75rem;
        top: -0.35rem;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 20px;
        height: 20px;
        padding: 0 0.4rem;
        border-radius: 999px;
        background: var(--danger-color);
        color: white;
        font-size: 0.65rem;
        font-weight: 800;
        transform: translateY(0);
      }

      @media (max-width: 1100px) {
        .cs-hero,
        .cs-schedule-layout,
        .cs-bottom-grid {
          grid-template-columns: 1fr;
        }

        .cs-pass-orb {
          width: 150px;
          height: 150px;
        }

        .cs-schedule-donut {
          min-height: 180px;
        }

        .cs-schedule-donut canvas {
          width: 160px !important;
          height: 160px !important;
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
        ${renderHero(data)}
        ${renderKpiCards(data)}
        ${renderBanner(pendingApproval, urls)}
        ${renderSchedulePanel(counts, month)}
        ${renderBottomRow(data, urls)}`;

      drawScheduleDonut(counts);
    } catch (error) {
      console.error("[calsoft_dashboard.js] load error:", error);
      content.innerHTML =
        '<div class="alert alert-danger">Error loading dashboard. Please refresh.</div>';
    }
  }

  function setupAutoRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
    }

    refreshTimer = setInterval(() => {
      loadDashboard();
    }, CONFIG.refreshInterval);
  }

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
