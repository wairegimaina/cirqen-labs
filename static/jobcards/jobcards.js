// ==========================================
// Job Cards Logic — Fixed & Refactored
// ==========================================

$(document).ready(function () {

  // --- Global State ---
  var partRowCounter = 0;
  window.techSignaturePad = null;
  window.nurseSignaturePad = null;
  var isSubmitting = false;

  // --- 1. Initialisation ---

  // --- Live Clock for Time Completed ---
  // Tracks whether the user has manually overridden the live clock
  var completedIsLive = true;
  var liveClockInterval = null;

  // Helper: get current time as HH:MM:SS
  function getCurrentTimeStr() {
    var n = new Date();
    return ('0' + n.getHours()).slice(-2) + ':' +
      ('0' + n.getMinutes()).slice(-2) + ':' +
      ('0' + n.getSeconds()).slice(-2);
  }

  // Helper: get start time as HH:MM:SS (pad seconds if missing)
  function getStartTimeStr() {
    var val = $('#time_started').val() || '';
    if (val && val.split(':').length === 2) val += ':00';
    return val;
  }

  // Inject a small live indicator badge next to time_completed label
  var $completedLabel = $('label[for="time_completed"]');
  $completedLabel.append(
    ' <span id="live-clock-badge" class="badge bg-success ms-1" role="button" title="Click to return to live mode">Live</span>'
  );

  $('#live-clock-badge').on('click', function () {
    completedIsLive = true;
    $(this).removeClass('bg-secondary').addClass('bg-success').text('Live');
    tickLiveClock();
  });

  function tickLiveClock() {
    if (!completedIsLive) return;
    var now = getCurrentTimeStr();
    var start = getStartTimeStr();

    // Only update if current time is >= start time
    if (!start || now >= start) {
      $('#time_completed').val(now);
    }
  }

  // Tick every second
  liveClockInterval = setInterval(tickLiveClock, 1000);
  tickLiveClock(); // set immediately on load

  // Only stop the live clock when the user actually types into the field
  $('#time_completed').on('keydown', function () {
    completedIsLive = false;
    $('#live-clock-badge').removeClass('bg-success').addClass('bg-secondary').text('Manual');
  });

  // Time Started flatpickr — plain HH:MM
  var startFP = flatpickr('#time_started', {
    enableTime: true,
    noCalendar: true,
    dateFormat: 'H:i',
    time_24hr: true,
    defaultDate: new Date(),
    onChange: function (selectedDates, dateStr) {
      // If completed time has gone behind the new start time, snap it forward
      var completedVal = $('#time_completed').val();
      var startFull = dateStr + ':00';
      if (completedVal && completedVal < startFull) {
        $('#time_completed').val(startFull);
      }
      // Re-resume live mode if it was still live
      if (completedIsLive) tickLiveClock();
    }
  });

  // Time Completed — plain text input with seconds, managed by live clock above
  // (No flatpickr on this field so we can freely set HH:MM:SS values programmatically)

  $('.select2-single').select2({ theme: 'default', width: '100%', allowClear: false });

  initSignaturePads();

  // --- 2. Signature Pads ---

  /**
   * KEY FIX: Canvas coordinate space must match its CSS display size.
   * If you set canvas width/height via HTML attributes but then scale it
   * with CSS (width:100%), the pointer position is mapped to the wrong
   * coordinate — the cursor appears offset or "lags".
   *
   * Solution: after the canvas has been laid out (offsetWidth / offsetHeight
   * are non-zero), set the internal pixel buffer to match the display pixels,
   * multiplied by devicePixelRatio for crisp rendering on HiDPI screens.
   */
  function resizeCanvas(canvas) {
    var ratio = Math.max(window.devicePixelRatio || 1, 1);
    var displayW = canvas.offsetWidth;
    var displayH = canvas.offsetHeight;

    // Only resize if dimensions have actually changed to avoid clearing unnecessarily
    if (canvas.width !== displayW * ratio || canvas.height !== displayH * ratio) {
      canvas.width = displayW * ratio;
      canvas.height = displayH * ratio;
      canvas.getContext('2d').scale(ratio, ratio);
    }
  }

  function initSignaturePads() {
    var techCanvas = document.getElementById('signature-pad-tech');
    var nurseCanvas = document.getElementById('signature-pad-nurse');

    if (techCanvas) {
      resizeCanvas(techCanvas);
      window.techSignaturePad = new SignaturePad(techCanvas, {
        backgroundColor: 'rgb(255,255,255)',
        penColor: '#2c7be5',
        minWidth: 1,
        maxWidth: 3
      });
    }

    if (nurseCanvas) {
      resizeCanvas(nurseCanvas);
      window.nurseSignaturePad = new SignaturePad(nurseCanvas, {
        backgroundColor: 'rgb(255,255,255)',
        penColor: '#ef4444',
        minWidth: 1,
        maxWidth: 3
      });
    }
  }

  // Re-resize signature canvases when the window is resized (e.g. responsive breakpoint)
  var _resizeTimer;
  $(window).on('resize', function () {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(function () {
      var techCanvas = document.getElementById('signature-pad-tech');
      var nurseCanvas = document.getElementById('signature-pad-nurse');

      if (techCanvas && window.techSignaturePad) {
        var data = window.techSignaturePad.toData();
        resizeCanvas(techCanvas);
        window.techSignaturePad.fromData(data);
      }
      if (nurseCanvas && window.nurseSignaturePad) {
        var data2 = window.nurseSignaturePad.toData();
        resizeCanvas(nurseCanvas);
        window.nurseSignaturePad.fromData(data2);
      }
    }, 200);
  });

  // Auto-signature toggle – tech
  $('#use_auto_signature_tech').on('change', function () {
    if (this.checked) {
      loadUserAutoSignature('tech');
      $('#manual-signature-tech').hide();
      $('#auto-signature-preview-tech').show();
    } else {
      $('#manual-signature-tech').show();
      $('#auto-signature-preview-tech').hide();
      $('#signature_data').val('');
    }
  });

  // Auto-signature toggle – nurse
  $('#use_auto_signature_nurse').on('change', function () {
    if (this.checked) {
      loadUserAutoSignature('nurse');
      $('#manual-signature-nurse').hide();
      $('#auto-signature-preview-nurse').show();
    } else {
      $('#manual-signature-nurse').show();
      $('#auto-signature-preview-nurse').hide();
      $('#nurse_signature_data').val('');
    }
  });

  // --- 3. Parts Management ---

  window.addPartRow = function () {
    partRowCounter++;
    var workshopId = window.WORKSHOP_ID || '';

    var html = `
      <div class="part-row fade-in" data-part-row="${partRowCounter}">
        <select class="form-control part-select" name="part_${partRowCounter}" data-placeholder="Select Part">
          <option value="none">Loading…</option>
        </select>
        <input type="number" class="form-control quantity-input"  placeholder="Qty"  min="1" name="quantity_${partRowCounter}" disabled>
        <input type="number" class="form-control cost-input"      placeholder="Cost" min="0" step="0.01" name="cost_${partRowCounter}" disabled>
        <input type="text"   class="form-control total-cost-display" placeholder="Total" readonly disabled>
        <input type="text"   class="form-control remarks-input"   placeholder="Remarks" maxlength="200" name="remarks_${partRowCounter}" disabled>
        <button type="button" class="btn btn-danger btn-sm" onclick="removePartRow(${partRowCounter})">
          <i class="fas fa-trash"></i>
        </button>
      </div>`;

    $('#parts-container').append(html);
    initializePartSelect(partRowCounter, workshopId);
  };

  function initializePartSelect(rowId, workshopId) {
    var $row = $(`[data-part-row="${rowId}"]`);
    var $select = $row.find('.part-select');

    if (workshopId) {
      loadAccessoriesForWorkshop(workshopId)
        .then(function (accessories) { populateSelect($select, accessories); })
        .catch(function (err) {
          console.error(err);
          $select.html('<option value="none">Error loading</option>');
        });
    } else {
      populateSelect($select, window.ACCESSORIES_DATA || []);
    }
  }

  function populateSelect($el, data) {
    $el.empty().append('<option value="none">Select Part</option>');
    if (data && data.length) {
      data.forEach(function (item) {
        var text = item.display_name || (item.name + ' (' + item.stock_count + ')');
        $el.append('<option value="' + item.id + '">' + text + '</option>');
      });
    } else {
      $el.append('<option value="none" disabled>No parts available</option>');
    }
    $el.select2({ theme: 'default', width: '100%' });
  }

  window.removePartRow = function (id) {
    $(`[data-part-row="${id}"]`).fadeOut(180, function () {
      $(this).remove();
      updateJobCardTotalCost();
    });
  };

  // Part selection → stock check
  $(document).on('change', '.part-select', function () {
    var $row = $(this).closest('.part-row');
    var val = $(this).val();

    if (!val || val === 'none') {
      $row.find('input').prop('disabled', true).val('');
      $row.find('.stock-info').remove();
    } else {
      $row.find('.remarks-input').prop('disabled', false);
      checkRealTimeStock(val, $row);
    }
  });

  // Cost calculation
  $(document).on('input', '.quantity-input, .cost-input', function () {
    var $row = $(this).closest('.part-row');
    var qty = parseFloat($row.find('.quantity-input').val()) || 0;
    var cost = parseFloat($row.find('.cost-input').val()) || 0;

    $row.find('.total-cost-display').val((qty * cost).toFixed(2));
    updateJobCardTotalCost();

    if ($(this).hasClass('quantity-input')) {
      var max = parseFloat($(this).attr('max'));
      if (max && qty > max) {
        alert('Only ' + max + ' items available');
        $(this).val(max);
      }
    }
  });

  // --- 4. Equipment & PPM ---

  $('#department').on('change', function () {
    var deptId = $(this).val();
    var $eqSel = $('#equipment');

    if (!deptId) {
      $eqSel.empty().trigger('change');
      $('#equipment-details').slideUp();
      return;
    }

    $.ajax({
      url: window.LOAD_EQUIPMENT_URL,
      data: { department_id: deptId },
      success: function (data) {
        $eqSel.empty().append('<option value="">Select Equipment</option>');
        data.forEach(function (eq) {
          $eqSel.append(
            '<option value="' + eq.id + '"' +
            ' data-description="' + (eq.description || '') + '"' +
            ' data-serial="' + (eq.serial_number || '') + '"' +
            ' data-model="' + (eq.model || '') + '"' +
            ' data-status="' + (eq.status || '') + '">' +
            eq.description + ' (' + eq.serial_number + ')</option>'
          );
        });
        if (window.FORM_DATA.equipment) {
          $eqSel.val(window.FORM_DATA.equipment).trigger('change');
        }
      },
      error: function () { console.error('Failed to load equipment'); }
    });
  });

  $('#equipment').on('change', function () {
    var $opt = $(this).find('option:selected');
    var id = $(this).val();

    if (id) {
      $('#eq-desc').text($opt.data('description') || '-');
      $('#eq-serial').text($opt.data('serial') || '-');
      $('#eq-model').text($opt.data('model') || '-');
      $('#eq-status').text($opt.data('status') || '-');
      $('#equipment-details').slideDown();

      if ($('#action_taken').val() === 'PPM') loadPendingPPMSchedules();
    } else {
      $('#equipment-details').slideUp();
    }
  });

  $('#action_taken').on('change', function () {
    if ($(this).val() === 'PPM') {
      $('#ppm-schedule-container').slideDown();
      if ($('#equipment').val()) loadPendingPPMSchedules();
    } else {
      $('#ppm-schedule-container').slideUp();
    }
  });

  // --- 5. Form Submission ---

  $('#technician-form').on('submit', function (e) {
    if (isSubmitting) return false;

    var useAuto = $('#use_auto_signature_tech').is(':checked');
    if (!useAuto && window.techSignaturePad && window.techSignaturePad.isEmpty()) {
      alert('Please sign the job card.');
      e.preventDefault();
      return false;
    }
    if (!useAuto && window.techSignaturePad) {
      $('#signature_data').val(window.techSignaturePad.toDataURL());
    }

    var parts = [];
    $('.part-row').each(function () {
      var pid = $(this).find('.part-select').val();
      var qty = $(this).find('.quantity-input').val();
      if (pid && pid !== 'none' && qty) {
        parts.push({
          part_id: pid,
          quantity: qty,
          cost: $(this).find('.cost-input').val(),
          remarks: $(this).find('.remarks-input').val()
        });
      }
    });
    $('#spare_parts_data').val(JSON.stringify(parts));
    isSubmitting = true;
  });

  $('#nurse-approval-form').on('submit', function (e) {
    if ($('#use_auto_signature_nurse').is(':checked')) {
      // data already in hidden input
    } else if (window.nurseSignaturePad && !window.nurseSignaturePad.isEmpty()) {
      $('#nurse_signature_data').val(window.nurseSignaturePad.toDataURL());
    } else {
      alert('Signature required');
      e.preventDefault();
      return false;
    }

    var btn = $(document.activeElement).attr('name');
    if (btn === 'decline' && !$('#decline_reason').val().trim()) {
      alert('Reason required for decline');
      $('#decline_reason').focus();
      e.preventDefault();
      return false;
    }
  });

  // --- 6. Cost Totals ---

  function updateJobCardTotalCost() {
    var totalParts = 0;
    $('.total-cost-display').each(function () { totalParts += parseFloat($(this).val()) || 0; });

    var labor = parseFloat($('#labor_cost').val()) || 0;
    var extra = parseFloat($('#additional_costs').val()) || 0;

    $('#total_parts_cost_display').text(totalParts.toFixed(2));
    $('#grand_total_display').text((totalParts + labor + extra).toFixed(2));
  }

  $('#labor_cost, #additional_costs').on('input', updateJobCardTotalCost);

  // --- 7. Helpers ---

  function checkRealTimeStock(partId, $row) {
    $row.find('.part-select').addClass('loading');
    $.ajax({
      url: window.CHECK_STOCK_URL,
      data: { part_ids: partId },
      success: function (res) {
        $row.find('.part-select').removeClass('loading');
        $row.find('.stock-info').remove();

        var info = res.data && res.data[partId];
        if (info) {
          var cls = info.available ? 'available' : 'unavailable';
          var text = info.available ? 'Stock: ' + info.current_stock : 'Out of Stock';
          $row.find('.remarks-input').before('<div class="stock-info ' + cls + '">' + text + '</div>');

          var $qty = $row.find('.quantity-input');
          $qty.prop('disabled', !info.available);
          $row.find('.cost-input').prop('disabled', !info.available);
          if (info.available) $qty.attr('max', info.current_stock);
        }
      },
      error: function () {
        $row.find('.part-select').removeClass('loading');
        console.error('Stock check failed');
      }
    });
  }

  function loadPendingPPMSchedules() {
    var eqId = $('#equipment').val();
    if (!eqId) return;

    $('#ppm_schedule_id').html('<option>Loading…</option>');
    $.get(window.GET_PPM_SCHEDULES_URL, { equipment_id: eqId }, function (res) {
      var $sel = $('#ppm_schedule_id').empty();
      $sel.append('<option value="">No Schedule (Manual)</option>');
      if (res.schedules && res.schedules.length) {
        $('#ppm-no-schedules').hide();
        res.schedules.forEach(function (s) {
          $sel.append('<option value="' + s.id + '">' + s.scheduled_month + ' (' + s.status + ')</option>');
        });
      } else {
        $('#ppm-no-schedules').show();
      }
    }).fail(function () {
      $('#ppm_schedule_id').html('<option value="">Error loading schedules</option>');
    });
  }

  function loadUserAutoSignature(type) {
    $.get(window.GET_SIGNATURE_URL, function (data) {
      if (data.signature_data) {
        $('#signature-preview-img-' + type).attr('src', data.signature_data);
        if (type === 'tech') $('#signature_data').val(data.signature_data);
        else $('#nurse_signature_data').val(data.signature_data);
      }
    }).fail(function () { console.error('Failed to load signature'); });
  }

  // Trigger department load on page load if pre-filled
  if (window.FORM_DATA && window.FORM_DATA.department) {
    $('#department').trigger('change');
  }

}); // end document.ready


// ── Global helpers (called from HTML onclick) ──────────────────────────────

function clearSignatureTech() {
  if (window.techSignaturePad) {
    window.techSignaturePad.clear();
    $('#signature_data').val('');
  }
}

function clearSignatureNurse() {
  if (window.nurseSignaturePad) {
    window.nurseSignaturePad.clear();
    $('#nurse_signature_data').val('');
  }
}

function loadAccessoriesForWorkshop(id) {
  return $.get(window.LOAD_ACCESSORIES_URL, { workshop_id: id });
}
