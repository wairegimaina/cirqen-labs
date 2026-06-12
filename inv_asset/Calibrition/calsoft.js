// Dashboard JavaScript for CalSoft
// Global variables
let dashboardData = {};
let currentChartPeriod = 'week';

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
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
    
    switch(type) {
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

function getPassRateFromPage(period) {
    try {
        const metricCards = document.querySelectorAll('.metric-value');
        switch(period) {
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
 * Draw performance chart with enhanced styling
 * @param {CanvasRenderingContext2D} ctx - Canvas context
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @param {string} period - Time period
 */
function drawPerformanceChart(ctx, width, height, period) {
    try {
        // Clear canvas with subtle background
        ctx.clearRect(0, 0, width, height);
        
        // Add subtle background gradient
        const bgGradient = ctx.createLinearGradient(0, 0, 0, height);
        bgGradient.addColorStop(0, '#fafbfc');
        bgGradient.addColorStop(1, '#f8f9fa');
        ctx.fillStyle = bgGradient;
        ctx.fillRect(0, 0, width, height);
        
        // Get pass rate data from the page or use defaults
        const weekPassRate = getPassRateFromPage('week') || 0;
        const monthPassRate = getPassRateFromPage('month') || 0;
        const quarterPassRate = getPassRateFromPage('quarter') || 0;
        
        let data, labels;
        
        switch(period) {
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
        
        drawEnhancedChart(ctx, width, height, data, labels);
    } catch (error) {
        console.error('Error drawing chart:', error);
        drawErrorChart(ctx, width, height);
    }
}

/**
 * Draw the enhanced chart with modern styling
 * @param {CanvasRenderingContext2D} ctx - Canvas context
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @param {Array} data - Data points
 * @param {Array} labels - Labels for data points
 */
function drawEnhancedChart(ctx, width, height, data, labels) {
    const maxValue = 100;
    const padding = 60;
    const chartWidth = width - 2 * padding;
    const chartHeight = height - 2 * padding;
    
    // Enable high-quality rendering
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    
    // Draw background grid with enhanced styling
    drawEnhancedGrid(ctx, padding, chartWidth, chartHeight, height);
    
    // Draw axes with enhanced styling
    drawEnhancedAxes(ctx, padding, chartWidth, chartHeight, height);
    
    // Draw data visualization
    if (data.length > 0) {
        drawEnhancedDataPoints(ctx, data, labels, padding, chartWidth, chartHeight, height, maxValue);
    }
    
    // Add chart title if needed
    drawChartTitle(ctx, width, padding, labels[0] || 'Performance');
}

/**
 * Draw enhanced grid with subtle styling
 */
function drawEnhancedGrid(ctx, padding, chartWidth, chartHeight, height) {
    // Horizontal grid lines
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.05)';
    ctx.lineWidth = 1;
    
    for (let i = 0; i <= 5; i++) {
        const y = padding + (i * chartHeight) / 5;
        
        // Draw grid line
        ctx.beginPath();
        ctx.moveTo(padding, y);
        ctx.lineTo(padding + chartWidth, y);
        ctx.stroke();
        
        // Y-axis labels with better typography
        ctx.fillStyle = '#6b7280';
        ctx.font = '12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText((100 - (i * 20)) + '%', padding - 15, y);
    }
    
    // Vertical grid lines (subtle)
    if (chartWidth > 200) {
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.03)';
        const verticalLines = Math.min(5, Math.floor(chartWidth / 80));
        
        for (let i = 1; i < verticalLines; i++) {
            const x = padding + (i * chartWidth) / verticalLines;
            ctx.beginPath();
            ctx.moveTo(x, padding);
            ctx.lineTo(x, height - padding);
            ctx.stroke();
        }
    }
}

/**
 * Draw enhanced axes
 */
function drawEnhancedAxes(ctx, padding, chartWidth, chartHeight, height) {
    // Main axes with rounded caps
    ctx.strokeStyle = '#d1d5db';
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    
    ctx.beginPath();
    // Y-axis
    ctx.moveTo(padding, padding);
    ctx.lineTo(padding, height - padding);
    // X-axis
    ctx.moveTo(padding, height - padding);
    ctx.lineTo(padding + chartWidth, height - padding);
    ctx.stroke();
    
    ctx.lineCap = 'butt'; // Reset
}

/**
 * Draw enhanced data points with modern styling
 */
function drawEnhancedDataPoints(ctx, data, labels, padding, chartWidth, chartHeight, height, maxValue) {
    const points = [];
    
    // Calculate points
    data.forEach((value, index) => {
        const x = data.length === 1 ? 
            padding + chartWidth / 2 : 
            padding + (index * chartWidth) / (data.length - 1);
        const y = height - padding - (value / maxValue) * chartHeight;
        
        points.push({ x, y, value });
    });
    
    // Draw area fill for multiple points
    if (data.length > 1) {
        drawAreaFill(ctx, points, height, padding);
        drawConnectionLine(ctx, points);
    }
    
    // Draw individual data points
    points.forEach((point, index) => {
        drawDataPoint(ctx, point, index, labels[index]);
    });
    
    // Add value indicators
    drawValueIndicators(ctx, points, labels);
}

/**
 * Draw smooth area fill
 */
function drawAreaFill(ctx, points, height, padding) {
    if (points.length < 2) return;
    
    // Create gradient
    const gradient = ctx.createLinearGradient(0, points[0].y, 0, height - padding);
    gradient.addColorStop(0, 'rgba(59, 130, 246, 0.15)');
    gradient.addColorStop(0.5, 'rgba(59, 130, 246, 0.08)');
    gradient.addColorStop(1, 'rgba(59, 130, 246, 0.02)');
    
    ctx.fillStyle = gradient;
    ctx.beginPath();
    
    // Start from bottom left
    ctx.moveTo(points[0].x, height - padding);
    
    // Draw smooth curve through points
    if (points.length === 2) {
        ctx.lineTo(points[0].x, points[0].y);
        ctx.lineTo(points[1].x, points[1].y);
    } else {
        // Use quadratic curves for smoother lines
        ctx.lineTo(points[0].x, points[0].y);
        
        for (let i = 1; i < points.length - 1; i++) {
            const cpX = (points[i].x + points[i + 1].x) / 2;
            const cpY = (points[i].y + points[i + 1].y) / 2;
            ctx.quadraticCurveTo(points[i].x, points[i].y, cpX, cpY);
        }
        
        ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
    }
    
    // Close path to bottom
    ctx.lineTo(points[points.length - 1].x, height - padding);
    ctx.closePath();
    ctx.fill();
}

/**
 * Draw connection line between points
 */
function drawConnectionLine(ctx, points) {
    if (points.length < 2) return;
    
    // Main line
    ctx.strokeStyle = '#3b82f6';
    ctx.lineWidth = 3;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    
    // Add subtle shadow
    ctx.shadowColor = 'rgba(59, 130, 246, 0.3)';
    ctx.shadowBlur = 4;
    ctx.shadowOffsetY = 2;
    
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    
    if (points.length === 2) {
        ctx.lineTo(points[1].x, points[1].y);
    } else {
        // Smooth curves
        for (let i = 1; i < points.length - 1; i++) {
            const cpX = (points[i].x + points[i + 1].x) / 2;
            const cpY = (points[i].y + points[i + 1].y) / 2;
            ctx.quadraticCurveTo(points[i].x, points[i].y, cpX, cpY);
        }
        ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
    }
    
    ctx.stroke();
    
    // Reset shadow
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;
}

/**
 * Draw individual data points with enhanced styling
 */
function drawDataPoint(ctx, point, index, label) {
    // Outer ring (shadow effect)
    ctx.fillStyle = 'rgba(59, 130, 246, 0.2)';
    ctx.beginPath();
    ctx.arc(point.x, point.y, 10, 0, 2 * Math.PI);
    ctx.fill();
    
    // Main point
    ctx.fillStyle = '#3b82f6';
    ctx.beginPath();
    ctx.arc(point.x, point.y, 6, 0, 2 * Math.PI);
    ctx.fill();
    
    // Inner highlight
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(point.x - 1, point.y - 1, 2, 0, 2 * Math.PI);
    ctx.fill();
    
    // Hover effect simulation for single point
    if (index === 0 && point.value > 0) {
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(point.x, point.y, 12, 0, 2 * Math.PI);
        ctx.stroke();
    }
}

/**
 * Draw value indicators with enhanced typography
 */
function drawValueIndicators(ctx, points, labels) {
    points.forEach((point, index) => {
        // Value label with background
        const valueText = point.value.toFixed(1) + '%';
        const labelText = labels[index];
        
        // Measure text for background
        ctx.font = 'bold 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        const valueMetrics = ctx.measureText(valueText);
        const valueWidth = valueMetrics.width;
        
        // Draw value background
        const bgPadding = 8;
        const bgHeight = 24;
        const bgY = point.y - 35;
        
        ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
        ctx.shadowColor = 'rgba(0, 0, 0, 0.1)';
        ctx.shadowBlur = 8;
        ctx.shadowOffsetY = 2;
        
        // Rounded rectangle for value
        drawRoundedRect(ctx, point.x - (valueWidth / 2) - bgPadding, bgY - bgHeight/2, 
                      valueWidth + (bgPadding * 2), bgHeight, 6);
        ctx.fill();
        
        // Reset shadow
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetY = 0;
        
        // Draw value text
        ctx.fillStyle = '#1f2937';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(valueText, point.x, bgY);
        
        // Draw label below
        ctx.font = '12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillStyle = '#6b7280';
        ctx.fillText(labelText, point.x, point.y + 35);
    });
}

/**
 * Draw rounded rectangle
 */
function drawRoundedRect(ctx, x, y, width, height, radius) {
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + width - radius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
    ctx.lineTo(x + width, y + height - radius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    ctx.lineTo(x + radius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
}

/**
 * Draw chart title
 */
function drawChartTitle(ctx, width, padding, title) {
    ctx.fillStyle = '#374151';
    ctx.font = 'bold 16px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(`${title} Pass Rate`, width / 2, padding / 2);
}

/**
 * Draw enhanced error chart
 */
function drawErrorChart(ctx, width, height) {
    // Clear and add background
    ctx.clearRect(0, 0, width, height);
    
    const bgGradient = ctx.createLinearGradient(0, 0, 0, height);
    bgGradient.addColorStop(0, '#fef2f2');
    bgGradient.addColorStop(1, '#fef7f7');
    ctx.fillStyle = bgGradient;
    ctx.fillRect(0, 0, width, height);
    
    // Error icon and text
    ctx.fillStyle = '#ef4444';
    ctx.font = '24px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('📊', width / 2, height / 2 - 20);
    
    ctx.fillStyle = '#7f1d1d';
    ctx.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.fillText('Chart data unavailable', width / 2, height / 2 + 10);
    
    ctx.fillStyle = '#a3a3a3';
    ctx.font = '12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.fillText('Please refresh the dashboard', width / 2, height / 2 + 30);
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
 * Close notification panel when clicking outside
 */
document.addEventListener('click', function(event) {
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
document.addEventListener('keydown', function(event) {
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
window.addEventListener('resize', function() {
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
   
    filterSchedules,
    refreshActivities,
    
};


document.addEventListener('DOMContentLoaded', function() {
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.getElementById('main-content');
    const navbar = document.querySelector('.navbar');
    
    // Check if elements exist
    if (!sidebarToggle || !sidebar || !mainContent || !navbar) {
        console.warn('Some required elements for sidebar toggle not found');
        return;
    }
    
    // Get stored sidebar state from localStorage (if supported in your environment)
    let sidebarState = 'closed'; // Default to closed for mobile-first approach
    
    // Try to get saved state (handle potential localStorage restrictions)
    try {
        sidebarState = localStorage.getItem('sidebarState') || 'closed';
    } catch (e) {
        // Fall back to sessionStorage or just use default
        sidebarState = 'closed';
    }
    
    // Initialize sidebar state
    function initializeSidebar() {
        if (window.innerWidth > 768) {
            // Desktop: show sidebar by default
            if (sidebarState === 'collapsed') {
                collapseSidebar();
            } else {
                openSidebar();
            }
        } else {
            // Mobile: hide sidebar by default
            closeSidebar();
        }
    }
    
    // Open sidebar (desktop full width)
    function openSidebar() {
        sidebar.classList.remove('collapsed');
        sidebar.classList.add('open');
        mainContent.classList.remove('sidebar-collapsed');
        mainContent.classList.add('sidebar-open');
        navbar.classList.remove('sidebar-collapsed');
        navbar.classList.add('sidebar-open');
        
        // Save state
        try {
            localStorage.setItem('sidebarState', 'open');
        } catch (e) {
            // Handle localStorage restriction
        }
    }
    
    // Collapse sidebar (desktop narrow width)
    function collapseSidebar() {
        sidebar.classList.remove('open');
        sidebar.classList.add('collapsed');
        mainContent.classList.remove('sidebar-open');
        mainContent.classList.add('sidebar-collapsed');
        navbar.classList.remove('sidebar-open');
        navbar.classList.add('sidebar-collapsed');
        
        // Save state
        try {
            localStorage.setItem('sidebarState', 'collapsed');
        } catch (e) {
            // Handle localStorage restriction
        }
    }
    
    // Close sidebar (mobile hidden)
    function closeSidebar() {
        sidebar.classList.remove('open', 'collapsed');
        mainContent.classList.remove('sidebar-open', 'sidebar-collapsed');
        navbar.classList.remove('sidebar-open', 'sidebar-collapsed');
        
        // Save state
        try {
            localStorage.setItem('sidebarState', 'closed');
        } catch (e) {
            // Handle localStorage restriction
        }
    }
    
    // Toggle sidebar based on current state and screen size
    function toggleSidebar() {
        if (window.innerWidth <= 768) {
            // Mobile behavior: toggle open/closed
            if (sidebar.classList.contains('open')) {
                closeSidebar();
            } else {
                openSidebar();
            }
        } else {
            // Desktop behavior: toggle open/collapsed
            if (sidebar.classList.contains('collapsed')) {
                openSidebar();
            } else if (sidebar.classList.contains('open')) {
                collapseSidebar();
            } else {
                openSidebar();
            }
        }
    }
    
    // Event listener for toggle button
    sidebarToggle.addEventListener('click', function(e) {
        e.preventDefault();
        toggleSidebar();
    });
    
    // Handle window resize
    let resizeTimeout;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(function() {
            initializeSidebar();
        }, 250);
    });
    
    // Close sidebar when clicking outside on mobile
    document.addEventListener('click', function(e) {
        if (window.innerWidth <= 768 && 
            sidebar.classList.contains('open') && 
            !sidebar.contains(e.target) && 
            !sidebarToggle.contains(e.target)) {
            closeSidebar();
        }
    });
    
    // Handle escape key to close sidebar on mobile
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && 
            window.innerWidth <= 768 && 
            sidebar.classList.contains('open')) {
            closeSidebar();
        }
    });
    
    // Initialize sidebar on page load
    initializeSidebar();
});
