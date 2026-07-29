/**
 * Generic "Saved filters" widget — works on any page that includes
 * includes/saved_filters.html, since it operates on the page's own
 * query string (window.location.search) rather than named fields.
 * Talks to users.views.saved_filters (list/create/delete).
 */
(function () {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s || '';
    return div.innerHTML;
  }

  function initWidget(root) {
    const viewName = root.dataset.viewName;
    const toggleBtn = root.querySelector('.saved-filters-toggle');
    const dropdown = root.querySelector('.saved-filters-dropdown');
    const list = root.querySelector('.saved-filters-list');
    const nameInput = root.querySelector('.saved-filters-name-input');
    const saveBtn = root.querySelector('.saved-filters-save-btn');
    let loaded = false;

    function render(filters) {
      if (!filters.length) {
        list.innerHTML = '<div class="saved-filters-empty">No saved filters yet.</div>';
        return;
      }
      list.innerHTML = filters.map((f) => `
        <div class="saved-filter-item">
          <a href="?${new URLSearchParams(f.filter_params).toString()}" class="sf-name">${escapeHtml(f.name)}</a>
          <button type="button" class="sf-delete" data-id="${f.id}" aria-label="Delete ${escapeHtml(f.name)}">
            <i class="fas fa-times"></i>
          </button>
        </div>
      `).join('');
    }

    function fetchList() {
      list.innerHTML = '<div class="notif-loading">Loading…</div>';
      fetch(`/login/api/saved-filters/?view_name=${encodeURIComponent(viewName)}`, { credentials: 'same-origin' })
        .then((r) => r.json())
        .then((data) => {
          if (data.success) {
            render(data.filters);
            loaded = true;
          }
        })
        .catch(() => { list.innerHTML = '<div class="saved-filters-empty">Couldn’t load filters.</div>'; });
    }

    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const opening = dropdown.hidden;
      dropdown.hidden = !opening;
      if (opening && !loaded) fetchList();
    });

    document.addEventListener('click', (e) => {
      if (!dropdown.hidden && !dropdown.contains(e.target) && e.target !== toggleBtn) {
        dropdown.hidden = true;
      }
    });

    saveBtn.addEventListener('click', () => {
      const name = nameInput.value.trim();
      if (!name) return;
      const body = new URLSearchParams({
        view_name: viewName,
        name: name,
        query_string: window.location.search.replace(/^\?/, ''),
      });
      fetch('/login/api/saved-filters/create/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken, 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      })
        .then((r) => r.json())
        .then((data) => {
          if (data.success) {
            nameInput.value = '';
            loaded = false;
            fetchList();
          }
        });
    });

    list.addEventListener('click', (e) => {
      const del = e.target.closest('.sf-delete');
      if (!del) return;
      e.preventDefault();
      fetch(`/login/api/saved-filters/${del.dataset.id}/delete/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken },
      }).then(() => { loaded = false; fetchList(); });
    });
  }

  document.querySelectorAll('.saved-filters').forEach(initWidget);
})();
