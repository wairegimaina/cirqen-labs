/**
 * Excel bulk-import modal (templates/includes/excel_import_modal.html).
 *
 * Every element marked [data-excel-import] is wired up on load. The chosen
 * file is kept in memory and posted twice: once to preview (the server
 * validates and rolls back) and once to commit. Nothing is stored server-side
 * between the two calls. After a commit, closing the modal reloads the page
 * so the new rows appear.
 */
(function () {
  'use strict';

  const BADGES = {
    create: '<span class="badge bg-success">New</span>',
    skip: '<span class="badge bg-secondary">Exists</span>',
    error: '<span class="badge bg-danger">Error</span>',
  };

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function csrfToken(modal) {
    return (
      modal.querySelector('[name=csrfmiddlewaretoken]')?.value ||
      document.cookie.match(/csrftoken=([\w-]+)/)?.[1] ||
      ''
    );
  }

  function setup(modal) {
    const find = (selector) => modal.querySelector(selector);
    const role = (name) => find(`[data-role="${name}"]`);
    const noun = modal.dataset.noun || 'record';
    let file = null;
    let committed = false;

    function showError(message) {
      const box = role('error');
      box.textContent = message;
      box.hidden = false;
    }

    function setBanner(kind, html) {
      const banner = role('banner');
      banner.className = `alert alert-${kind}`;
      banner.innerHTML = html;
    }

    function reset() {
      file = null;
      committed = false;
      role('file').value = '';
      find('[data-step="select"]').hidden = false;
      find('[data-step="review"]').hidden = true;
      role('footer').hidden = true;
      role('error').hidden = true;
      role('rows').innerHTML = '';
    }

    function send(commit) {
      if (!commit) {
        const input = role('file');
        if (!input.files.length) return showError('Please choose an Excel file first.');
        file = input.files[0];
        role('error').hidden = true;
      }
      if (!file) return showError('Please choose an Excel file first.');

      const button = find(commit ? '[data-action="confirm"]' : '[data-action="preview"]');
      const original = button.innerHTML;
      button.disabled = true;
      button.innerHTML = `<span class="spinner-border spinner-border-sm me-2"></span>${
        commit ? 'Importing...' : 'Validating...'
      }`;

      const body = new FormData();
      body.append('file', file);
      body.append('commit', commit ? 'true' : 'false');
      const createMissing = role('create-missing');
      if (createMissing) body.append('create_missing', createMissing.checked ? 'true' : 'false');
      body.append('csrfmiddlewaretoken', csrfToken(modal));

      const fail = (message) => {
        if (commit) {
          setBanner('danger', `<i class="fas fa-times-circle me-2"></i>${escapeHtml(message)}`);
          if (typeof window.notify === 'function') window.notify(message, 'error');
        } else {
          showError(message);
        }
      };

      fetch(modal.dataset.uploadUrl, {
        method: 'POST',
        body,
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then((res) => res.json())
        .then((data) => {
          if (!data.success) return fail(data.error || 'The upload could not be processed.');
          render(data);
        })
        .catch(() => fail('Upload failed. Please try again.'))
        .finally(() => {
          button.disabled = false;
          button.innerHTML = original;
        });
    }

    function render(data) {
      const counts = data.counts;
      committed = data.committed;

      find('[data-step="select"]').hidden = true;
      find('[data-step="review"]').hidden = false;
      role('footer').hidden = false;

      role('stat-total').textContent = data.total;
      role('stat-create').textContent = counts.create;
      role('stat-skip').textContent = counts.skip;
      role('stat-error').textContent = counts.error;

      const skipped = counts.skip ? `, ${counts.skip} already existed` : '';
      const failed = counts.error ? `, ${counts.error} had errors and were not imported` : '';
      if (data.committed) {
        setBanner(
          'success',
          `<i class="fas fa-check-circle me-2"></i><strong>Import complete.</strong>
          ${counts.create} ${noun}(s) added${skipped}${failed}.`,
        );
      } else if (!counts.create) {
        setBanner(
          counts.error ? 'danger' : 'warning',
          `<i class="fas fa-times-circle me-2"></i><strong>Nothing to import.</strong> ${
            counts.error
              ? 'Fix the errors below and upload again.'
              : 'Everything in this file already exists.'
          }`,
        );
      } else {
        setBanner(
          'info',
          `<i class="fas fa-info-circle me-2"></i><strong>Preview only - nothing has been saved yet.</strong>
          ${counts.create} ${noun}(s) are ready to import${skipped}${failed}.`,
        );
      }

      const created = role('created');
      const parts = Object.entries(data.created || {}).map(
        ([label, names]) =>
          `<strong>${escapeHtml(label)} ${data.committed ? 'created' : 'to create'}:</strong> ${names
            .map(escapeHtml)
            .join(', ')}`,
      );
      created.innerHTML = parts.join('<br>');
      created.hidden = !parts.length;

      const warnings = [];
      if (data.file_truncated) {
        warnings.push('The file exceeds the 5,000-row limit; only the first 5,000 rows were processed.');
      }
      if (data.rows_truncated) {
        warnings.push('Only the first 500 rows are listed below; all rows were still processed.');
      }
      role('warnings').innerHTML = warnings.join('<br>');
      role('warnings').hidden = !warnings.length;

      role('rows').innerHTML = data.rows
        .map(
          (row) => `<tr class="${row.action === 'error' ? 'table-danger' : ''}">
            <td class="text-nowrap">${row.sheet ? `${escapeHtml(row.sheet)} &middot; ` : ''}${row.row}</td>
            <td>${BADGES[row.action] || escapeHtml(row.action)}</td>
            <td>${escapeHtml(row.item) || '-'}</td>
            <td class="small ${row.action === 'error' ? 'text-danger' : 'text-muted'}">
              ${row.messages.map(escapeHtml).join('<br>')}
            </td>
          </tr>`,
        )
        .join('');

      const confirm = find('[data-action="confirm"]');
      find('[data-action="back"]').hidden = data.committed;
      find('[data-action="done"]').hidden = !data.committed;
      confirm.hidden = data.committed || !counts.create;
      if (!data.committed) {
        confirm.innerHTML = `<i class="fas fa-check"></i> Import ${counts.create} ${noun}(s)`;
      }
    }

    find('[data-action="preview"]').addEventListener('click', () => send(false));
    find('[data-action="confirm"]').addEventListener('click', () => send(true));
    find('[data-action="back"]').addEventListener('click', reset);
    find('[data-action="done"]').addEventListener('click', () => {
      bootstrap.Modal.getInstance(modal)?.hide();
    });
    modal.addEventListener('hidden.bs.modal', () => {
      if (committed) {
        window.location.reload();
      } else {
        reset();
      }
    });
  }

  function init() {
    document.querySelectorAll('[data-excel-import]').forEach(setup);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
