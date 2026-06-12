// Dashboard JavaScript for CalSoft
// Global variables
let dashboardData = {};
let currentChartPeriod = 'week';

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', function () {
    initializeDashboard();
});

/**
 * Initialize dashboard functionality
 */
function initializeDashboard() {
    try {
        initializeChart();

        // Get notification count from the page
        const notificationCountElement = document.getElementById('notification-count');
        const count = notificationCountElement ? parseInt(notificationCountElement.textContent) || 0 : 0;
        updateNotificationBell(count);

        // Auto-refresh every 5 minutes (300000ms)
        setInterval(refreshDashboard, 300000);

        // Initialize CSRF token
        initializeCSRF();

        console.log('Dashboard initialized successfully');
    } catch (error) {
        console.error('Error initializing dashboard:', error);
    }
}

/**
 * Initialize CSRF token for AJAX requests
 */
function initializeCSRF() {
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
    if (csrfToken) {
        window.csrfToken = csrfToken.value;
    }
}

/**
 * Update notification bell with count
 * @param {number} count - Number of notifications
 */
function updateNotificationBell(count) {
    const notificationBell = document.getElementById('notification-count');
    if (notificationBell) {
        notificationBell.textContent = count;
        notificationBell.style.display = count > 0 ? 'flex' : 'none';

        // Add pulse animation for new notifications
        if (count > 0) {
            notificationBell.style.animation = 'pulse 2s infinite';
        } else {
            notificationBell.style.animation = 'none';
        }
    }
}

/**
 * Toggle notifications panel visibility
 */
function toggleNotifications() {
    const panel = document.getElementById('notifications-panel');
    if (panel) {
        const isVisible = panel.style.display !== 'none';
        panel.style.display = isVisible ? 'none' : 'block';

        // Add fade animation
        if (!isVisible) {
            panel.style.opacity = '0';
            panel.style.transform = 'translateY(-10px)';
            setTimeout(() => {
                panel.style.transition = 'all 0.3s ease';
                panel.style.opacity = '1';
                panel.style.transform = 'translateY(0)';
            }, 10);
        }
    }
}

/**
 * Refresh dashboard data
 */
function refreshDashboard() {
    const refreshButtons = document.querySelectorAll('.refresh-button');

    // Update button states
    refreshButtons.forEach(btn => {
        btn.classList.add('loading');
        btn.textContent = '⏳ Refreshing...';
        btn.disabled = true;
    });

    // Fetch updated dashboard metrics
    fetch('/calibration/api/dashboard-metrics/')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.success) {
                updateDashboardMetrics(data.metrics);
                showNotification('Dashboard refreshed successfully', 'success');
            } else {
                throw new Error(data.message || 'Failed to refresh dashboard');
            }
        })
        .catch(error => {
            console.error('Error refreshing dashboard:', error);
            showNotification('Failed to refresh dashboard', 'error');
        })
        .finally(() => {
            // Reset button states
            refreshButtons.forEach(btn => {
                btn.classList.remove('loading');
                btn.textContent = '🔄 Refresh';
                btn.disabled = false;
            });
        });
}

/**
 * Update dashboard metrics with new data
 * @param {Object} metrics - Updated metrics data
 */
function updateDashboardMetrics(metrics) {
    try {
        // Update schedule counts
        updateScheduleCounts(metrics.schedule_counts);

        // Update performance metrics
        updatePerformanceMetrics(metrics.performance);

        // Update notifications
        if (metrics.notifications) {
            updateNotificationBell(metrics.notifications.length);
        }

        // Update chart with new data
        initializeChart();

        console.log('Dashboard metrics updated successfully');
    } catch (error) {
        console.error('Error updating dashboard metrics:', error);
    }
}

/**
 * Update schedule count cards
 * @param {Object} scheduleCounts - Schedule count data
 */
function updateScheduleCounts(scheduleCounts) {
    if (!scheduleCounts) return;

    const scheduleCards = document.querySelectorAll('.stat-card');
    const counts = [
        scheduleCounts.pending || 0,
        scheduleCounts.overdue || 0,
        scheduleCounts.in_progress || 0,
        scheduleCounts.completed || 0
    ];

    scheduleCards.forEach((card, index) => {
        const numberElement = card.querySelector('.stat-number');
        if (numberElement && counts[index] !== undefined) {
            animateNumber(numberElement, parseInt(numberElement.textContent) || 0, counts[index]);
        }
    });
}

/**
 * Update performance metrics cards
 * @param {Object} performance - Performance data
 */
function updatePerformanceMetrics(performance) {
    if (!performance) return;

    const metricCards = document.querySelectorAll('.metric-value');
    if (metricCards.length >= 2) {
        if (performance.week_pass_rate !== undefined) {
            animateNumber(metricCards[0],
                parseFloat(metricCards[0].textContent) || 0,
                performance.week_pass_rate,
                '%'
            );
        }
        if (performance.month_pass_rate !== undefined) {
            animateNumber(metricCards[1],
                parseFloat(metricCards[1].textContent) || 0,
                performance.month_pass_rate,
                '%'
            );
        }
    }
}

/**
 * Animate number changes
 * @param {HTMLElement} element - Element to animate
 * @param {number} from - Starting value
 * @param {number} to - Ending value
 * @param {string} suffix - Optional suffix (like %)
 */
function animateNumber(element, from, to, suffix = '') {
    const duration = 1000; // 1 second
    const steps = 30;
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
}

/**
 * Refresh recent activities section
 */
function refreshActivities() {
    console.log('Refreshing recent activities...');

    // Show loading state
    const refreshButton = document.querySelector('#recent-sessions .refresh-button');
    if (refreshButton) {
        refreshButton.textContent = '⏳';
        refreshButton.disabled = true;
    }

    fetch('/calibration/api/recent-activities/?period=week&limit=5')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                updateRecentActivities(data.activities);
                showNotification('Recent sessions refreshed', 'success');
            } else {
                throw new Error(data.message || 'Failed to refresh activities');
            }
        })
        .catch(error => {
            console.error('Error refreshing activities:', error);
            showNotification('Failed to refresh activities', 'error');
        })
        .finally(() => {
            // Reset button state
            if (refreshButton) {
                refreshButton.textContent = '🔄';
                refreshButton.disabled = false;
            }
        });
}

/**
 * Update recent activities section
 * @param {Array} activities - Recent activities data
 */
function updateRecentActivities(activities) {
    const container = document.getElementById('recent-sessions');
    if (!container || !activities) return;

    try {
        // Clear current content
        container.innerHTML = '';

        if (activities.length === 0) {
            // Show empty state
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📋</div>
                    <h4>No recent calibration sessions</h4>
                    <p>No calibration sessions have been performed this week</p>
                    <a href="/calibration/perform_calibration/" class="btn btn-primary">Start Calibration</a>
                </div>
            `;
            return;
        }

        // Create sessions HTML
        let sessionsHTML = '';
        activities.slice(0, 5).forEach(session => {
            const statusClass = session.overall_pass ? 'completed' : 'failed';
            const statusText = session.overall_pass ? 'PASSED' : 'FAILED';
            const progressWidth = session.overall_pass ? '100' : '50';

            sessionsHTML += `
                <div class="schedule-item ${statusClass}">
                    <div class="schedule-info">
                        <div class="schedule-equipment">
                            ${session.equipment ?
                    `${session.equipment.description} - ${session.device_serial || 'N/A'}` :
                    `${session.device_model || 'Unknown Model'} - ${session.device_serial || 'N/A'}`
                }
                        </div>
                        <div class="schedule-details">
                            Procedure: ${session.procedure ? session.procedure.name : 'No procedure assigned'}
                            <br>
                            Performed by: ${session.performed_by_name || 'Unknown'}
                            <br>
                            Status: <strong>${statusText}</strong>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: ${progressWidth}%;"></div>
                        </div>
                    </div>
                    <div class="schedule-actions">
                        <div class="schedule-date">
                            ${formatDate(session.timestamp)}
                            <div class="time-ago">${timeAgo(session.timestamp)}</div>
                        </div>
                        <div class="action-buttons">
                            <a href="/calibration/session/${session.id}/" class="btn btn-sm">View Details</a>
                            ${session.overall_pass && session.equipment ?
                    `<form action="/calibration/complete-from-session/${session.id}/" method="post" style="display: inline;">
                                    <input type="hidden" name="csrfmiddlewaretoken" value="${window.csrfToken}">
                                    <button type="submit" class="btn btn-success btn-sm">Complete Schedule</button>
                                </form>` :
                    ''
                }
                        </div>
                    </div>
                </div>
            `;
        });

        // Add summary
        const summaryHTML = `
            <div class="sessions-summary">
                <p class="text-muted">
                    Showing ${Math.min(activities.length, 5)} of ${activities.length} sessions this week
                    ${activities.length > 5 ?
                '<a href="/calibration/sessions/?week=current" class="btn-link">View all this week\'s sessions</a>' :
                ''
            }
                </p>
            </div>
        `;

        container.innerHTML = sessionsHTML + summaryHTML;

    } catch (error) {
        console.error('Error updating recent activities:', error);
        // Fallback to page reload if update fails
        location.reload();
    }
}

/**
 * Filter schedules by type
 * @param {string} type - Schedule type to filter
 */
function filterSchedules(type) {
    let url = '/calibration/schedules/';

    switch (type) {
        case 'pending':
            url += '?status=pending';
            break;
        case 'overdue':
            url += '?overdue=true';
            break;
        case 'in_progress':
            url += '?status=in_progress';
            break;
        case 'completed':
            url += '?status=completed';
            break;
        default:
            break;
    }

    window.location.href = url;
}

/**
 * Initialize performance chart
 */
function initializeChart() {
    const canvas = document.getElementById('performanceChart');
    if (!canvas) return;

    try {
        const ctx = canvas.getContext('2d');
        drawPerformanceChart(ctx, canvas.width, canvas.height, currentChartPeriod);
    } catch (error) {
        console.error('Error initializing chart:', error);
    }
}

/**
 * Draw performance chart
 * @param {CanvasRenderingContext2D} ctx - Canvas context
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @param {string} period - Time period
 */
function drawPerformanceChart(ctx, width, height, period) {
    try {
        ctx.clearRect(0, 0, width, height);

        // Get pass rate data from the page or use defaults
        const weekPassRate = getPassRateFromPage('week') || 0;
        const monthPassRate = getPassRateFromPage('month') || 0;
        const quarterPassRate = getPassRateFromPage('quarter') || 0;

        let data, labels;

        switch (period) {
            case 'week':
                data = [weekPassRate];
                labels = ['This Week'];
                break;
            case 'month':
                data = [monthPassRate];
                labels = ['This Month'];
                break;
            case 'quarter':
                data = [weekPassRate, monthPassRate, quarterPassRate];
                labels = ['Week', 'Month', 'Quarter'];
                break;
            default:
                data = [weekPassRate];
                labels = ['Week'];
        }

        drawChart(ctx, width, height, data, labels);
    } catch (error) {
        console.error('Error drawing chart:', error);
        drawErrorChart(ctx, width, height);
    }
}

/**
 * Get pass rate from page elements
 * @param {string} period - Time period
 * @returns {number} Pass rate value
 */
function getPassRateFromPage(period) {
    try {
        const metricCards = document.querySelectorAll('.metric-value');
        switch (period) {
            case 'week':
                return metricCards[0] ? parseFloat(metricCards[0].textContent) : 0;
            case 'month':
                return metricCards[1] ? parseFloat(metricCards[1].textContent) : 0;
            case 'quarter':
                // Quarter data would need to be provided by backend
                return 85; // Default fallback
            default:
                return 0;
        }
    } catch (error) {
        console.error('Error getting pass rate from page:', error);
        return 0;
    }
}

/**
 * Draw the actual chart
 * @param {CanvasRenderingContext2D} ctx - Canvas context
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @param {Array} data - Data points
 * @param {Array} labels - Labels for data points
 */
function drawChart(ctx, width, height, data, labels) {
    const maxValue = 100;
    const padding = 40;
    const chartWidth = width - 2 * padding;
    const chartHeight = height - 2 * padding;

    // Draw grid lines
    ctx.strokeStyle = '#f0f0f0';
    ctx.lineWidth = 1;

    for (let i = 0; i <= 5; i++) {
        const y = padding + (i * chartHeight) / 5;
        ctx.beginPath();
        ctx.moveTo(padding, y);
        ctx.lineTo(width - padding, y);
        ctx.stroke();

        // Y-axis labels
        ctx.fillStyle = '#666';
        ctx.font = '12px Arial, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText((100 - (i * 20)) + '%', padding - 10, y + 4);
    }

    // Draw axes
    ctx.strokeStyle = '#ddd';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(padding, padding);
    ctx.lineTo(padding, height - padding);
    ctx.lineTo(width - padding, height - padding);
    ctx.stroke();

    // Draw data points and lines
    if (data.length > 0) {
        drawDataPoints(ctx, data, labels, padding, chartWidth, chartHeight, height, maxValue);
    }
}

/**
 * Draw data points and connecting lines
 * @param {CanvasRenderingContext2D} ctx - Canvas context
 * @param {Array} data - Data points
 * @param {Array} labels - Labels
 * @param {number} padding - Chart padding
 * @param {number} chartWidth - Chart width
 * @param {number} chartHeight - Chart height
 * @param {number} height - Total height
 * @param {number} maxValue - Maximum value
 */
function drawDataPoints(ctx, data, labels, padding, chartWidth, chartHeight, height, maxValue) {
    ctx.strokeStyle = '#4fc3f7';
    ctx.lineWidth = 3;
    ctx.beginPath();

    const points = [];

    data.forEach((value, index) => {
        const x = data.length === 1 ?
            padding + chartWidth / 2 :
            padding + (index * chartWidth) / (data.length - 1);
        const y = height - padding - (value / maxValue) * chartHeight;

        points.push({ x, y, value });

        if (index === 0) {
            ctx.moveTo(x, y);
        } else {
            ctx.lineTo(x, y);
        }
    });

    // Draw line for multiple points
    if (data.length > 1) {
        ctx.stroke();

        // Add gradient fill
        const gradient = ctx.createLinearGradient(0, padding, 0, height - padding);
        gradient.addColorStop(0, 'rgba(79, 195, 247, 0.3)');
        gradient.addColorStop(1, 'rgba(79, 195, 247, 0.0)');

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.moveTo(padding, height - padding);
        points.forEach(point => ctx.lineTo(point.x, point.y));
        ctx.lineTo(points[points.length - 1].x, height - padding);
        ctx.closePath();
        ctx.fill();
    }

    // Draw points and labels
    points.forEach((point, index) => {
        // Draw point
        ctx.fillStyle = '#29b6f6';
        ctx.beginPath();
        ctx.arc(point.x, point.y, 6, 0, 2 * Math.PI);
        ctx.fill();

        // Draw value label
        ctx.fillStyle = '#333';
        ctx.font = 'bold 14px Arial, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(point.value.toFixed(1) + '%', point.x, point.y - 15);

        // Draw x-axis label
        ctx.fillStyle = '#666';
        ctx.font = '12px Arial, sans-serif';
        ctx.fillText(labels[index], point.x, height - padding + 20);
    });
}

/**
 * Draw error chart when data is unavailable
 * @param {CanvasRenderingContext2D} ctx - Canvas context
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 */
function drawErrorChart(ctx, width, height) {
    ctx.fillStyle = '#666';
    ctx.font = '16px Arial, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Chart data unavailable', width / 2, height / 2);
}

/**
 * Show chart for specific period
 * @param {string} period - Time period
 */
function showChart(period) {
    currentChartPeriod = period;

    const canvas = document.getElementById('performanceChart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    drawPerformanceChart(ctx, canvas.width, canvas.height, period);

    // Update button states
    const buttons = document.querySelectorAll('.card-header button');
    buttons.forEach(btn => {
        btn.classList.remove('btn-primary');
        btn.classList.add('btn');
    });

    // Find and highlight the clicked button
    const clickedButton = Array.from(buttons).find(btn =>
        btn.textContent.toLowerCase().includes(period.toLowerCase())
    );
    if (clickedButton) {
        clickedButton.classList.remove('btn');
        clickedButton.classList.add('btn-primary');
    }
}

/**
 * Mark single notification as read
 * @param {number} notificationId - Notification ID
 */
function markNotificationRead(notificationId) {
    if (!window.csrfToken) {
        console.error('CSRF token not available');
        return;
    }

    fetch(`/calibration/notifications/${notificationId}/read/`, {
        method: 'POST',
        headers: {
            'X-CSRFToken': window.csrfToken,
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const notificationElement = document.querySelector(`[data-id="${notificationId}"]`);
                if (notificationElement) {
                    notificationElement.style.transition = 'all 0.3s ease';
                    notificationElement.style.opacity = '0';
                    notificationElement.style.transform = 'translateX(20px)';

                    setTimeout(() => {
                        notificationElement.remove();
                        const remainingNotifications = document.querySelectorAll('.notification-item').length;
                        updateNotificationBell(remainingNotifications);

                        if (remainingNotifications === 0) {
                            const notificationsList = document.getElementById('notifications-list');
                            if (notificationsList) {
                                notificationsList.innerHTML = '<div class="empty-state"><p>No new notifications</p></div>';
                            }
                        }
                    }, 300);
                }
            } else {
                showNotification('Failed to mark notification as read', 'error');
            }
        })
        .catch(error => {
            console.error('Error marking notification as read:', error);
            showNotification('Error marking notification as read', 'error');
        });
}

/**
 * Mark all notifications as read
 */
function markAllNotificationsRead() {
    if (!window.csrfToken) {
        console.error('CSRF token not available');
        return;
    }

    fetch('/calibration/notifications/mark-all-read/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': window.csrfToken,
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const notificationsList = document.getElementById('notifications-list');
                if (notificationsList) {
                    notificationsList.innerHTML = '<div class="empty-state"><p>No new notifications</p></div>';
                }
                updateNotificationBell(0);
                showNotification('All notifications marked as read', 'success');
            } else {
                showNotification('Failed to mark notifications as read', 'error');
            }
        })
        .catch(error => {
            console.error('Error marking all notifications as read:', error);
            showNotification('Error marking notifications as read', 'error');
        });
}

/**
 * Show temporary notification message
 * @param {string} message - Notification message
 * @param {string} type - Notification type (success, error, info)
 */
function showNotification(message, type = 'info') {
    // notification-toast colours are handled entirely by dashbord.css
    // using CSS custom properties so they respond to light/dark theme.
    const notification = document.createElement('div');
    // 'error' maps to the .error class; keep 'error' as-is (CSS has it)
    notification.className = `notification-toast ${type}`;
    notification.textContent = message;

    document.body.appendChild(notification);

    // Animate in (initial state is translateX(400px) via CSS)
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            notification.style.transform = 'translateX(0)';
        });
    });

    // Auto-dismiss after 3 s
    setTimeout(() => {
        notification.style.transform = 'translateX(400px)';
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 320);
    }, 3000);
}

/**
 * Close notification panel when clicking outside
 */
document.addEventListener('click', function (event) {
    const notificationsPanel = document.getElementById('notifications-panel');
    const notificationBell = document.querySelector('.notification-bell');

    if (notificationsPanel &&
        notificationsPanel.style.display !== 'none' &&
        !notificationsPanel.contains(event.target) &&
        !notificationBell.contains(event.target)) {
        notificationsPanel.style.display = 'none';
    }
});

/**
 * Handle keyboard shortcuts
 */
document.addEventListener('keydown', function (event) {
    // ESC to close notifications panel
    if (event.key === 'Escape') {
        const notificationsPanel = document.getElementById('notifications-panel');
        if (notificationsPanel && notificationsPanel.style.display !== 'none') {
            notificationsPanel.style.display = 'none';
        }
    }

    // Ctrl/Cmd + R to refresh dashboard
    if ((event.ctrlKey || event.metaKey) && event.key === 'r' && event.shiftKey) {
        event.preventDefault();
        refreshDashboard();
    }
});

/**
 * Handle responsive chart resizing
 */
window.addEventListener('resize', function () {
    setTimeout(initializeChart, 100);
});

/**
 * Utility function to format numbers with commas
 * @param {number} num - Number to format
 * @returns {string} Formatted number
 */
function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/**
 * Utility function to format dates
 * @param {Date|string} date - Date to format
 * @returns {string} Formatted date
 */
function formatDate(date) {
    if (typeof date === 'string') {
        date = new Date(date);
    }
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

/**
 * Utility function to calculate time ago
 * @param {Date|string} date - Date to calculate from
 * @returns {string} Time ago string
 */
function timeAgo(date) {
    if (typeof date === 'string') {
        date = new Date(date);
    }

    const now = new Date();
    const diffInMs = now - date;
    const diffInSec = Math.floor(diffInMs / 1000);
    const diffInMin = Math.floor(diffInSec / 60);
    const diffInHour = Math.floor(diffInMin / 60);
    const diffInDay = Math.floor(diffInHour / 24);

    if (diffInDay > 0) {
        return `${diffInDay} day${diffInDay > 1 ? 's' : ''} ago`;
    } else if (diffInHour > 0) {
        return `${diffInHour} hour${diffInHour > 1 ? 's' : ''} ago`;
    } else if (diffInMin > 0) {
        return `${diffInMin} minute${diffInMin > 1 ? 's' : ''} ago`;
    } else {
        return 'Just now';
    }
}

// Export functions for global access (if needed)
window.CalSoftDashboard = {
    refreshDashboard,
    showChart,
    toggleNotifications,
    markNotificationRead,
    markAllNotificationsRead,
    filterSchedules,
    refreshActivities,
    showNotification
};
