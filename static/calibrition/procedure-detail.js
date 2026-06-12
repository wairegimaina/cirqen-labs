const { procedureData } = require("./Calibration-Procedure");

// Global variables
let isEditMode = false;
const procedureId = {{ procedure.id }};
    name: "{{ procedure.name|escapejs }}",
    description: "{{ procedure.description|escapejs }}",
    temperature: {{ procedure.temperature|default:"null" }},
    humidity: {{ procedure.humidity|default:"null" }},
    pressure: {{ procedure.pressure|default:"null" }}
};

// Initialize page
document.addEventListener('DOMContentLoaded', function() {
    initializeEventListeners();
    loadStandards();
    initializeTooltips();
    
    // Hide edit form initially
    document.getElementById('edit-form').classList.remove('show');
});

// Initialize all event listeners
function initializeEventListeners() {
    // Tab switching
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', function() {
            switchTab(this.getAttribute('data-tab'));
        });
    });
    
    // Edit form submission
    document.getElementById('procedure-edit-form').addEventListener('submit', saveProcedure);
    
    // Edit mode toggle
    document.querySelector('[onclick="toggleEditMode()"]').addEventListener('click', toggleEditMode);
    
    // Add parameter button
    document.querySelector('[onclick="addNewParameter()"]').addEventListener('click', addNewParameter);
    
    // Cancel edit button
    document.querySelector('[onclick="cancelEdit()"]').addEventListener('click', cancelEdit);
}

// Tab switching functionality
function switchTab(tabName) {
    // Hide all tab contents
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });

    // Remove active class from all tabs
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.remove('active');
    });

    // Show selected tab content
    document.getElementById(tabName + '-tab').classList.add('active');

    // Add active class to selected tab
    event.currentTarget.classList.add('active');

    // Special handling for standards tab
    if (tabName === 'standards') {
        loadStandards();
    }
}

// Toggle parameter visibility
function toggleParameter(parameterId) {
    const content = document.getElementById(parameterId);
    const chevron = document.getElementById('chevron-' + parameterId);

    if (content.classList.contains('show')) {
        content.classList.remove('show');
        chevron.style.transform = 'rotate(0deg)';
    } else {
        content.classList.add('show');
        chevron.style.transform = 'rotate(180deg)';
    }
}

// Toggle parameter edit section
function toggleEditParameter(paramId) {
    const content = document.getElementById(paramId);
    const chevron = document.getElementById('edit-chevron-' + paramId);
    
    if (content.classList.contains('show')) {
        content.classList.remove('show');
        chevron.style.transform = 'rotate(0deg)';
    } else {
        content.classList.add('show');
        chevron.style.transform = 'rotate(180deg)';
    }
}

// Toggle sub-parameter edit section
function toggleEditSubParameter(subParamId) {
    const content = document.getElementById(subParamId);
    const chevron = document.getElementById('edit-chevron-' + subParamId);
    
    if (content.classList.contains('show')) {
        content.classList.remove('show');
        chevron.style.transform = 'rotate(0deg)';
    } else {
        content.classList.add('show');
        chevron.style.transform = 'rotate(180deg)';
    }
}

// Add new parameter
function addNewParameter() {
    const paramsList = document.getElementById('edit-parameters-list');
    const newParamId = 'new-' + Date.now();
    
    const paramHtml = `
        <div class="parameter-form" data-param-id="${newParamId}">
            <div class="parameter-header" onclick="toggleEditParameter('edit-param-${newParamId}')">
                <h4>
                    <i class="fas fa-ruler"></i>
                    New Parameter
                </h4>
                <i class="fas fa-chevron-down" id="edit-chevron-edit-param-${newParamId}"></i>
            </div>
            
            <div class="parameter-content" id="edit-param-${newParamId}">
                <div class="parameter-details">
                    <div class="form-group">
                        <label class="form-label">Parameter Name</label>
                        <input type="text" class="form-control" value="New Parameter" data-field="name">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Unit</label>
                        <input type="text" class="form-control" value="" data-field="unit">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Number of Readings</label>
                        <input type="number" class="form-control" value="5" min="1" data-field="num_readings">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Standard Reference</label>
                        <input type="text" class="form-control" value="" data-field="standard_reference">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Reference Uncertainty</label>
                        <input type="number" step="0.000001" class="form-control" value="0.001" data-field="reference_uncertainty">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Coverage Factor</label>
                        <input type="number" step="0.1" class="form-control" value="2.0" min="1" data-field="coverage_factor">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Tolerance (±)</label>
                        <input type="number" step="0.000001" class="form-control" value="1.0" min="0" data-field="tolerance">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Order</label>
                        <input type="number" class="form-control" value="0" min="0" data-field="order">
                    </div>
                </div>

                <!-- Set values for main parameter -->
                <div class="set-values">
                    <h5>
                        <i class="fas fa-bullseye"></i>
                        Set Values
                    </h5>
                    <button type="button" class="btn btn-primary btn-sm" onclick="addSetValue(this, 'param', '${newParamId}')">
                        <i class="fas fa-plus"></i> Add Set Value
                    </button>
                </div>
            </div>
        </div>
    `;
    
    paramsList.insertAdjacentHTML('beforeend', paramHtml);
}

// Add set value
function addSetValue(button, type, parentId) {
    const container = button.closest('.set-values');
    const newId = 'new-set-' + Date.now();
    
    const setValueHtml = `
        <div class="set-value-form" data-setvalue-id="${newId}">
            <div class="form-group">
                <label class="form-label">Value</label>
                <input type="number" step="0.000001" class="form-control" value="0" data-field="value">
            </div>
            <div class="form-group">
                <label class="form-label">Order</label>
                <input type="number" class="form-control" value="0" min="0" data-field="order">
            </div>
            <button type="button" class="btn btn-danger btn-sm" onclick="deleteSetValue(this)">
                <i class="fas fa-trash"></i> Delete
            </button>
        </div>
    `;
    
    button.insertAdjacentHTML('beforebegin', setValueHtml);
}

// Delete set value
function deleteSetValue(button) {
    if (confirm('Are you sure you want to delete this set value?')) {
        const setValueForm = button.closest('.set-value-form');
        setValueForm.remove();
    }
}

// Save procedure changes
function saveProcedure(event) {
    if (event) event.preventDefault();
    
    // Collect basic procedure data
    const formData = {
        name: document.getElementById('edit-name').value,
        description: document.getElementById('edit-description').value,
        temperature: document.getElementById('edit-temperature').value || null,
        humidity: document.getElementById('edit-humidity').value || null,
        pressure: document.getElementById('edit-pressure').value || null,
        parameters: []
    };
    
    // Collect parameters data
    const parameterForms = document.querySelectorAll('.parameter-form');
    parameterForms.forEach(paramForm => {
        const paramId = paramForm.dataset.paramId;
        const isNew = paramId.startsWith('new-');
        
        const paramData = {
            id: isNew ? null : paramId,
            name: paramForm.querySelector('[data-field="name"]').value,
            unit: paramForm.querySelector('[data-field="unit"]').value,
            num_readings: parseInt(paramForm.querySelector('[data-field="num_readings"]').value),
            standard_reference: paramForm.querySelector('[data-field="standard_reference"]').value,
            reference_uncertainty: parseFloat(paramForm.querySelector('[data-field="reference_uncertainty"]').value),
            coverage_factor: parseFloat(paramForm.querySelector('[data-field="coverage_factor"]').value),
            tolerance: parseFloat(paramForm.querySelector('[data-field="tolerance"]').value),
            order: parseInt(paramForm.querySelector('[data-field="order"]').value),
            set_values: [],
            sub_parameters: []
        };
        
        // Collect set values for main parameter
        const setValueForms = paramForm.querySelectorAll('.set-values .set-value-form');
        setValueForms.forEach(setForm => {
            const setId = setForm.dataset.setvalueId;
            const isNewSet = setId.startsWith('new-set-');
            
            paramData.set_values.push({
                id: isNewSet ? null : setId,
                value: parseFloat(setForm.querySelector('[data-field="value"]').value),
                order: parseInt(setForm.querySelector('[data-field="order"]').value),
                sub_parameter: null
            });
        });
        
        // Collect sub-parameters
        const subParamForms = paramForm.querySelectorAll('.sub-parameter-form');
        subParamForms.forEach(subForm => {
            const subParamId = subForm.dataset.subparamId;
            const isNewSub = subParamId.startsWith('new-sub-');
            
            const subParamData = {
                id: isNewSub ? null : subParamId,
                name: subForm.querySelector('[data-field="name"]').value,
                tolerance: parseFloat(subForm.querySelector('[data-field="tolerance"]').value),
                order: parseInt(subForm.querySelector('[data-field="order"]').value),
                set_values: []
            };
            
            // Collect set values for sub-parameter
            const subSetValueForms = subForm.querySelectorAll('.set-value-form');
            subSetValueForms.forEach(setForm => {
                const setId = setForm.dataset.setvalueId;
                const isNewSet = setId.startsWith('new-set-');
                
                subParamData.set_values.push({
                    id: isNewSet ? null : setId,
                    value: parseFloat(setForm.querySelector('[data-field="value"]').value),
                    order: parseInt(setForm.querySelector('[data-field="order"]').value)
                });
            });
            
            paramData.sub_parameters.push(subParamData);
        });
        
        formData.parameters.push(paramData);
    });
    
    // Show loading state
    const form = document.getElementById('procedure-edit-form');
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
    
    // Send data to server
    fetch(`/calibration/procedures/${procedureId}/edit/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken(),
            'X-Requested-With': 'XMLHttpRequest'
        },
        body: JSON.stringify(formData)
    })
    .then(response => {
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }
        return response.json();
    })
    .then(data => {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="fas fa-save"></i> Save Changes';
        
        if (data.success) {
            showAlert('Procedure updated successfully!', 'success');
            // Refresh the page to show changes
            setTimeout(() => location.reload(), 1500);
        } else {
            let errorMessage = 'Please correct the following errors:';
            if (data.errors) {
                errorMessage += '<ul>';
                Object.keys(data.errors).forEach(field => {
                    errorMessage += `<li>${field}: ${data.errors[field][0].message}</li>`;
                });
                errorMessage += '</ul>';
            }
            showAlert(errorMessage, 'error');
        }
    })
    .catch(error => {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="fas fa-save"></i> Save Changes';
        showAlert('An error occurred while updating the procedure. Please try again.', 'error');
        console.error('Error:', error);
    });
}

// Cancel edit mode
function cancelEdit() {
    const editForm = document.getElementById('edit-form');
    const editBtn = document.getElementById('edit-btn-text');

    editForm.classList.remove('show');
    editBtn.textContent = 'Edit Procedure';
    isEditMode = false;
}

// Load standards information
function loadStandards() {
    const standardsList = document.getElementById('standards-list');
    standardsList.innerHTML = '<div class="loading-spinner"><i class="fas fa-spinner fa-spin"></i> Loading standards...</div>';

    fetch(`/calibration/api/procedure/${procedureId}/`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.json();
        })
        .then(data => {
            if (data.success && data.procedure && data.procedure.parameters) {
                const standards = new Map();

                // Collect unique standards from parameters
                data.procedure.parameters.forEach(param => {
                    if (param.standard_reference) {
                        standards.set(param.standard_reference, {
                            reference: param.standard_reference,
                            parameters: standards.has(param.standard_reference)
                                ? [...standards.get(param.standard_reference).parameters, param.name]
                                : [param.name],
                            uncertainty: param.reference_uncertainty
                        });
                    }
                });

                if (standards.size > 0) {
                    standardsList.innerHTML = '';
                    standards.forEach((standard, reference) => {
                        const standardCard = createStandardCard(standard);
                        standardsList.appendChild(standardCard);
                    });
                } else {
                    standardsList.innerHTML = `
                        <div class="no-standards">
                            <i class="fas fa-exclamation-circle"></i>
                            <h3>No Standards Referenced</h3>
                            <p>This procedure doesn't reference any calibration standards.</p>
                        </div>
                    `;
                }
            } else {
                throw new Error('Failed to load procedure data');
            }
        })
        .catch(error => {
            standardsList.innerHTML = `
                <div class="error-message">
                    <i class="fas fa-exclamation-triangle"></i>
                    <h3>Error Loading Standards</h3>
                    <p>Unable to load standards information. Please try refreshing the page.</p>
                </div>
            `;
            console.error('Error loading standards:', error);
        });
}

// Create standard card HTML element
function createStandardCard(standard) {
    const card = document.createElement('div');
    card.className = 'info-card';
    card.style.borderLeft = '4px solid #f39c12';

    card.innerHTML = `
        <h3>
            <i class="fas fa-certificate"></i>
            ${standard.reference}
        </h3>
        <div class="standard-details">
            <div class="detail-item">
                <span class="detail-label">Reference Uncertainty</span>
                <span class="detail-value">${standard.uncertainty}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label">Used for Parameters</span>
                <div class="parameter-tags">
                    ${standard.parameters.map(param =>
                        `<span class="parameter-tag">${param}</span>`
                    ).join('')}
                </div>
            </div>
        </div>
    `;

    return card;
}

// Duplicate procedure
function duplicateProcedure() {
    if (confirm('Are you sure you want to create a copy of this procedure?')) {
        const newName = prompt('Enter name for the duplicated procedure:', procedureData.name + ' (Copy)');
        if (newName) {
            fetch(`/calibration/procedures/${procedureId}/duplicate/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ name: newName })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success && data.new_procedure_id) {
                    showAlert('Procedure duplicated successfully!', 'success');
                    window.location.href = `/calibration/procedures/${data.new_procedure_id}/`;
                } else {
                    showAlert('Failed to duplicate procedure: ' + (data.error || 'Unknown error'), 'error');
                }
            })
            .catch(error => {
                showAlert('Error duplicating procedure: ' + error.message, 'error');
                console.error('Error:', error);
            });
        }
    }
}

// Delete procedure
function deleteProcedure() {
    if (confirm(`Are you sure you want to delete "${procedureData.name}"? This action cannot be undone.`)) {
        fetch(`/calibration/procedures/${procedureId}/delete/`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showAlert('Procedure deleted successfully', 'success');
                setTimeout(() => {
                    window.location.href = '/calibration/procedures/';
                }, 2000);
            } else {
                showAlert('Error deleting procedure: ' + (data.error || 'Unknown error'), 'error');
            }
        })
        .catch(error => {
            showAlert('Network error occurred while deleting procedure', 'error');
            console.error('Error:', error);
        });
    }
}

// Show alert message
function showAlert(message, type) {
    const alertContainer = document.getElementById('alert-container');
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.style.display = 'flex';
    alertDiv.style.alignItems = 'center';
    alertDiv.style.gap = '10px';

    const icon = type === 'success' ? 'check-circle' : 
                 type === 'error' ? 'exclamation-circle' : 
                 'exclamation-triangle';
    
    alertDiv.innerHTML = `
        <i class="fas fa-${icon}"></i>
        <span>${message}</span>
    `;

    alertContainer.appendChild(alertDiv);

    // Auto remove after 5 seconds
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.style.opacity = '0';
            alertDiv.style.transform = 'translateY(-10px)';
            setTimeout(() => alertDiv.remove(), 300);
        }
    }, 5000);
}

// Get CSRF token
function getCsrfToken() {
    const cookieValue = document.cookie
        .split('; ')
        .find(row => row.startsWith('csrftoken='))
        ?.split('=')[1];
    return cookieValue || '';
}

// Initialize tooltips
function initializeTooltips() {
    // Add hover effects to parameter cards
    document.querySelectorAll('.parameter-card').forEach(card => {
        card.addEventListener('mouseenter', function() {
            this.style.transform = 'translateY(-2px)';
            this.style.boxShadow = '0 4px 8px rgba(0,0,0,0.1)';
        });

        card.addEventListener('mouseleave', function() {
            this.style.transform = 'translateY(0)';
            this.style.boxShadow = '0 2px 4px rgba(0,0,0,0.05)';
        });
    });
}

// Toggle edit mode
function toggleEditMode() {
    const editForm = document.getElementById('edit-form');
    const editBtn = document.getElementById('edit-btn-text');
    
    if (isEditMode) {
        editForm.classList.remove('show');
        editBtn.textContent = 'Edit Procedure';
    } else {
        editForm.classList.add('show');
        editBtn.textContent = 'Cancel Edit';
        // Scroll to edit form
        editForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    
    isEditMode = !isEditMode;
}

// Keyboard shortcuts
document.addEventListener('keydown', function(event) {
    // Ctrl + E to toggle edit mode
    if (event.ctrlKey && event.key === 'e') {
        event.preventDefault();
        toggleEditMode();
    }

    // Escape to cancel edit mode
    if (event.key === 'Escape' && isEditMode) {
        cancelEdit();
    }

    // Ctrl + S to save (when in edit mode)
    if (event.ctrlKey && event.key === 's' && isEditMode) {
        event.preventDefault();
        document.getElementById('procedure-edit-form').dispatchEvent(new Event('submit'));
    }
});
