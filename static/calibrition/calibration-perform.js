// Debug mode - set to true to see detailed error information
const DEBUG_MODE = true;

// Store parameter requirements for validation
let parameterRequirements = {};

function logDebug(message) {
  if (DEBUG_MODE) {
    console.log('[Calibration Debug]:', message);
  }
}

// NEW: Check if all required fields are filled and valid
function checkAllRequiredFields() {
  const submitBtn = document.getElementById('submitBtn');
  let allValid = true;
  const errors = [];

  // 1. Check procedure selection
  const procedureSelect = document.getElementById('procedure');
  if (!procedureSelect.value) {
    allValid = false;
    errors.push('Procedure not selected');
  }

  // 2. Check environmental conditions
  const tempInput = document.getElementById('actual_temperature');
  const humidityInput = document.getElementById('actual_humidity');
  const pressureInput = document.getElementById('actual_pressure');

  if (!tempInput.value || isNaN(tempInput.value)) {
    allValid = false;
    errors.push('Temperature missing or invalid');
  }
  if (!humidityInput.value || isNaN(humidityInput.value)) {
    allValid = false;
    errors.push('Humidity missing or invalid');
  }
  if (!pressureInput.value || isNaN(pressureInput.value)) {
    allValid = false;
    errors.push('Pressure missing or invalid');
  }

  // 3. Check resolution inputs (all must be filled)
  const resolutionInputs = document.querySelectorAll('input[name^="resolution_"]');
  resolutionInputs.forEach(input => {
    if (!input.value.trim() || isNaN(parseFloat(input.value)) || parseFloat(input.value) <= 0) {
      allValid = false;
      const paramId = input.getAttribute('data-param-id');
      const paramName = parameterRequirements[paramId]
        ? parameterRequirements[paramId].name
        : `Parameter ${paramId}`;
      errors.push(`Resolution for ${paramName} missing or invalid`);
    }
  });

  // 4. Check required readings for each parameter - STRICT VALIDATION
  for (const [paramId, paramData] of Object.entries(parameterRequirements)) {
    const requiredReadings = paramData.required_readings || paramData.num_readings; // ✅ USE CORRECT FIELD
    const paramName = paramData.name;

    logDebug(`Checking parameter ${paramName}: required_readings=${requiredReadings}`);

    // Get all rows for this parameter
    const rows = document.querySelectorAll(`input[data-param="${paramId}"]`);
    const rowGroups = {};

    // Group inputs by row (set value)
    rows.forEach(input => {
      const rowKey = input.name.split('_').slice(0, -1).join('_');
      if (!rowGroups[rowKey]) {
        rowGroups[rowKey] = [];
      }
      rowGroups[rowKey].push(input);
    });

    // ENFORCE ALL: Every row, every required reading must be filled
    Object.entries(rowGroups).forEach(([rowKey, inputs]) => {
      let validFilledCount = 0;
      let invalidCount = 0;

      inputs.forEach((input, index) => {
        if (index >= requiredReadings) return; // only check required columns
        const value = input.value.trim();
        if (value === '') {
          invalidCount++; // treat empty as missing
        } else if (!isNaN(parseFloat(value))) {
          validFilledCount++;
        } else {
          invalidCount++;
        }
      });

      logDebug(
        `Row check for ${paramName}: ${validFilledCount} valid out of ${requiredReadings} required`,
      );

      if (invalidCount > 0) {
        allValid = false;
        const setValueCell = inputs[0].closest('tr')?.querySelector('.set-value-cell');
        const setValue = setValueCell ? setValueCell.textContent.trim() : 'Row';
        errors.push(
          `${paramName} (${setValue}): needs ${requiredReadings} readings, only ${validFilledCount} valid`,
        );
        logDebug(`BLOCKING: ${paramName} row — ${invalidCount} missing/invalid`);
      }
    });
  }

  // 5. Ensure at least one parameter section exists
  if (Object.keys(parameterRequirements).length === 0) {
    allValid = false;
    errors.push('No procedure parameters loaded yet');
  }

  // Update button state
  if (allValid) {
    submitBtn.disabled = false;
    submitBtn.style.opacity = '1';
    submitBtn.style.cursor = 'pointer';
    submitBtn.title = 'Ready to submit';
    logDebug('All fields valid - button enabled');
  } else {
    submitBtn.disabled = true;
    submitBtn.style.opacity = '0.5';
    submitBtn.style.cursor = 'not-allowed';
    submitBtn.title = 'Please fill all required fields: ' + errors.join(', ');
    logDebug('Missing fields: ' + errors.join(', '));
  }

  return allValid;
}

// Prefill environmental conditions with standard lab values
function prefillEnvironmentalConditions() {
  const tempInput = document.getElementById('actual_temperature');
  const humidityInput = document.getElementById('actual_humidity');
  const pressureInput = document.getElementById('actual_pressure');
  const indicator = document.getElementById('prefilledIndicator');

  // Standard laboratory conditions
  const standardTemp = 23.0;
  const standardHumidity = 50.0;
  const standardPressure = 101.325;

  if (!tempInput.value) {
    tempInput.value = standardTemp;
  }
  if (!humidityInput.value) {
    humidityInput.value = standardHumidity;
  }
  if (!pressureInput.value) {
    pressureInput.value = standardPressure;
  }

  if (tempInput.value || humidityInput.value || pressureInput.value) {
    indicator.style.display = 'block';
  }
}

// Auto-load procedure if equipment is pre-selected
document.addEventListener('DOMContentLoaded', function () {
  // Force button disabled until all readings pass
  const submitBtn = document.getElementById('submitBtn');
  submitBtn.disabled = true;

  // Prefill environmental conditions on page load
  prefillEnvironmentalConditions();

  // Initial button state check
  checkAllRequiredFields();
});

// Show loading overlay with step-progress design
function showLoading(message = 'Processing calibration...') {
  const overlay = document.getElementById('loadingOverlay');
  const loadingContent = document.getElementById('loadingContent');

  loadingContent.innerHTML = `
    <div class="loading-ring">
      <svg viewBox="0 0 64 64">
        <circle class="ring-track" cx="32" cy="32" r="27"/>
        <circle class="ring-progress" cx="32" cy="32" r="27"/>
      </svg>
    </div>
    <div class="loading-title">Processing calibration</div>
    <div class="loading-text" id="loadingText">${escapeHTML(message)}</div>
    <div class="loading-steps">
      <div class="loading-step active" id="step-validate">
        <div class="step-dot"></div>
        <span>Validating inputs</span>
      </div>
      <div class="loading-step" id="step-prepare">
        <div class="step-dot"></div>
        <span>Preparing calibration data</span>
      </div>
      <div class="loading-step" id="step-submit">
        <div class="step-dot"></div>
        <span>Submitting to server</span>
      </div>
    </div>
  `;
  overlay.classList.add('active');
}

// Advance a loading step
function advanceStep(doneId, activeId) {
  const done = document.getElementById(doneId);
  const active = document.getElementById(activeId);
  if (done) {
    done.classList.remove('active');
    done.classList.add('done');
    done.querySelector('.step-dot').innerHTML = '<i class="fas fa-check"></i>';
  }
  if (active) active.classList.add('active');
}

// Show success message
function showSuccess(message = 'Calibration completed successfully!', details = '') {
  const loadingContent = document.getElementById('loadingContent');

  loadingContent.innerHTML = `
    <div class="loading-success-icon"><i class="fas fa-circle-check"></i></div>
    <div class="loading-success-title">${escapeHTML(message)}</div>
    ${details ? `<div class="loading-success-sub">${details}</div>` : ''}
  `;
}

// Hide loading overlay
function hideLoading() {
  const overlay = document.getElementById('loadingOverlay');
  overlay.classList.remove('active');
}

// Validate environmental conditions
function validateEnvironmentalConditions() {
  const tempInput = document.getElementById('actual_temperature');
  const humidityInput = document.getElementById('actual_humidity');
  const pressureInput = document.getElementById('actual_pressure');

  let isValid = true;

  if (!tempInput.value || isNaN(tempInput.value)) {
    tempInput.classList.add('validation-error');
    isValid = false;
  } else {
    tempInput.classList.remove('validation-error');
  }

  if (!humidityInput.value || isNaN(humidityInput.value)) {
    humidityInput.classList.add('validation-error');
    isValid = false;
  } else {
    humidityInput.classList.remove('validation-error');
  }

  if (!pressureInput.value || isNaN(pressureInput.value)) {
    pressureInput.classList.add('validation-error');
    isValid = false;
  } else {
    pressureInput.classList.remove('validation-error');
  }

  return isValid;
}

// Comprehensive validation of all inputs
function validateAllInputs() {
  const errors = [];
  const validationAlert = document.getElementById('validationAlert');
  const validationErrors = document.getElementById('validationErrors');

  // Clear previous highlights and errors
  document.querySelectorAll('.missing-required').forEach(el => {
    el.classList.remove('missing-required');
  });
  document.querySelectorAll('.has-errors').forEach(el => {
    el.classList.remove('has-errors');
  });
  document.querySelectorAll('.validation-error').forEach(el => {
    el.classList.remove('validation-error');
  });

  // Clear all error messages
  document.querySelectorAll('.error-message').forEach(el => {
    el.textContent = '';
  });

  // 1. Validate procedure selection
  const procedureSelect = document.getElementById('procedure');
  if (!procedureSelect.value) {
    errors.push('Please select a calibration procedure');
    procedureSelect.classList.add('validation-error');
  } else {
    procedureSelect.classList.remove('validation-error');
  }

  // 2. Validate environmental conditions
  if (!validateEnvironmentalConditions()) {
    errors.push('Please fill in all environmental conditions (Temperature, Humidity, Pressure)');
  }

  // 3. Validate resolution inputs
  const resolutionInputs = document.querySelectorAll('input[name^="resolution_"]');
  resolutionInputs.forEach(input => {
    if (!input.value.trim()) {
      const paramId = input.getAttribute('data-param-id');
      const paramName = parameterRequirements[paramId]
        ? parameterRequirements[paramId].name
        : `Parameter ${paramId}`;
      errors.push(`Please enter resolution for "${paramName}"`);
      input.classList.add('missing-required', 'validation-error');
    } else if (isNaN(parseFloat(input.value)) || parseFloat(input.value) <= 0) {
      const paramId = input.getAttribute('data-param-id');
      const paramName = parameterRequirements[paramId]
        ? parameterRequirements[paramId].name
        : `Parameter ${paramId}`;
      errors.push(`Resolution for "${paramName}" must be a positive number`);
      input.classList.add('validation-error');
    } else {
      input.classList.remove('missing-required', 'validation-error');
    }
  });

  // 4. Validate required readings - STRICT: Check EVERY row that has ANY data
  for (const [paramId, paramData] of Object.entries(parameterRequirements)) {
    const requiredReadings = paramData.required_readings || paramData.num_readings; // ✅ USE CORRECT FIELD
    const paramName = paramData.name;

    const rows = document.querySelectorAll(`input[data-param="${paramId}"]`);
    const rowGroups = {};

    rows.forEach(input => {
      const rowKey = input.name.split('_').slice(0, -1).join('_');
      if (!rowGroups[rowKey]) {
        rowGroups[rowKey] = [];
      }
      rowGroups[rowKey].push(input);
    });

    // ENFORCE ALL: every row, every required reading must be filled and valid
    Object.entries(rowGroups).forEach(([rowKey, inputs]) => {
      let validFilledCount = 0;
      const missingInputs = [];
      const invalidInputs = [];

      inputs.forEach((input, index) => {
        if (index >= requiredReadings) return; // only validate required columns
        const value = input.value.trim();
        const errorDiv = document.getElementById(`error_${input.name}`);
        if (errorDiv) errorDiv.textContent = '';

        if (value === '') {
          missingInputs.push(input);
        } else if (isNaN(parseFloat(value))) {
          invalidInputs.push(input);
          if (errorDiv) errorDiv.textContent = 'Invalid number';
        } else {
          validFilledCount++;
        }
      });

      const rowIncomplete = missingInputs.length > 0 || invalidInputs.length > 0;

      if (rowIncomplete) {
        const setValueCell = inputs[0].closest('tr')?.querySelector('.set-value-cell');
        const setValue = setValueCell ? setValueCell.textContent.trim() : 'Unknown';

        if (missingInputs.length > 0) {
          errors.push(
            `"${paramName}" (${setValue}): ${missingInputs.length} required reading(s) missing`,
          );
          missingInputs.forEach(input => {
            input.classList.add('missing-required', 'validation-error');
          });
        }

        if (invalidInputs.length > 0) {
          errors.push(
            `"${paramName}" (${setValue}): ${invalidInputs.length} reading(s) have invalid values`,
          );
          invalidInputs.forEach(input => {
            input.classList.add('validation-error');
          });
        }
      }
    });
  }

  // 5. Ensure parameters are loaded
  if (Object.keys(parameterRequirements).length === 0) {
    errors.push('Please select a procedure and wait for parameters to load');
  }

  // Display errors if any
  if (errors.length > 0) {
    validationErrors.innerHTML = errors.map(err => `<li>${escapeHTML(err)}</li>`).join('');
    validationAlert.classList.add('show');
    validationAlert.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return false;
  } else {
    validationAlert.classList.remove('show');
    return true;
  }
}

// Handle procedure selection to load parameters and set values
document.getElementById('procedure').addEventListener('change', function () {
  const procedureId = this.value;
  if (procedureId) {
    logDebug(`Procedure changed to: ${procedureId}`);
    loadProcedureDetails(procedureId);
  } else {
    document.getElementById('reading-fields').innerHTML = '';
    logDebug('Procedure cleared');
  }
  // Check button state after procedure change
  checkAllRequiredFields();
});

// Add event listeners to environmental conditions
document.getElementById('actual_temperature').addEventListener('input', checkAllRequiredFields);
document.getElementById('actual_humidity').addEventListener('input', checkAllRequiredFields);
document.getElementById('actual_pressure').addEventListener('input', checkAllRequiredFields);

// Fetch procedure details and generate tables
function loadProcedureDetails(procedureId) {
  const readingFields = document.getElementById('reading-fields');
  const loading = document.getElementById('loading');

  // Show skeleton loader
  readingFields.innerHTML = `
    <div class="skeleton-loader">
      <div class="skeleton-status">
        <div class="skeleton-spinner"></div>
        Fetching procedure details...
      </div>
      ${[1, 2].map(() => `
        <div class="skeleton-card">
          <div class="skeleton-header">
            <div class="skeleton-bar" style="width:35%;"></div>
            <div class="skeleton-bar" style="width:12%; margin-left:auto;"></div>
          </div>
          <div class="skeleton-body">
            <div class="skeleton-row">
              ${[2, 4, 3, 3, 3].map(w => `<div class="skeleton-cell" style="flex:${w};"></div>`).join('')}
            </div>
            <div class="skeleton-row">
              ${[2, 4, 3, 3, 3].map(w => `<div class="skeleton-cell" style="flex:${w};"></div>`).join('')}
            </div>
            <div class="skeleton-row">
              ${[2, 4, 3, 3, 3].map(w => `<div class="skeleton-cell" style="flex:${w};"></div>`).join('')}
            </div>
          </div>
        </div>
      `).join('')}
    </div>
  `;

  parameterRequirements = {};
  logDebug(`Fetching procedure details for ID: ${procedureId}`);

  fetch(`/calibration/api/procedures/${procedureId}/`)
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return response.json();
    })
    .then(data => {
      readingFields.innerHTML = '';
      logDebug(`Procedure API response: ${JSON.stringify(data).substring(0, 200)}...`);

      if (data.success && data.procedure && data.procedure.parameters) {
        logDebug(`Found ${data.procedure.parameters.length} parameters`);

        const parameterPromises = data.procedure.parameters.map(param => {
          logDebug(`Processing parameter: ${param.name} (ID: ${param.id})`);

          // ✅ FIXED: Store BOTH required_readings AND num_readings
          parameterRequirements[param.id] = {
            name: param.name,
            required_readings: param.required_readings || param.num_readings, // Use required_readings if available
            num_readings: param.num_readings,
          };

          logDebug(
            `Stored requirement for ${param.name}: required=${parameterRequirements[param.id].required_readings
            }, total=${parameterRequirements[param.id].num_readings}`,
          );

          return fetchSetValues(param.id)
            .then(setValuesData => {
              const values = Array.isArray(setValuesData)
                ? setValuesData
                : setValuesData.set_values
                  ? setValuesData.set_values
                  : setValuesData.data
                    ? setValuesData.data
                    : [];
              logDebug(`Found ${values.length} set values for parameter ${param.name}`);
              generateParameterTable(param, values);
            })
            .catch(error => {
              console.error('Error fetching set values:', error);
              logDebug(`Error fetching set values for ${param.name}: ${error.message}`);
              generateParameterTable(param, []);
            });
        });

        return Promise.all(parameterPromises).then(() => {
          // Check button state after all parameters are loaded
          checkAllRequiredFields();
        });
      } else {
        const errorMsg = 'No parameters available for this procedure';
        readingFields.innerHTML = `<div class="alert alert-warning">${escapeHTML(errorMsg)}</div>`;
        logDebug(errorMsg);
        checkAllRequiredFields();
      }
    })
    .catch(error => {
      console.error('Error fetching procedure:', error);
      const errorMsg = `Error loading procedure details: ${error.message}`;
      readingFields.innerHTML = `<div class="alert alert-error">${escapeHTML(errorMsg)}</div>`;
      logDebug(errorMsg);
      checkAllRequiredFields();
    });
}

// Enhanced set values fetch with better error handling
function fetchSetValues(parameterId) {
  logDebug(`Fetching set values for parameter: ${parameterId}`);

  return fetch(`/calibration/api/set_values/?parameter=${parameterId}`)
    .then(response => {
      logDebug(`Set values API response status: ${response.status}`);

      if (!response.ok) {
        return response.text().then(text => {
          logDebug(`Set values API error response: ${text}`);
          throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
        });
      }
      return response.json();
    })
    .then(data => {
      logDebug(`Set values API success: ${JSON.stringify(data).substring(0, 200)}...`);
      return data;
    })
    .catch(error => {
      logDebug(`Set values fetch failed: ${error.message}`);
      throw error;
    });
}

// Generate calibration table (API order preserved)
function generateParameterTable(param, setValues) {
  const readingFields = document.getElementById('reading-fields');

  const paramSection = document.createElement('div');
  paramSection.className = 'parameter-section section-card';

  // Header
  const header = document.createElement('h3');
  header.className = 'parameter-header';
  header.innerHTML = `
        <span>${escapeHTML(param.name)}</span>
        <span>${escapeHTML(param.unit || 'No unit')}</span>
    `;

  // Resolution input
  const resolutionSection = document.createElement('div');
  resolutionSection.className = 'parameter-resolution';
  resolutionSection.innerHTML = `
        <div class="form-group">
            <label for="resolution_${escapeHTML(param.id)}">Resolution <span class="label-unit">${escapeHTML(param.unit || '')}</span></label>
            <input type="number"
                name="resolution_${escapeHTML(param.id)}"
                id="resolution_${escapeHTML(param.id)}"
                class="form-control resolution-input"
                step="0.000001"
                placeholder="e.g., 0.001"
                required
                data-param-id="${escapeHTML(param.id)}">
            <div class="error-message" id="error_resolution_${escapeHTML(param.id)}"></div>
            <small class="help-text">Enter the smallest unit the equipment can read for this parameter</small>
        </div>
    `;

  // ✅ FIXED: Use the correct required_readings value from param
  const requiredReadings = param.required_readings || param.num_readings;

  // Info section
  const infoSection = document.createElement('div');
  infoSection.className = 'parameter-info';
  infoSection.innerHTML = `
        <div class="info-item"><span class="info-label">Standard reference</span><span class="info-value">${escapeHTML(param.standard_reference || 'N/A')}</span></div>
        <div class="info-item"><span class="info-label">Reference uncertainty</span><span class="info-value">±${escapeHTML(param.reference_uncertainty || 'N/A')} ${escapeHTML(param.unit || '')}</span></div>
        <div class="info-item"><span class="info-label">Tolerance</span><span class="info-value">±${escapeHTML(param.tolerance || 'N/A')} ${escapeHTML(param.unit || '')}</span></div>
        <div class="info-item"><span class="info-label">Coverage factor</span><span class="info-value">k = ${escapeHTML(param.coverage_factor || '2.0')}</span></div>
        <div class="info-item"><span class="info-label">Readings required</span><span class="info-value">${escapeHTML(requiredReadings)} of ${escapeHTML(param.num_readings)}</span></div>
    `;

  // Calibration table
  const table = document.createElement('table');
  table.className = 'calibration-table';

  // Header row
  const thead = document.createElement('thead');
  let headerHTML = `
        <tr>
            <th rowspan="2">Set value</th>
            <th rowspan="2">Sub-parameter</th>
            <th colspan="${escapeHTML(param.num_readings)}">Readings (${escapeHTML(param.unit || '')})</th>
        </tr>
        <tr>
    `;
  for (let i = 1; i <= param.num_readings; i++) {
    const required = i <= requiredReadings ? ' *' : ''; // ✅ USE requiredReadings
    headerHTML += `<th>R${i}${escapeHTML(required)}</th>`;
  }
  headerHTML += '</tr>';
  thead.innerHTML = headerHTML;

  const tbody = document.createElement('tbody');

  if (setValues.length === 0) {
    logDebug(`No set values found for ${param.name}, using default row`);
    const row = document.createElement('tr');
    let rowHTML = `<td class="set-value-cell">Default</td><td>-</td>`;
    for (let i = 1; i <= param.num_readings; i++) {
      const required = i <= requiredReadings ? 'required' : ''; // ✅ USE requiredReadings
      const inputName = `reading_${param.id}_null_${i}`;
      rowHTML += `
                <td>
                    <input type="number"
                        name="${escapeHTML(inputName)}"
                        class="reading-input ${escapeHTML(required)}"
                        step="0.000001"
                        placeholder="R${i}"
                        data-param="${escapeHTML(param.id)}"
                        data-reading="${i}">
                    <div class="error-message" id="error_${escapeHTML(inputName)}"></div>
                </td>
            `;
    }
    row.innerHTML = rowHTML;
    tbody.appendChild(row);
  } else {
    logDebug(`Generating table with ${setValues.length} set values for ${param.name}`);
    setValues.forEach(setValue => {
      const row = document.createElement('tr');
      const subParamId = setValue.sub_parameter || 'null';
      const subParamName = setValue.sub_parameter_name || 'Default';

      let rowHTML = `
                <td class="set-value-cell">${escapeHTML(setValue.value)} ${escapeHTML(param.unit || '')}</td>
                <td class="sub-parameter-name">${escapeHTML(subParamName)}</td>
            `;

      for (let i = 1; i <= param.num_readings; i++) {
        const required = i <= requiredReadings ? 'required' : ''; // ✅ USE requiredReadings
        const inputName = `reading_${param.id}_${subParamId}_${setValue.id}_${i}`;
        rowHTML += `
                    <td>
                        <input type="number"
                            name="${escapeHTML(inputName)}"
                            class="reading-input ${escapeHTML(required)}"
                            step="0.000001"
                            placeholder="R${i}"
                            data-param="${escapeHTML(param.id)}"
                            data-sub-param="${escapeHTML(subParamId)}"
                            data-set-value="${escapeHTML(setValue.value)}"
                            data-reading="${i}">
                        <div class="error-message" id="error_${escapeHTML(inputName)}"></div>
                    </td>
                `;
      }

      row.innerHTML = rowHTML;
      tbody.appendChild(row);
    });
  }

  table.appendChild(thead);
  table.appendChild(tbody);

  paramSection.appendChild(header);
  paramSection.appendChild(resolutionSection);
  paramSection.appendChild(infoSection);
  const tableWrapper = document.createElement('div');
  tableWrapper.className = 'table-wrapper';
  tableWrapper.appendChild(table);
  paramSection.appendChild(tableWrapper);

  readingFields.appendChild(paramSection);

  // Hook up resolution validation
  const resolutionInput = paramSection.querySelector(`#resolution_${param.id}`);
  resolutionInput.addEventListener('input', function () {
    validateResolutionInput(this);
    updateReadingSteps(param.id, this.value);
    checkAllRequiredFields();
  });

  // Hook up reading validation
  addReadingValidation(paramSection);

  logDebug(
    `Parameter table generated for: ${param.name} with ${requiredReadings} required readings`,
  );
}

// Validate resolution input
function validateResolutionInput(input) {
  const value = input.value.trim();
  const errorDiv = document.getElementById(
    `error_resolution_${input.getAttribute('data-param-id')}`,
  );

  if (errorDiv) errorDiv.textContent = '';
  input.classList.remove('validation-error', 'validation-success');

  if (value === '') {
    if (errorDiv) errorDiv.textContent = 'Resolution is required';
    input.classList.add('validation-error');
    return false;
  }

  if (isNaN(parseFloat(value)) || parseFloat(value) <= 0) {
    if (errorDiv) errorDiv.textContent = 'Must be a positive number';
    input.classList.add('validation-error');
    return false;
  }

  input.classList.add('validation-success');
  return true;
}

function updateReadingSteps(paramId, resolution) {
  const readingInputs = document.querySelectorAll(`input[data-param="${paramId}"].reading-input`);
  readingInputs.forEach(input => {
    if (resolution && !isNaN(parseFloat(resolution))) {
      input.step = resolution;
    } else {
      input.step = '0.000001';
    }
  });
}

function addReadingValidation(paramSection) {
  const inputs = paramSection.querySelectorAll('.reading-input');
  inputs.forEach(input => {
    // Mark required inputs visually on load
    if (input.classList.contains('required')) {
      input.addEventListener('input', () => {
        input.classList.remove('missing-required', 'validation-error');
        const errorDiv = document.getElementById(`error_${input.name}`);
        if (errorDiv) errorDiv.textContent = '';
        validateReading(input);
        checkAllRequiredFields();
      });
      input.addEventListener('blur', () => {
        validateReading(input);
        // If still empty after blur, mark as missing
        if (input.value.trim() === '') {
          input.classList.add('missing-required');
        }
        checkAllRequiredFields();
      });
    } else {
      input.addEventListener('input', () => {
        input.classList.remove('missing-required', 'validation-error');
        const errorDiv = document.getElementById(`error_${input.name}`);
        if (errorDiv) errorDiv.textContent = '';
        validateReading(input);
        checkAllRequiredFields();
      });
      input.addEventListener('blur', () => {
        validateReading(input);
        checkAllRequiredFields();
      });
    }
  });
}

function validateReading(input) {
  const value = input.value.trim();
  const errorDiv = document.getElementById(`error_${input.name}`);
  input.classList.remove('validation-error', 'validation-success');
  if (errorDiv) errorDiv.textContent = '';

  if (value === '') {
    return;
  }

  if (isNaN(parseFloat(value))) {
    input.classList.add('validation-error');
    if (errorDiv) errorDiv.textContent = 'Invalid number';
    return;
  }

  input.classList.add('validation-success');
}

// Enhanced form validation and submission
document.getElementById('calibrationForm').addEventListener('submit', function (e) {
  e.preventDefault();

  const submitBtn = document.getElementById('submitBtn');
  submitBtn.disabled = true;
  submitBtn.textContent = 'Validating…';

  if (!validateAllInputs()) {
    // Re-evaluate button state based on current field state (do NOT just re-enable)
    checkAllRequiredFields();
    submitBtn.textContent = 'Start calibration';
    hideLoading();
    return false;
  }

  showLoading('Processing calibration data...');
  submitBtn.textContent = 'Processing…';

  // Step 1 → 2 after 800ms
  setTimeout(() => {
    advanceStep('step-validate', 'step-prepare');

    // Step 2 → 3 after another 800ms
    setTimeout(() => {
      advanceStep('step-prepare', 'step-submit');

      // Show success then submit
      setTimeout(() => {
        showSuccess('Validation complete', 'All inputs are valid. Submitting calibration data…');
        setTimeout(() => {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Start calibration';
          this.submit();
        }, 1500);
      }, 800);
    }, 800);
  }, 800);
});
