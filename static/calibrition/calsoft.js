// ============================================================
// CALSOFT — DASHBOARD (Unified)
// Combines both dashboard implementations with DRY principles
// ============================================================

(function () {
  "use strict";

  // -------- CONFIGURATION --------
  const CONFIG = {
    REFRESH_INTERVAL: 200000,
    ANIMATION_DURATION: 1000,
    ANIMATION_STEPS: 30,
    CHART_PADDING: 60,
    MAX_CHART_VALUE: 100,
  };

  // -------- STATE --------
  let state = {
    chartInstance: null,
    currentPeriod: "week",
    dashboardData: {},
    csrfToken: null,
    resizeTimeout: null,
  };

  // -------- UTILITY FUNCTIONS --------
  const Utils = {
    /** Read CSS variable value (theme-aware) */
    cssVar: (name, fallback) => {
      const val = getComputedStyle(document.documentElement)
        .getPropertyValue(name)
        .trim();
      return val || fallback;
    },

    /** Format number with commas */
    formatNumber: (num) => num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ","),

    /** Format date */
    formatDate: (date) => {
      if (typeof date === "string") date = new Date(date);
      return date.toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    },

    /** Calculate time ago */
    timeAgo: (date) => {
      if (typeof date === "string") date = new Date(date);
      const diff = Math.floor((Date.now() - date) / 1000);
      const intervals = [
        { label: "day", seconds: 86400 },
        { label: "hour", seconds: 3600 },
        { label: "minute", seconds: 60 },
      ];
      for (const interval of intervals) {
        const value = Math.floor(diff / interval.seconds);
        if (value > 0)
          return `${value} ${interval.label}${value > 1 ? "s" : ""} ago`;
      }
      return "Just now";
    },

    /** Animate number changes */
    animateNumber: (element, from, to, suffix = "") => {
      const duration = CONFIG.ANIMATION_DURATION;
      const steps = CONFIG.ANIMATION_STEPS;
      const increment = (to - from) / steps;
      const stepDuration = duration / steps;

      let current = from;
      let step = 0;

      const timer = setInterval(() => {
        current += increment;
        step++;
        if (step >= steps) {
          current = to;
          clearInterval(timer);
        }
        element.textContent = Math.round(current * 10) / 10 + suffix;
      }, stepDuration);
    },

    /** Draw rounded rectangle */
    drawRoundedRect: (ctx, x, y, width, height, radius) => {
      ctx.beginPath();
      ctx.moveTo(x + radius, y);
      ctx.lineTo(x + width - radius, y);
      ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
      ctx.lineTo(x + width, y + height - radius);
      ctx.quadraticCurveTo(
        x + width,
        y + height,
        x + width - radius,
        y + height,
      );
      ctx.lineTo(x + radius, y + height);
      ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
      ctx.lineTo(x, y + radius);
      ctx.quadraticCurveTo(x, y, x + radius, y);
      ctx.closePath();
    },
  };

  // -------- RENDER HELPERS (DRY) --------

  /** Generic card renderer - reduces repetitive card creation */
  const CardRenderer = {
    /** Render KPI cards from data array */
    kpiCards: (cards) => {
      return cards
        .map(
          (c) => `
                <div class="kpi-card ${c.variant || ""}">
                    <div class="kpi-icon">${c.icon}</div>
                    <div class="kpi-num">${c.value}</div>
                    <div class="kpi-label">${c.label}</div>
                    ${c.sub ? `<div class="kpi-sub">${c.sub}</div>` : ""}
                </div>
            `,
        )
        .join("");
    },

    /** Render gauge rows from data array */
    gaugeRows: (rows) => {
      return rows
        .map(
          (r) => `
                <div class="gauge-row">
                    <div class="gauge-label-row">
                        <span class="gl-name">${r.name}</span>
                        <span class="gl-pct">${r.pct}%</span>
                    </div>
                    <div class="gauge-track">
                        <div class="gauge-fill ${r.fill || ""}" style="width:${r.pct}%"></div>
                    </div>
                    ${r.sub ? `<div class="gauge-sub">${r.sub}</div>` : ""}
                </div>
            `,
        )
        .join("");
    },

    /** Render quick action links from data array */
    quickActions: (actions) => {
      return actions
        .map(
          (a) => `
                <a class="qa-item" href="${a.href}">
                    <span class="qi-icon">${a.icon}</span>
                    <span class="qi-label">${a.label}</span>
                    ${a.badge > 0 ? `<span class="qi-badge">${a.badge}</span>` : ""}
                </a>
            `,
        )
        .join("");
    },

    /** Render session rows from data array */
    sessionRows: (sessions, urls) => {
      if (sessions.length === 0) {
        return `
                    <tr><td colspan="5">
                        <div class="empty-sessions">
                            <span>🔬</span>
                            No approved sessions yet. <a href="${urls.performCalibration}">Start a calibration</a>
                        </div>
                    </td></tr>`;
      }

      return sessions
        .map((s) => {
          const passEl = s.overall_pass
            ? '<span class="pass-pill pass">Pass</span>'
            : '<span class="pass-pill fail">Fail</span>';
          const date = new Date(s.timestamp).toLocaleDateString("en-GB", {
            day: "2-digit",
            month: "short",
          });

          return `
                    <tr>
                        <td>${s.device_model || "—"}</td>
                        <td><small class="text-muted">${s.device_serial || "—"}</small></td>
                        <td>${s.procedure_name || "—"}</td>
                        <td>${date}</td>
                        <td>${passEl}</td>
                    </tr>
                `;
        })
        .join("");
    },

    /** Render schedule items from data array */
    scheduleItems: (sessions) => {
      if (!sessions || sessions.length === 0) {
        return `
                    <div class="empty-state">
                        <div class="empty-icon">📋</div>
                        <h4>No recent calibration sessions</h4>
                        <p>No calibration sessions have been performed this week</p>
                        <a href="/calibration/perform_calibration/" class="btn btn-primary">Start Calibration</a>
                    </div>
                `;
      }

      return sessions
        .slice(0, 5)
        .map((session) => {
          const statusClass = session.overall_pass ? "completed" : "failed";
          const statusText = session.overall_pass ? "PASSED" : "FAILED";
          const progressWidth = session.overall_pass ? "100" : "50";

          return `
                    <div class="schedule-item ${statusClass}">
                        <div class="schedule-info">
                            <div class="schedule-equipment">
                                ${
                                  session.equipment
                                    ? `${session.equipment.description} - ${session.device_serial || "N/A"}`
                                    : `${session.device_model || "Unknown Model"} - ${session.device_serial || "N/A"}`
                                }
                            </div>
                            <div class="schedule-details">
                                Procedure: ${session.procedure ? session.procedure.name : "No procedure assigned"}
                                <br>
                                Performed by: ${session.performed_by_name || "Unknown"}
                                <br>
                                Status: <strong>${statusText}</strong>
                            </div>
                            <div class="progress-bar">
                                <div class="progress-fill" style="width: ${progressWidth}%;"></div>
                            </div>
                        </div>
                        <div class="schedule-actions">
                            <div class="schedule-date">
                                ${Utils.formatDate(session.timestamp)}
                                <div class="time-ago">${Utils.timeAgo(session.timestamp)}</div>
                            </div>
                            <div class="action-buttons">
                                <a href="/calibration/session/${session.id}/" class="btn btn-sm">View Details</a>
                                ${
                                  session.overall_pass && session.equipment
                                    ? `<form action="/calibration/complete-from-session/${session.id}/" method="post" style="display: inline;">
                                        <input type="hidden" name="csrfmiddlewaretoken" value="${state.csrfToken}">
                                        <button type="submit" class="btn btn-success btn-sm">Complete Schedule</button>
                                    </form>`
                                    : ""
                                }
                            </div>
                        </div>
                    </div>
                `;
        })
        .join("");
    },
  };

  // -------- CHART RENDERER --------
  const ChartRenderer = {
    /** Draw performance chart */
    drawPerformanceChart: (canvas, period = "week") => {
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const width = canvas.width;
      const height = canvas.height;

      // Get data based on period
      const dataMap = {
        week: {
          data: [state.dashboardData.week_pass_rate || 0],
          labels: ["This Week"],
        },
        month: {
          data: [state.dashboardData.month_pass_rate || 0],
          labels: ["This Month"],
        },
        quarter: {
          data: [
            state.dashboardData.week_pass_rate || 0,
            state.dashboardData.month_pass_rate || 0,
            state.dashboardData.quarter_pass_rate || 85,
          ],
          labels: ["Week", "Month", "Quarter"],
        },
      };

      const chartData = dataMap[period] || dataMap.week;
      ChartRenderer._drawEnhancedChart(
        ctx,
        width,
        height,
        chartData.data,
        chartData.labels,
      );
    },

    /** Draw donut chart */
    drawDonutChart: (canvasId, data) => {
      const canvas = document.getElementById(canvasId);
      if (!canvas) return;

      const totalEquip = data.total_equipment || 0;
      const needCalib = data.equipment_needing_calibration || 0;
      const calibOk = totalEquip - needCalib;

      if (state.chartInstance) state.chartInstance.destroy();

      state.chartInstance = new Chart(canvas, {
        type: "doughnut",
        data: {
          labels: ["Up to date", "Needs calibration"],
          datasets: [
            {
              data: [calibOk || 0, needCalib || 0],
              backgroundColor: [
                Utils.cssVar("--success-color", "#10b981"),
                Utils.cssVar("--warning-color", "#f59e0b"),
              ],
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
    },

    /** Internal: draw enhanced chart with modern styling */
    _drawEnhancedChart: (ctx, width, height, data, labels) => {
      const maxValue = CONFIG.MAX_CHART_VALUE;
      const padding = CONFIG.CHART_PADDING;
      const chartWidth = width - 2 * padding;
      const chartHeight = height - 2 * padding;

      // Clear and set background
      ctx.clearRect(0, 0, width, height);
      const bgGradient = ctx.createLinearGradient(0, 0, 0, height);
      bgGradient.addColorStop(0, "#fafbfc");
      bgGradient.addColorStop(1, "#f8f9fa");
      ctx.fillStyle = bgGradient;
      ctx.fillRect(0, 0, width, height);

      // Enable high-quality rendering
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";

      // Draw grid
      ChartRenderer._drawGrid(ctx, padding, chartWidth, chartHeight, height);

      // Draw axes
      ChartRenderer._drawAxes(ctx, padding, chartWidth, chartHeight, height);

      // Draw data if available
      if (data.length > 0) {
        ChartRenderer._drawDataPoints(
          ctx,
          data,
          labels,
          padding,
          chartWidth,
          chartHeight,
          height,
          maxValue,
        );
      }

      // Draw title
      ChartRenderer._drawTitle(ctx, width, padding, labels[0] || "Performance");
    },

    /** Draw grid with subtle styling */
    _drawGrid: (ctx, padding, chartWidth, chartHeight, height) => {
      ctx.strokeStyle = "rgba(0, 0, 0, 0.05)";
      ctx.lineWidth = 1;

      for (let i = 0; i <= 5; i++) {
        const y = padding + (i * chartHeight) / 5;
        ctx.beginPath();
        ctx.moveTo(padding, y);
        ctx.lineTo(padding + chartWidth, y);
        ctx.stroke();

        ctx.fillStyle = "#6b7280";
        ctx.font =
          '12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        ctx.fillText(100 - i * 20 + "%", padding - 15, y);
      }
    },

    /** Draw axes */
    _drawAxes: (ctx, padding, chartWidth, chartHeight, height) => {
      ctx.strokeStyle = "#d1d5db";
      ctx.lineWidth = 2;
      ctx.lineCap = "round";

      ctx.beginPath();
      ctx.moveTo(padding, padding);
      ctx.lineTo(padding, height - padding);
      ctx.moveTo(padding, height - padding);
      ctx.lineTo(padding + chartWidth, height - padding);
      ctx.stroke();
      ctx.lineCap = "butt";
    },

    /** Draw data points with enhanced styling */
    _drawDataPoints: (
      ctx,
      data,
      labels,
      padding,
      chartWidth,
      chartHeight,
      height,
      maxValue,
    ) => {
      const points = data.map((value, index) => ({
        x:
          data.length === 1
            ? padding + chartWidth / 2
            : padding + (index * chartWidth) / (data.length - 1),
        y: height - padding - (value / maxValue) * chartHeight,
        value,
      }));

      // Draw area fill for multiple points
      if (data.length > 1) {
        ChartRenderer._drawAreaFill(ctx, points, height, padding);
        ChartRenderer._drawConnectionLine(ctx, points);
      }

      // Draw individual points
      points.forEach((point, index) => {
        ChartRenderer._drawPoint(ctx, point, index, labels[index]);
      });

      // Draw value indicators
      ChartRenderer._drawValueIndicators(ctx, points, labels);
    },

    /** Draw area fill */
    _drawAreaFill: (ctx, points, height, padding) => {
      if (points.length < 2) return;

      const gradient = ctx.createLinearGradient(
        0,
        points[0].y,
        0,
        height - padding,
      );
      gradient.addColorStop(0, "rgba(59, 130, 246, 0.15)");
      gradient.addColorStop(0.5, "rgba(59, 130, 246, 0.08)");
      gradient.addColorStop(1, "rgba(59, 130, 246, 0.02)");

      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.moveTo(points[0].x, height - padding);
      ctx.lineTo(points[0].x, points[0].y);

      if (points.length === 2) {
        ctx.lineTo(points[1].x, points[1].y);
      } else {
        for (let i = 1; i < points.length - 1; i++) {
          const cpX = (points[i].x + points[i + 1].x) / 2;
          const cpY = (points[i].y + points[i + 1].y) / 2;
          ctx.quadraticCurveTo(points[i].x, points[i].y, cpX, cpY);
        }
        ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
      }

      ctx.lineTo(points[points.length - 1].x, height - padding);
      ctx.closePath();
      ctx.fill();
    },

    /** Draw connection line */
    _drawConnectionLine: (ctx, points) => {
      if (points.length < 2) return;

      ctx.strokeStyle = "#3b82f6";
      ctx.lineWidth = 3;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.shadowColor = "rgba(59, 130, 246, 0.3)";
      ctx.shadowBlur = 4;
      ctx.shadowOffsetY = 2;

      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);

      if (points.length === 2) {
        ctx.lineTo(points[1].x, points[1].y);
      } else {
        for (let i = 1; i < points.length - 1; i++) {
          const cpX = (points[i].x + points[i + 1].x) / 2;
          const cpY = (points[i].y + points[i + 1].y) / 2;
          ctx.quadraticCurveTo(points[i].x, points[i].y, cpX, cpY);
        }
        ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
      }

      ctx.stroke();
      ctx.shadowColor = "transparent";
      ctx.shadowBlur = 0;
      ctx.shadowOffsetY = 0;
    },

    /** Draw individual data point */
    _drawPoint: (ctx, point, index, label) => {
      // Outer ring
      ctx.fillStyle = "rgba(59, 130, 246, 0.2)";
      ctx.beginPath();
      ctx.arc(point.x, point.y, 10, 0, 2 * Math.PI);
      ctx.fill();

      // Main point
      ctx.fillStyle = "#3b82f6";
      ctx.beginPath();
      ctx.arc(point.x, point.y, 6, 0, 2 * Math.PI);
      ctx.fill();

      // Inner highlight
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(point.x - 1, point.y - 1, 2, 0, 2 * Math.PI);
      ctx.fill();

      // Hover effect for first point
      if (index === 0 && point.value > 0) {
        ctx.strokeStyle = "#3b82f6";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(point.x, point.y, 12, 0, 2 * Math.PI);
        ctx.stroke();
      }
    },

    /** Draw value indicators */
    _drawValueIndicators: (ctx, points, labels) => {
      points.forEach((point, index) => {
        const valueText = point.value.toFixed(1) + "%";
        const labelText = labels[index];

        ctx.font =
          'bold 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        const valueMetrics = ctx.measureText(valueText);
        const valueWidth = valueMetrics.width;

        const bgPadding = 8;
        const bgHeight = 24;
        const bgY = point.y - 35;

        ctx.fillStyle = "rgba(255, 255, 255, 0.95)";
        ctx.shadowColor = "rgba(0, 0, 0, 0.1)";
        ctx.shadowBlur = 8;
        ctx.shadowOffsetY = 2;

        Utils.drawRoundedRect(
          ctx,
          point.x - valueWidth / 2 - bgPadding,
          bgY - bgHeight / 2,
          valueWidth + bgPadding * 2,
          bgHeight,
          6,
        );
        ctx.fill();

        ctx.shadowColor = "transparent";
        ctx.shadowBlur = 0;
        ctx.shadowOffsetY = 0;

        ctx.fillStyle = "#1f2937";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(valueText, point.x, bgY);

        ctx.font =
          '12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillStyle = "#6b7280";
        ctx.fillText(labelText, point.x, point.y + 35);
      });
    },

    /** Draw chart title */
    _drawTitle: (ctx, width, padding, title) => {
      ctx.fillStyle = "#374151";
      ctx.font =
        'bold 16px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(`${title} Pass Rate`, width / 2, padding / 2);
    },

    /** Draw error chart */
    drawErrorChart: (ctx, width, height) => {
      ctx.clearRect(0, 0, width, height);
      const bgGradient = ctx.createLinearGradient(0, 0, 0, height);
      bgGradient.addColorStop(0, "#fef2f2");
      bgGradient.addColorStop(1, "#fef7f7");
      ctx.fillStyle = bgGradient;
      ctx.fillRect(0, 0, width, height);

      ctx.fillStyle = "#ef4444";
      ctx.font =
        '24px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      ctx.textAlign = "center";
      ctx.fillText("📊", width / 2, height / 2 - 20);

      ctx.fillStyle = "#7f1d1d";
      ctx.font =
        '14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      ctx.fillText("Chart data unavailable", width / 2, height / 2 + 10);

      ctx.fillStyle = "#a3a3a3";
      ctx.font =
        '12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      ctx.fillText("Please refresh the dashboard", width / 2, height / 2 + 30);
    },
  };

  // -------- DASHBOARD RENDERER --------
  const DashboardRenderer = {
    /** Main render function */
    render: (data, urls) => {
      const counts = data.schedule_counts || {};
      const pendingApproval = data.pending_approval_count || 0;
      const totalEquip = data.total_equipment || 0;
      const needCalib = data.equipment_needing_calibration || 0;
      const calibOk = totalEquip - needCalib;
      const equipPct =
        totalEquip > 0 ? Math.round((calibOk / totalEquip) * 100) : 0;

      return {
        content: `
                    ${DashboardRenderer._renderBanner(pendingApproval, urls)}
                    ${DashboardRenderer._renderGreeting(data)}
                    ${DashboardRenderer._renderKpiGrid(counts, data)}
                    ${DashboardRenderer._renderMidRow(data, equipPct, calibOk, totalEquip)}
                    ${DashboardRenderer._renderBottomRow(data, urls, pendingApproval)}
                `,
        equipPct,
        calibOk,
        needCalib,
      };
    },

    /** Render approval banner */
    _renderBanner: (pendingApproval, urls) => {
      if (pendingApproval <= 0) return "";
      return `
                <div class="approval-banner">
                    <span class="ab-icon">🔔</span>
                    <span class="ab-text">
                        <strong>${pendingApproval}</strong>
                        session${pendingApproval !== 1 ? "s" : ""} pending your review
                    </span>
                    <a href="${urls.sessionsPendingApproval}" class="btn-approve">Review &amp; Approve →</a>
                </div>
            `;
    },

    /** Render greeting */
    _renderGreeting: (data) => `
            <div class="dash-greeting">
                <span>📅</span>
                <span>${data.current_month || ""} overview</span>
            </div>
        `,

    /** Render KPI grid */
    _renderKpiGrid: (counts, data) => {
      const month = data.current_month || "This month";
      const cards = [
        {
          variant: "kpi-pending",
          icon: "📋",
          value: counts.pending || 0,
          label: "Pending Calibrations",
          sub: month,
        },
        {
          variant: "kpi-pushed",
          icon: "📌",
          value: counts.pushed || 0,
          label: "Pushed Schedules",
          sub: "Carried from prior period",
        },
        {
          variant: "kpi-overdue",
          icon: "⚠️",
          value: counts.overdue || 0,
          label: "Overdue Items",
          sub: "Deadline passed",
        },
        {
          variant: "kpi-approved",
          icon: "✅",
          value: counts.completed || 0,
          label: "Approved Sessions",
          sub: month,
        },
      ];
      return `<div class="kpi-grid">${CardRenderer.kpiCards(cards)}</div>`;
    },

    /** Render middle row with donut and gauges */
    _renderMidRow: (data, equipPct, calibOk, totalEquip) => {
      const needCalib = totalEquip - calibOk;
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
      ];

      return `
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
                            ${CardRenderer.gaugeRows(gaugeRows)}
                            <div class="gauge-row gauge-total-row">
                                <div class="gauge-label-row">
                                    <span class="gl-name">TOTAL APPROVED (ALL TIME)</span>
                                    <span class="gl-pct">${data.total_sessions || 0}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
    },

    /** Render bottom row with sessions and quick actions */
    _renderBottomRow: (data, urls, pendingApproval) => {
      const recent = data.recent_sessions || [];
      const actions = [
        { href: urls.performCalibration, icon: "🔬", label: "New Calibration" },
        {
          href: urls.sessionsPendingApproval,
          icon: "📝",
          label: "Pending Approval",
          badge: pendingApproval,
        },
        { href: urls.certificates, icon: "🏅", label: "Certificates" },
        { href: urls.sessionList, icon: "📚", label: "All Sessions" },
        { href: urls.auditLog, icon: "🗂️", label: "Audit Log" },
      ];

      return `
                <div class="bottom-row">
                    <div class="panel">
                        <div class="panel-title">🕐 Recent Approved Sessions</div>
                        <table class="sessions-table">
                            <thead>
                                <tr>
                                    <th>Device</th>
                                    <th>Serial</th>
                                    <th>Procedure</th>
                                    <th>Date</th>
                                    <th>Result</th>
                                </tr>
                            </thead>
                            <tbody>${CardRenderer.sessionRows(recent, urls)}</tbody>
                        </table>
                        <div class="mt-2 text-end">
                            <a href="${urls.sessionsPendingApproval}" class="sessions-view-all">View all pending →</a>
                        </div>
                    </div>
                    <div class="panel">
                        <div class="panel-title">⚡ Quick Actions</div>
                        <div class="qa-list">${CardRenderer.quickActions(actions)}</div>
                    </div>
                </div>
            `;
    },
  };

  // -------- DASHBOARD CONTROLLER --------
  const DashboardController = {
    /** Initialize dashboard */
    init: () => {
      try {
        // Initialize CSRF
        const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]");
        if (csrfToken) state.csrfToken = csrfToken.value;

        // Load dashboard data
        DashboardController.load();

        // Set up auto-refresh
        setInterval(DashboardController.refresh, CONFIG.REFRESH_INTERVAL);

        // Set up event listeners
        DashboardController._setupEventListeners();

        console.log("Dashboard initialized successfully");
      } catch (error) {
        console.error("Error initializing dashboard:", error);
      }
    },

    /** Load dashboard data */
    load: async () => {
      const content = document.getElementById("dashboardContent");
      if (!content) return;

      try {
        const response = await fetch(window.DashboardURLs.dashboardData, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        const data = await response.json();

        state.dashboardData = data;
        const urls = window.DashboardURLs;

        // Render dashboard
        const result = DashboardRenderer.render(data, urls);
        content.innerHTML = result.content;

        // Render charts
        ChartRenderer.drawDonutChart("equipDonut", data);

        // Render performance chart if canvas exists
        const perfCanvas = document.getElementById("performanceChart");
        if (perfCanvas) {
          ChartRenderer.drawPerformanceChart(perfCanvas, state.currentPeriod);
        }
      } catch (error) {
        console.error("Dashboard error:", error);
        content.innerHTML =
          '<div class="alert alert-danger">Error loading dashboard. Please refresh.</div>';
      }
    },

    /** Refresh dashboard data */
    refresh: async () => {
      const refreshButtons = document.querySelectorAll(".refresh-button");

      // Update button states
      refreshButtons.forEach((btn) => {
        btn.classList.add("loading");
        btn.textContent = "⏳ Refreshing...";
        btn.disabled = true;
      });

      try {
        const response = await fetch("/calibration/api/dashboard-metrics/");
        if (!response.ok)
          throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();

        if (data.success) {
          state.dashboardData = data.metrics;
          DashboardController._updateMetrics(data.metrics);
          DashboardController._showNotification(
            "Dashboard refreshed successfully",
            "success",
          );
        } else {
          throw new Error(data.message || "Failed to refresh dashboard");
        }
      } catch (error) {
        console.error("Error refreshing dashboard:", error);
        DashboardController._showNotification(
          "Failed to refresh dashboard",
          "error",
        );
      } finally {
        refreshButtons.forEach((btn) => {
          btn.classList.remove("loading");
          btn.textContent = "🔄 Refresh";
          btn.disabled = false;
        });
      }
    },

    /** Update dashboard metrics */
    _updateMetrics: (metrics) => {
      try {
        // Update KPI counts
        if (metrics.schedule_counts) {
          const cards = document.querySelectorAll(".kpi-card .kpi-num");
          const counts = [
            metrics.schedule_counts.pending || 0,
            metrics.schedule_counts.pushed || 0,
            metrics.schedule_counts.overdue || 0,
            metrics.schedule_counts.completed || 0,
          ];
          cards.forEach((el, i) => {
            if (counts[i] !== undefined) {
              const current = parseInt(el.textContent) || 0;
              Utils.animateNumber(el, current, counts[i]);
            }
          });
        }

        // Update performance metrics
        if (metrics.performance) {
          const metricCards = document.querySelectorAll(".metric-value");
          if (metricCards.length >= 2) {
            if (metrics.performance.week_pass_rate !== undefined) {
              Utils.animateNumber(
                metricCards[0],
                parseFloat(metricCards[0].textContent) || 0,
                metrics.performance.week_pass_rate,
                "%",
              );
            }
            if (metrics.performance.month_pass_rate !== undefined) {
              Utils.animateNumber(
                metricCards[1],
                parseFloat(metricCards[1].textContent) || 0,
                metrics.performance.month_pass_rate,
                "%",
              );
            }
          }
        }

        // Update notifications
        if (metrics.notifications) {
          DashboardController._updateNotificationBell(
            metrics.notifications.length,
          );
        }

        // Refresh chart
        const perfCanvas = document.getElementById("performanceChart");
        if (perfCanvas) {
          ChartRenderer.drawPerformanceChart(perfCanvas, state.currentPeriod);
        }

        console.log("Dashboard metrics updated successfully");
      } catch (error) {
        console.error("Error updating dashboard metrics:", error);
      }
    },

    /** Update notification bell */
    _updateNotificationBell: (count) => {
      const bell = document.getElementById("notification-count");
      if (bell) {
        bell.textContent = count;
        bell.style.display = count > 0 ? "flex" : "none";
        bell.style.animation = count > 0 ? "pulse 2s infinite" : "none";
      }
    },

    /** Show notification */
    _showNotification: (message, type = "info") => {
      // Implement notification display
      console.log(`[${type}] ${message}`);
    },

    /** Toggle notifications panel */
    toggleNotifications: () => {
      const panel = document.getElementById("notifications-panel");
      if (panel) {
        const isVisible = panel.style.display !== "none";
        panel.style.display = isVisible ? "none" : "block";

        if (!isVisible) {
          panel.style.opacity = "0";
          panel.style.transform = "translateY(-10px)";
          setTimeout(() => {
            panel.style.transition = "all 0.3s ease";
            panel.style.opacity = "1";
            panel.style.transform = "translateY(0)";
          }, 10);
        }
      }
    },

    /** Filter schedules by type */
    filterSchedules: (type) => {
      const baseUrl = "/calibration/schedules/";
      const params = {
        pending: "?status=pending",
        overdue: "?overdue=true",
        in_progress: "?status=in_progress",
        completed: "?status=completed",
      };
      window.location.href = baseUrl + (params[type] || "");
    },

    /** Show chart for specific period */
    showChart: (period) => {
      state.currentPeriod = period;
      const canvas = document.getElementById("performanceChart");
      if (canvas) {
        ChartRenderer.drawPerformanceChart(canvas, period);

        // Update button states
        const buttons = document.querySelectorAll(".card-header button");
        buttons.forEach((btn) => {
          btn.classList.remove("btn-primary");
          btn.classList.add("btn");
        });

        const clickedButton = Array.from(buttons).find((btn) =>
          btn.textContent.toLowerCase().includes(period.toLowerCase()),
        );
        if (clickedButton) {
          clickedButton.classList.remove("btn");
          clickedButton.classList.add("btn-primary");
        }
      }
    },

    /** Refresh activities */
    refreshActivities: async () => {
      console.log("Refreshing recent activities...");
      const refreshButton = document.querySelector(
        "#recent-sessions .refresh-button",
      );
      if (refreshButton) {
        refreshButton.textContent = "⏳";
        refreshButton.disabled = true;
      }

      try {
        const response = await fetch(
          "/calibration/api/recent-activities/?period=week&limit=5",
        );
        const data = await response.json();

        if (data.success) {
          DashboardController._updateActivities(data.activities);
          DashboardController._showNotification(
            "Recent sessions refreshed",
            "success",
          );
        } else {
          throw new Error(data.message || "Failed to refresh activities");
        }
      } catch (error) {
        console.error("Error refreshing activities:", error);
        DashboardController._showNotification(
          "Failed to refresh activities",
          "error",
        );
      } finally {
        if (refreshButton) {
          refreshButton.textContent = "🔄";
          refreshButton.disabled = false;
        }
      }
    },

    /** Update activities */
    _updateActivities: (activities) => {
      const container = document.getElementById("recent-sessions");
      if (!container) return;

      container.innerHTML = CardRenderer.scheduleItems(activities);
    },

    /** Set up event listeners */
    _setupEventListeners: () => {
      // Click outside notifications panel
      document.addEventListener("click", (event) => {
        const panel = document.getElementById("notifications-panel");
        const bell = document.querySelector(".notification-bell");
        if (
          panel &&
          panel.style.display !== "none" &&
          !panel.contains(event.target) &&
          !bell.contains(event.target)
        ) {
          panel.style.display = "none";
        }
      });

      // Keyboard shortcuts
      document.addEventListener("keydown", (event) => {
        // ESC to close notifications
        if (event.key === "Escape") {
          const panel = document.getElementById("notifications-panel");
          if (panel && panel.style.display !== "none") {
            panel.style.display = "none";
          }
        }
        // Ctrl+Shift+R to refresh
        if (
          (event.ctrlKey || event.metaKey) &&
          event.key === "r" &&
          event.shiftKey
        ) {
          event.preventDefault();
          DashboardController.refresh();
        }
      });

      // Resize handler
      window.addEventListener("resize", () => {
        clearTimeout(state.resizeTimeout);
        state.resizeTimeout = setTimeout(() => {
          const canvas = document.getElementById("performanceChart");
          if (canvas) {
            ChartRenderer.drawPerformanceChart(canvas, state.currentPeriod);
          }
        }, 100);
      });

      // Theme change handler
      document.addEventListener("themechange", () => {
        DashboardController.load();
      });

      // Quick select buttons
      document.querySelectorAll(".quick-select-btn").forEach((button) => {
        button.addEventListener("click", function () {
          const scheduleId = this.getAttribute("data-schedule-id");
          const equipmentId = this.getAttribute("data-equipment-id");
          DashboardController._selectEquipment(scheduleId, equipmentId);
        });
      });
    },

    /** Select equipment for calibration */
    _selectEquipment: (scheduleId, equipmentId) => {
      const baseUrl = "{% url 'calibration:perform_calibration' %}";
      const url = `${baseUrl}?schedule=${scheduleId}&equipment=${equipmentId}&quick_select=true`;
      window.location.href = url;
    },
  };

  // -------- EXPOSE PUBLIC API --------
  window.CalSoftDashboard = {
    refresh: DashboardController.refresh.bind(DashboardController),
    showChart: DashboardController.showChart.bind(DashboardController),
    toggleNotifications:
      DashboardController.toggleNotifications.bind(DashboardController),
    filterSchedules:
      DashboardController.filterSchedules.bind(DashboardController),
    refreshActivities:
      DashboardController.refreshActivities.bind(DashboardController),
    load: DashboardController.load.bind(DashboardController),
  };

  // -------- INITIALIZE --------
  document.addEventListener("DOMContentLoaded", DashboardController.init);
})();
