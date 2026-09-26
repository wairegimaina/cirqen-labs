/* Work order Checklist section.
 *
 * Once equipment and action are chosen, list the checklists from the
 * Checklists module (the ones written for this device + task first) in the
 * "Select Checklist" dropdown and pre-select the first suggestion. The selected
 * checklist's steps render one row each; the technician can also add steps for
 * this one job. Field names match jobcard/checklists.py:
 *   checklist_template
 *   checklist_result_<id>, checklist_value_<id>, checklist_note_<id>, checklist_at_<id>
 *   custom_keys, custom_task_<k>, custom_result_<k>, custom_note_<k>, custom_at_<k>
 * checklist_at_ / custom_at_ record when each step was answered.
 * Answers posted back after a rejected submit are restored from the
 * #checklist-answers JSON block.
 */
(function ($) {
  'use strict';

  const $container = $('#checklist-container');
  if (!$container.length || !window.GET_CHECKLIST_URL) return;

  const $select = $('#checklist_template');
  const $hint = $('#checklist-hint');
  const $body = $('#checklist-body');
  const $custom = $('#checklist-custom');
  const $progress = $('#checklist-progress');
  let restored = {};
  try {
    restored = JSON.parse(document.getElementById('checklist-answers')?.textContent || 'null') || {};
  } catch (e) {
    restored = {};
  }
  let customChoices = [
    { value: 'done', label: 'Done' },
    { value: 'not_done', label: 'Not done' },
    { value: 'na', label: 'N/A' },
  ];
  // The checklist chosen before a rejected submit; applied on the first real load.
  let pendingRestore = 'checklist_template' in restored ? restored.checklist_template : undefined;
  let optionsSeq = 0;
  let templateSeq = 0;
  let customCounter = 0;

  function detailUrl(id) {
    return (window.CHECKLIST_DETAIL_URL || '').replace('00000000-0000-0000-0000-000000000000', id);
  }

  function esc(v) {
    return window.escapeHTML ? window.escapeHTML(v == null ? '' : v) : String(v == null ? '' : v);
  }

  function radios(name, idPrefix, choices, current) {
    return choices.map((c) => {
      const inputId = `${idPrefix}_${c.value}`;
      return `<div class="form-check form-check-inline">
          <input class="form-check-input checklist-result" type="radio" name="${esc(name)}"
                 id="${esc(inputId)}" value="${esc(c.value)}" ${current === c.value ? 'checked' : ''}>
          <label class="form-check-label" for="${esc(inputId)}">${esc(c.label)}</label>
        </div>`;
    }).join('');
  }

  function itemHtml(item, index) {
    const id = item.id;
    const valueField = item.response_type === 'value'
      ? `<input type="text" class="form-control form-control-sm mt-1" name="checklist_value_${esc(id)}"
               placeholder="Reading" maxlength="100" value="${esc(restored[`checklist_value_${id}`] || '')}"
               aria-label="Reading for ${esc(item.task)}">`
      : '';
    return `<div class="checklist-item border rounded p-2 mb-2" data-required="${item.is_required ? 1 : 0}">
        <div class="fw-semibold">${index}. ${esc(item.task)}${item.is_required ? ' <span class="text-danger">*</span>' : ''}</div>
        ${item.guidance ? `<div class="small text-muted" style="white-space: pre-line">${esc(item.guidance)}</div>` : ''}
        ${item.expected_result ? `<div class="small"><strong>Expected:</strong> ${esc(item.expected_result)}</div>` : ''}
        <div class="mt-1">${radios(`checklist_result_${id}`, `cl_${id}`, item.choices, restored[`checklist_result_${id}`])}</div>
        ${valueField}
        <input type="text" class="form-control form-control-sm mt-1" name="checklist_note_${esc(id)}"
               placeholder="Remarks (required if failed / not done)" maxlength="500"
               value="${esc(restored[`checklist_note_${id}`] || '')}" aria-label="Remarks for ${esc(item.task)}">
        <input type="hidden" class="checklist-at" name="checklist_at_${esc(id)}" value="${esc(restored[`checklist_at_${id}`] || '')}">
      </div>`;
  }

  function renderTemplate(t) {
    if (!t) {
      $body.empty();
      updateProgress();
      return;
    }
    let html = `<div class="small text-muted mb-2">${esc(t.applies_to)} · ${esc(t.task_type)} ·
      <a href="${esc(detailUrl(t.id))}" target="_blank" rel="noopener">view checklist</a></div>`;
    if (t.instructions) {
      html += `<div class="alert alert-light small" style="white-space: pre-line">${esc(t.instructions)}</div>`;
    }
    t.items.forEach((item, i) => { html += itemHtml(item, i + 1); });
    if (!t.items.length) html += '<div class="text-muted small">This checklist has no steps yet.</div>';
    $body.html(html);
    updateProgress();
  }

  function loadTemplate(templateId) {
    const seq = ++templateSeq;
    if (!templateId) {
      renderTemplate(null);
      return;
    }
    $body.html('<div class="text-muted small">Loading checklist…</div>');
    $.getJSON(window.GET_CHECKLIST_URL, { template_id: templateId })
      .done((data) => {
        if (seq !== templateSeq) return;
        if (data && data.success) renderTemplate(data.template);
        else $body.html('<div class="alert alert-warning">That checklist is no longer available.</div>');
      })
      .fail(() => {
        if (seq !== templateSeq) return;
        $body.html('<div class="alert alert-warning">Could not load the checklist.</div>');
      });
  }

  function optionHtml(t) {
    return `<option value="${esc(t.id)}">${esc(t.title)} — ${esc(t.applies_to)} (${esc(t.task_type)})</option>`;
  }

  function renderOptions(data, previous) {
    let html = '<option value="">— No checklist —</option>';
    if (data.suggested.length) {
      html += `<optgroup label="Suggested for this equipment and task">${data.suggested.map(optionHtml).join('')}</optgroup>`;
    }
    if (data.others.length) {
      html += `<optgroup label="Other checklists">${data.others.map(optionHtml).join('')}</optgroup>`;
    }
    $select.html(html);
    const ids = data.suggested.concat(data.others).map((t) => t.id);
    let chosen = '';
    if (previous !== undefined && (previous === '' || ids.includes(previous))) chosen = previous;
    else if (data.suggested.length) chosen = data.suggested[0].id;
    $select.val(chosen);
    $hint.text(data.suggested.length
      ? `${data.suggested.length} checklist(s) written for this equipment and task.`
      : 'No checklist is written for this equipment and task; pick another or add steps below.');
    loadTemplate(chosen);
  }

  function loadOptions() {
    const equipmentId = $('#equipment').val();
    const action = $('#action_taken').val();
    if (!equipmentId || !action) {
      $container.hide();
      return;
    }
    const seq = ++optionsSeq;
    $.getJSON(window.GET_CHECKLIST_URL, { equipment_id: equipmentId, action: action })
      .done((data) => {
        if (seq !== optionsSeq) return; // a newer selection is already loading
        if (!data || !data.success) return;
        if (data.custom_choices) customChoices = data.custom_choices;
        $container.show();
        renderOptions(data, pendingRestore);
        pendingRestore = undefined;
      })
      .fail(() => {
        if (seq !== optionsSeq) return;
        $container.show();
        $body.html('<div class="alert alert-warning">Could not load checklists for this equipment.</div>');
      });
  }

  function addCustomStep(values) {
    const key = values.key || `n${Date.now().toString(36)}${customCounter++}`;
    const html = `<div class="checklist-item checklist-custom border rounded p-2 mb-2" data-required="1" data-key="${esc(key)}">
        <input type="hidden" name="custom_keys" value="${esc(key)}">
        <input type="hidden" class="checklist-at" name="custom_at_${esc(key)}" value="${esc(values.at || '')}">
        <div class="d-flex gap-2 align-items-start">
          <input type="text" class="form-control form-control-sm" name="custom_task_${esc(key)}" maxlength="255"
                 placeholder="Step, e.g. Check battery" value="${esc(values.task || '')}" aria-label="Step">
          <button type="button" class="btn btn-outline-danger btn-sm checklist-remove-step" title="Remove step" aria-label="Remove step">
            <i class="fas fa-times"></i>
          </button>
        </div>
        <div class="mt-1">${radios(`custom_result_${key}`, `cs_${key}`, customChoices, values.result)}</div>
        <input type="text" class="form-control form-control-sm mt-1" name="custom_note_${esc(key)}" maxlength="500"
               placeholder="Remarks (required if not done)" value="${esc(values.note || '')}" aria-label="Remarks">
      </div>`;
    $custom.append(html);
    updateProgress();
  }

  function updateProgress() {
    const $items = $container.find('.checklist-item');
    const answered = $items.filter((_, el) => $(el).find('.checklist-result:checked').length).length;
    $progress.text($items.length ? `${answered} / ${$items.length} answered` : '');
  }

  $(document).on('change', '#equipment, #action_taken', loadOptions);
  $select.on('change', () => loadTemplate($select.val()));
  $('#checklist-add-step').on('click', () => addCustomStep({}));
  $custom.on('click', '.checklist-remove-step', function () {
    $(this).closest('.checklist-item').remove();
    updateProgress();
  });
  // Record when each step was answered (the server keeps it as "completed at").
  $container.on('change', '.checklist-result', function () {
    $(this).closest('.checklist-item').find('.checklist-at').val(new Date().toISOString());
    $(this).closest('.checklist-item').removeClass('border-danger');
    updateProgress();
  });

  // Block submit while required steps are unanswered (the server checks too).
  // Capture phase on document, so this runs before jobcards.js's submit
  // handler, which marks the form as submitting and would ignore a retry.
  document.addEventListener('submit', function (e) {
    if (e.target.id !== 'technician-form') return;
    const missing = $container.find('.checklist-item[data-required="1"]').filter((_, el) => {
      const $el = $(el);
      if ($el.hasClass('checklist-custom') && !$el.find('input[name^="custom_task_"]').val().trim()) return false;
      return !$el.find('.checklist-result:checked').length;
    });
    if (!missing.length) return;
    e.preventDefault();
    e.stopPropagation();
    missing.addClass('border-danger');
    missing.first()[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
    $progress.text(`${missing.length} required step(s) unanswered`);
    if (window.notify) window.notify(`Answer all required checklist steps (${missing.length} left).`, 'error');
  }, true);

  (restored.custom_keys || []).forEach((key) => addCustomStep({
    key,
    task: restored[`custom_task_${key}`],
    result: restored[`custom_result_${key}`],
    note: restored[`custom_note_${key}`],
    at: restored[`custom_at_${key}`],
  }));
  loadOptions();
})(jQuery);
