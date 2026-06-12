{% extends 'base.html' %}
{% load static %}
{% load calibration_filters %}

{% block title %}Calibration Session Detail - {{ session.certificate_number }}{% endblock %}

{% block extra_css %}
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
    .session-container {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        min-height: 100vh;
        padding: 20px;
    }

    .session-card {
        max-width: 1400px;
        margin: 0 auto;
        background: white;
        border-radius: 20px;
        box-shadow: 0 25px 50px rgba(0, 0, 0, 0.1);
        overflow: hidden;
    }

    .session-header {
        background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%);
        color: white;
        padding: 30px;
        position: relative;
        overflow: hidden;
    }

    .session-header::before {
        content: '';
        position: absolute;
        top: -50%;
        right: -50%;
        width: 100%;
        height: 200%;
        background: url('data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="2" fill="rgba(255,255,255,0.1)"/></svg>') repeat;
        animation: float 20s ease-in-out infinite;
    }

    @keyframes float {
        0%, 100% { transform: translateY(0px) rotate(0deg); }
        50% { transform: translateY(-20px) rotate(180deg); }
    }

    .header-content {
        position: relative;
        z-index: 2;
    }

    .session-title {
        font-size: 2.5rem;
        font-weight: 700;
        margin-bottom: 10px;
        text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
    }

    .session-subtitle {
        font-size: 1.2rem;
        opacity: 0.9;
        display: flex;
        align-items: center;
        gap: 15px;
        flex-wrap: wrap;
    }

    .status-badge {
        padding: 8px 16px;
        border-radius: 25px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
        animation: pulse 2s infinite;
    }

    .status-pass {
        background: linear-gradient(135deg, #27ae60, #2ecc71);
        box-shadow: 0 4px 15px rgba(39, 174, 96, 0.3);
    }

    .status-fail {
        background: linear-gradient(135deg, #e74c3c, #c0392b);
        box-shadow: 0 4px 15px rgba(231, 76, 60, 0.3);
    }

    @keyframes pulse {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.05); }
    }

    .session-content {
        padding: 40px;
    }

    .info-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
        gap: 30px;
        margin-bottom: 40px;
    }

    .info-card {
        background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
        border-radius: 15px;
        padding: 25px;
        box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
        transition: all 0.3s ease;
        border: 1px solid rgba(255, 255, 255, 0.2);
    }

    .info-card:hover {
        transform: translateY(-5px);
        box-shadow: 0 15px 35px rgba(0, 0, 0, 0.15);
    }

    .card-header {
        display: flex;
        align-items: center;
        margin-bottom: 20px;
        color: #2c3e50;
    }

    .card-icon {
        font-size: 1.5rem;
        margin-right: 12px;
        padding: 12px;
        background: linear-gradient(135deg, #3498db, #2980b9);
        color: white;
        border-radius: 10px;
        box-shadow: 0 4px 15px rgba(52, 152, 219, 0.3);
    }

    .card-title {
        font-size: 1.3rem;
        font-weight: 700;
    }

    .info-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 12px 0;
        border-bottom: 1px solid rgba(0, 0, 0, 0.05);
    }

    .info-row:last-child {
        border-bottom: none;
    }

    .info-label {
        font-weight: 600;
        color: #5a6c7d;
    }

    .info-value {
        color: #2c3e50;
        font-weight: 500;
    }

    .section {
        margin-bottom: 50px;
    }

    .section-header {
        display: flex;
        align-items: center;
        margin-bottom: 30px;
        padding-bottom: 15px;
        border-bottom: 3px solid #3498db;
    }

    .section-title {
        font-size: 2rem;
        font-weight: 700;
        color: #2c3e50;
        margin-left: 15px;
    }

    .section-icon {
        font-size: 2rem;
        color: #3498db;
        padding: 15px;
        background: linear-gradient(135deg, rgba(52, 152, 219, 0.1), rgba(52, 152, 219, 0.2));
        border-radius: 12px;
    }

    .readings-container {
        display: grid;
        gap: 30px;
    }

    .parameter-group {
        background: white;
        border-radius: 15px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
        overflow: hidden;
        border: 1px solid rgba(0, 0, 0, 0.05);
    }

    .parameter-header {
        background: linear-gradient(135deg, #34495e 0%, #2c3e50 100%);
        color: white;
        padding: 20px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .parameter-name {
        font-size: 1.4rem;
        font-weight: 700;
    }

    .parameter-unit {
        background: rgba(255, 255, 255, 0.2);
        padding: 5px 12px;
        border-radius: 20px;
        font-size: 0.9rem;
    }

    .readings-table {
        width: 100%;
        border-collapse: collapse;
    }

    .readings-table th,
    .readings-table td {
        padding: 15px;
        text-align: center;
        border-bottom: 1px solid rgba(0, 0, 0, 0.05);
    }

    .readings-table th {
        background: linear-gradient(135deg, #ecf0f1 0%, #bdc3c7 100%);
        font-weight: 700;
        color: #2c3e50;
        text-transform: uppercase;
        letter-spacing: 1px;
        font-size: 0.9rem;
    }

    .readings-table tr:hover {
        background: rgba(52, 152, 219, 0.05);
    }

    .tolerance-pass {
        color: #27ae60;
        font-weight: 700;
    }

    .tolerance-fail {
        color: #e74c3c;
        font-weight: 700;
    }

    .chart-container {
        background: white;
        border-radius: 15px;
        padding: 30px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
        margin-bottom: 30px;
    }

    .chart-title {
        font-size: 1.5rem;
        font-weight: 700;
        color: #2c3e50;
        margin-bottom: 20px;
        text-align: center;
    }

    .chart-wrapper {
        position: relative;
        height: 400px;
    }

    .analytics-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
        gap: 30px;
        margin-top: 40px;
    }

    .uncertainty-budget {
        background: linear-gradient(135deg, #fff 0%, #f8f9fa 100%);
        border-radius: 15px;
        padding: 25px;
        box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
    }

    .uncertainty-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 10px 0;
        border-bottom: 1px solid rgba(0, 0, 0, 0.05);
    }

    .uncertainty-label {
        font-weight: 600;
        color: #5a6c7d;
    }

    .uncertainty-value {
        font-weight: 700;
        color: #2c3e50;
    }

    .actions-bar {
        background: linear-gradient(135deg, #ecf0f1 0%, #bdc3c7 100%);
        padding: 25px;
        display: flex;
        justify-content: center;
        gap: 20px;
        flex-wrap: wrap;
    }

    .action-btn {
        padding: 12px 25px;
        border: none;
        border-radius: 25px;
        font-weight: 600;
        text-decoration: none;
        color: white;
        transition: all 0.3s ease;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        cursor: pointer;
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    .btn-primary {
        background: linear-gradient(135deg, #3498db, #2980b9);
        box-shadow: 0 4px 15px rgba(52, 152, 219, 0.3);
    }

    .btn-success {
        background: linear-gradient(135deg, #27ae60, #2ecc71);
        box-shadow: 0 4px 15px rgba(39, 174, 96, 0.3);
    }

    .btn-info {
        background: linear-gradient(135deg, #17a2b8, #138496);
        box-shadow: 0 4px 15px rgba(23, 162, 184, 0.3);
    }

    .btn-warning {
        background: linear-gradient(135deg, #f39c12, #e67e22);
        box-shadow: 0 4px 15px rgba(243, 156, 18, 0.3);
    }

    .action-btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 25px rgba(0, 0, 0, 0.2);
        text-decoration: none;
        color: white;
    }

    .stats-overview {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 20px;
        margin-bottom: 40px;
    }

    .stat-card {
        background: linear-gradient(135deg, #fff 0%, #f8f9fa 100%);
        border-radius: 12px;
        padding: 20px;
        text-align: center;
        box-shadow: 0 5px 15px rgba(0, 0, 0, 0.08);
        border: 2px solid transparent;
        transition: all 0.3s ease;
    }

    .stat-card:hover {
        border-color: #3498db;
        transform: translateY(-3px);
    }

    .stat-value {
        font-size: 2rem;
        font-weight: 700;
        color: #2c3e50;
        margin-bottom: 8px;
    }

    .stat-label {
        font-size: 0.9rem;
        color: #5a6c7d;
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    .environmental-conditions {
        background: linear-gradient(135deg, #e8f8f5 0%, #d5f4e6 100%);
        border-radius: 15px;
        padding: 25px;
        margin-bottom: 30px;
    }

    .env-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 20px;
    }

    .env-item {
        text-align: center;
        padding: 15px;
        background: white;
        border-radius: 10px;
        box-shadow: 0 3px 10px rgba(0, 0, 0, 0.1);
    }

    .env-icon {
        font-size: 2rem;
        margin-bottom: 10px;
        color: #27ae60;
    }

    .env-value {
        font-size: 1.3rem;
        font-weight: 700;
        color: #2c3e50;
        margin-bottom: 5px;
    }

    .env-label {
        font-size: 0.9rem;
        color: #5a6c7d;
    }

    @media (max-width: 768px) {
        .session-card {
            margin: 10px;
            border-radius: 15px;
        }

        .session-header {
            padding: 20px;
        }

        .session-title {
            font-size: 1.8rem;
        }

        .session-content {
            padding: 20px;
        }

        .info-grid {
            grid-template-columns: 1fr;
            gap: 20px;
        }

        .actions-bar {
            padding: 20px;
        }

        .action-btn {
            padding: 10px 20px;
            font-size: 0.9rem;
        }
    }
</style>
{% endblock %}

{% block extra_js %}
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
{% endblock %}

{% block content %}
<div class="session-container">
    <div class="session-card">
        <!-- Header Section -->
        <div class="session-header">
            <div class="header-content">
                <h1 class="session-title">
                    <i class="fas fa-certificate"></i>
                    Calibration Session Detail
                </h1>
                <div class="session-subtitle">
                    <span><i class="fas fa-barcode"></i> Certificate: {{ session.certificate_number }}</span>
                    <span><i class="fas fa-calendar-alt"></i> {{ session.timestamp|date:"F d, Y" }}</span>
                    <span class="status-badge {% if session.overall_pass %}status-pass{% else %}status-fail{% endif %}">
                        <i class="fas {% if session.overall_pass %}fa-check-circle{% else %}fa-times-circle{% endif %}"></i> 
                        {% if session.overall_pass %}PASSED{% else %}FAILED{% endif %}
                    </span>
                </div>
            </div>
        </div>

        <div class="session-content">
            <!-- Session Information -->
            <div class="info-grid">
                <div class="info-card">
                    <div class="card-header">
                        <div class="card-icon">
                            <i class="fas fa-cog"></i>
                        </div>
                        <div class="card-title">Equipment Details</div>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Model:</span>
                        <span class="info-value">{{ session.device_model|default:"N/A" }}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Serial Number:</span>
                        <span class="info-value">{{ session.device_serial|default:"N/A" }}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Manufacturer:</span>
                        <span class="info-value">{{ session.device_manufacturer|default:"N/A" }}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Description:</span>
                        <span class="info-value">{{ session.device_description|default:"N/A" }}</span>
                    </div>
                </div>

                <div class="info-card">
                    <div class="card-header">
                        <div class="card-icon">
                            <i class="fas fa-clipboard-list"></i>
                        </div>
                        <div class="card-title">Procedure Information</div>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Procedure:</span>
                        <span class="info-value">{{ session.procedure.name|default:"N/A" }}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Performed By:</span>
                        <span class="info-value">{{ session.performed_by.get_full_name|default:session.performed_by.username }}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Duration:</span>
                        <span class="info-value">
                            {% if session.duration %}
                                {{ session.duration }}
                            {% else %}
                                N/A
                            {% endif %}
                        </span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Timestamp:</span>
                        <span class="info-value">{{ session.timestamp|date:"Y-m-d H:i:s" }}</span>
                    </div>
                </div>
            </div>

            <!-- Environmental Conditions -->
            {% if session.actual_temperature or session.actual_humidity or session.actual_pressure %}
            <div class="environmental-conditions">
                <div class="card-header">
                    <div class="card-icon">
                        <i class="fas fa-thermometer-half"></i>
                    </div>
                    <div class="card-title">Environmental Conditions</div>
                </div>
                <div class="env-grid">
                    {% if session.actual_temperature %}
                    <div class="env-item">
                        <div class="env-icon">
                            <i class="fas fa-thermometer-half"></i>
                        </div>
                        <div class="env-value">{{ session.actual_temperature }}°C</div>
                        <div class="env-label">Temperature</div>
                    </div>
                    {% endif %}
                    {% if session.actual_humidity %}
                    <div class="env-item">
                        <div class="env-icon">
                            <i class="fas fa-tint"></i>
                        </div>
                        <div class="env-value">{{ session.actual_humidity }}%</div>
                        <div class="env-label">Humidity</div>
                    </div>
                    {% endif %}
                    {% if session.actual_pressure %}
                    <div class="env-item">
                        <div class="env-icon">
                            <i class="fas fa-compress-arrows-alt"></i>
                        </div>
                        <div class="env-value">{{ session.actual_pressure }} kPa</div>
                        <div class="env-label">Pressure</div>
                    </div>
                    {% endif %}
                </div>
            </div>
            {% endif %}

            <!-- Statistics Overview -->
            <div class="stats-overview">
                {% with total_readings=session.readings.count %}
                {% with passed_readings=session.readings.all|length %}
                {% with failed_count=0 %}
                <div class="stat-card">
                    <div class="stat-value">{{ total_readings }}</div>
                    <div class="stat-label">Total Readings</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">
                        {% for reading in session.readings.all %}
                            {% if reading.passes_tolerance %}{{ forloop.counter }}{% endif %}
                        {% empty %}0{% endfor %}
                    </div>
                    <div class="stat-label">Passed</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">
                        {% for reading in session.readings.all %}
                            {% if not reading.passes_tolerance %}{{ forloop.counter }}{% endif %}
                        {% empty %}0{% endfor %}
                    </div>
                    <div class="stat-label">Failed</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">
                        {% if total_readings > 0 %}
                            {% for reading in session.readings.all %}{% if reading.passes_tolerance %}{% add forloop.counter %}{% endif %}{% endfor %}
                            {% with passed_count=session.readings.all|length %}
                                {{ passed_count|percentage:total_readings }}%
                            {% endwith %}
                        {% else %}
                            0%
                        {% endif %}
                    </div>
                    <div class="stat-label">Pass Rate</div>
                </div>
                {% endwith %}
                {% endwith %}
                {% endwith %}
            </div>

            <!-- Calibration Results -->
            <div class="section">
                <div class="section-header">
                    <div class="section-icon">
                        <i class="fas fa-chart-line"></i>
                    </div>
                    <div class="section-title">Calibration Results</div>
                </div>

                <div class="readings-container">
                    {% for parameter_key, readings in readings_by_parameter.items %}
                    <div class="parameter-group">
                        <div class="parameter-header">
                            <div class="parameter-name">
                                {% for reading in readings %}
                                    {% if forloop.first %}
                                        {{ reading.parameter.name }}
                                        {% if reading.sub_parameter %}
                                            - {{ reading.sub_parameter.name }}
                                        {% endif %}
                                    {% endif %}
                                {% endfor %}
                            </div>
                            <div class="parameter-unit">
                                {% for reading in readings %}
                                    {% if forloop.first %}
                                        {% if reading.sub_parameter %}
                                            {{ reading.sub_parameter.unit|default:reading.parameter.unit }}
                                        {% else %}
                                            {{ reading.parameter.unit }}
                                        {% endif %}
                                    {% endif %}
                                {% endfor %}
                            </div>
                        </div>
                        <table class="readings-table">
                            <thead>
                                <tr>
                                    <th>Set Value</th>
                                    {% with first_reading=readings.0 %}
                                        {% if first_reading.parameter.num_readings %}
                                            {% for i in "12345"|slice:":"|slice:first_reading.parameter.num_readings %}
                                                <th>Reading {{ forloop.counter }}</th>
                                            {% endfor %}
                                        {% else %}
                                            {% for i in "12345"|make_list %}
                                                <th>Reading {{ forloop.counter }}</th>
                                            {% endfor %}
                                        {% endif %}
                                    {% endwith %}
                                    <th>Mean</th>
                                    <th>Error</th>
                                    <th>Uncertainty</th>
                                    <th>Pass/Fail</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for reading in readings %}
                                <tr>
                                    <td>{{ reading.set_value.value }}</td>
                                    <td>{{ reading|get_reading:1 }}</td>
                                    <td>{{ reading|get_reading:2 }}</td>
                                    <td>{{ reading|get_reading:3 }}</td>
                                    <td>{{ reading|get_reading:4 }}</td>
                                    <td>{{ reading|get_reading:5 }}</td>
                                    <td>{{ reading.mean|format_uncertainty|default:"-" }}</td>
                                    <td>{{ reading.error|format_uncertainty|default:"-" }}</td>
                                    <td>{{ reading.expanded_uncertainty|format_uncertainty|default:"-" }}</td>
                                    <td>
                                        <span class="{{ reading.passes_tolerance|status_class }}">
                                            <i class="fas {% if reading.passes_tolerance %}fa-check{% else %}fa-times{% endif %}"></i>
                                            {% if reading.passes_tolerance %}PASS{% else %}FAIL{% endif %}
                                        </span>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <!-- Analytics Charts -->
            <div class="analytics-grid">
                <div class="chart-container">
                    <div class="chart-title">
                        <i class="fas fa-chart-line"></i>
                        Error Analysis
                    </div>
                    <div class="chart-wrapper">
                        <canvas id="errorChart"></canvas>
                    </div>
                </div>

                <div class="chart-container">
                    <div class="chart-title">
                        <i class="fas fa-chart-bar"></i>
                        Uncertainty Budget
                    </div>
                    <div class="chart-wrapper">
                        <canvas id="uncertaintyChart"></canvas>
                    </div>
                </div>
            </div>

            {% if linearity_analysis %}
            <div class="chart-container">
                <div class="chart-title">
                    <i class="fas fa-chart-area"></i>
                    Linearity Analysis
                </div>
                <div class="chart-wrapper">
                    <canvas id="linearityChart"></canvas>
                </div>
            </div>
            {% endif %}

            <!-- Notes Section -->
            {% if session.notes %}
            <div class="section">
                <div class="section-header">
                    <div class="section-icon">
                        <i class="fas fa-sticky-note"></i>
                    </div>
                    <div class="section-title">Notes</div>
                </div>
                <div class="info-card">
                    <p>{{ session.notes|linebreaks }}</p>
                </div>
            </div>
            {% endif %}
        </div>

        <!-- Action Buttons -->
        <div class="actions-bar">
            <a href="{% url 'calibration:generate_certificate' session.pk %}" class="action-btn btn-primary">
                <i class="fas fa-certificate"></i>
                Generate Certificate
            </a>
            <a href="{% url 'calibration:generate_certificate' session.pk %}?format=pdf" class="action-btn btn-success">
                <i class="fas fa-file-pdf"></i>
                Download PDF
            </a>
            <a href="{% url 'calibration:uncertainty_budget' session.pk %}" class="action-btn btn-info">
                <i class="fas fa-calculator"></i>
                Uncertainty Budget
            </a>
            {% if session.device_serial %}
            <a href="{% url 'calibration:drift_analysis' session.pk %}" class="action-btn btn-warning">
                <i class="fas fa-trending-up"></i>
                Drift Analysis
            </a>
            {% endif %}
        </div>
    </div>
</div>

<script>
document.addEventListener('DOMContentLoaded', function() {
    // Error Analysis Chart
    const errorCtx = document.getElementById('errorChart').getContext('2d');
    const errorChart = new Chart(errorCtx, {
        type: 'line',
        data: {
            labels: [
                {% for parameter_key, readings in readings_by_parameter.items %}
                    {% for reading in readings %}
                        '{{ reading.set_value.value }}',
                    {% endfor %}
                {% endfor %}
            ],
            datasets: [{
                label: 'Measurement Error',
                data: [
                    {% for parameter_key, readings in readings_by_parameter.items %}
                        {% for reading in readings %}
                            {% if reading.error %}{{ reading.error }}{% else %}0{% endif %},
                        {% endfor %}
                    {% endfor %}
                ],
                borderColor: 'rgb(75, 192, 192)',
                backgroundColor: 'rgba(75, 192, 192, 0.2)',
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: {
                    display: true,
                    text: 'Measurement Error vs Set Value'
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Error'
                    }
                },
                x: {
                    title: {
                        display: true,
                        text: 'Set Value'
                    }
                }
            }
        }
    });

    // Uncertainty Budget Chart
    const uncertaintyCtx = document.getElementById('uncertaintyChart').getContext('2d');
    const uncertaintyChart = new Chart(uncertaintyCtx, {
        type: 'bar',
        data: {
            labels: ['Type A', 'Type B', 'Reference', 'Combined', 'Expanded'],
            datasets: [{
                label: 'Uncertainty Components',
                data: [
                    {% with first_reading=session.readings.first %}
                        {% if first_reading.type_a_uncertainty %}{{ first_reading.type_a_uncertainty }}{% else %}0{% endif %},
                        {% if first_reading.type_b_uncertainty %}{{ first_reading.type_b_uncertainty }}{% else %}0{% endif %},
                        {% if first_reading.reference_uncertainty_component %}{{ first_reading.reference_uncertainty_component }}{% else %}0{% endif %},
                        {% if first_reading.combined_uncertainty %}{{ first_reading.combined_uncertainty }}{% else %}0{% endif %},
                        {% if first_reading.expanded_uncertainty %}{{ first_reading.expanded_uncertainty }}{% else %}0{% endif %}
                    {% endwith %}
                ],
                backgroundColor: [
                    'rgba(255, 99, 132, 0.7)',
                    'rgba(54, 162, 235, 0.7)',
                    'rgba(255, 205, 86, 0.7)',
                    'rgba(75, 192, 192, 0.7)',
                    'rgba(153, 102, 255, 0.7)'
                ],
                borderColor: [
                    'rgba(255, 99, 132, 1)',
                    'rgba(54, 162, 235, 1)',
                    'rgba(255, 205, 86, 1)',
                    'rgba(75, 192, 192, 1)',
                    'rgba(153, 102, 255, 1)'
                ],
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: {
                    display: true,
                    text: 'Uncertainty Budget Components'
                },
                legend: {
                    display: false
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Uncertainty Value'
                    }
                }
            }
        }
    });

    {% if linearity_analysis %}
    // Linearity Analysis Chart
    const linearityCtx = document.getElementById('linearityChart').getContext('2d');
    
    // Prepare linearity data
    const setValues = [];
    const measuredValues = [];
    const linearFitValues = [];
    
    {% for parameter_key, readings in readings_by_parameter.items %}
        {% for reading in readings %}
            setValues.push({{ reading.set_value.value }});
            measuredValues.push({% if reading.mean %}{{ reading.mean }}{% else %}null{% endif %});
            {% get_linearity_data linearity_analysis parameter_key as linearity_info %}
            {% if linearity_info.slope and linearity_info.intercept %}
                linearFitValues.push({{ linearity_info.slope }} * {{ reading.set_value.value }} + {{ linearity_info.intercept }});
            {% else %}
                linearFitValues.push(null);
            {% endif %}
        {% endfor %}
    {% endfor %}

    const linearityChart = new Chart(linearityCtx, {
        type: 'scatter',
        data: {
            datasets: [{
                label: 'Measured Values',
                data: setValues.map((x, i) => ({x: x, y: measuredValues[i]})),
                backgroundColor: 'rgba(75, 192, 192, 0.6)',
                borderColor: 'rgba(75, 192, 192, 1)',
                pointRadius: 6,
                pointHoverRadius: 8
            }, {
                label: 'Linear Fit',
                data: setValues.map((x, i) => ({x: x, y: linearFitValues[i]})),
                type: 'line',
                borderColor: 'rgba(255, 99, 132, 1)',
                backgroundColor: 'rgba(255, 99, 132, 0.1)',
                borderWidth: 2,
                pointRadius: 0,
                fill: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: {
                    display: true,
                    text: 'Linearity Analysis - Measured vs Set Values'
                }
            },
            scales: {
                x: {
                    type: 'linear',
                    position: 'bottom',
                    title: {
                        display: true,
                        text: 'Set Value'
                    }
                },
                y: {
                    title: {
                        display: true,
                        text: 'Measured Value'
                    }
                }
            }
        }
    });
    {% endif %}

    // Add smooth animations to cards
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
            }
        });
    }, observerOptions);

    // Observe all cards for animation
    document.querySelectorAll('.info-card, .parameter-group, .chart-container').forEach(card => {
        card.style.opacity = '0';
        card.style.transform = 'translateY(20px)';
        card.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
        observer.observe(card);
    });

    // Add interactive tooltips for readings table
    document.querySelectorAll('.readings-table tr').forEach(row => {
        row.addEventListener('mouseenter', function() {
            this.style.transform = 'scale(1.02)';
            this.style.boxShadow = '0 4px 15px rgba(52, 152, 219, 0.2)';
        });
        
        row.addEventListener('mouseleave', function() {
            this.style.transform = 'scale(1)';
            this.style.boxShadow = 'none';
        });
    });

    // Print functionality
    function printCertificate() {
        window.print();
    }

    // Add print styles
    const printStyles = `
        @media print {
            .actions-bar { display: none !important; }
            .session-container { background: white !important; padding: 0 !important; }
            .session-card { box-shadow: none !important; margin: 0 !important; }
            .chart-wrapper { height: 300px !important; }
            body { background: white !important; }
        }
    `;
    
    const styleSheet = document.createElement('style');
    styleSheet.textContent = printStyles;
    document.head.appendChild(styleSheet);
});

// Template filter for dictionary lookup (since Django doesn't have built-in)
// This would need to be implemented as a custom Django template filter
</script>
{% endblock %}