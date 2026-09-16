/**
 * Header notification bell + dropdown.
 *
 * Talks to CalSoft.view_modules.notifications: the list is fetched lazily
 * (only when the dropdown opens) rather than on every page load, since the
 * unread count itself is already rendered server-side via nav.unread_notification_count
 * (Equiper/context_processors.py) — no need to round-trip just to show a number.
 */
(function () {
  const bell = document.getElementById('notifBell');
  if (!bell) return; // not authenticated, block wasn't rendered

  const dropdown = document.getElementById('notifDropdown');
  const list = document.getElementById('notifList');
  const badge = document.getElementById('notifBadge');
  const markAllBtn = document.getElementById('notifMarkAll');

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  let loaded = false;

  function timeAgo(iso) {
    if (!iso) return '';
    const diffMs = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  }

  const escapeHtml = window.escapeHTML; // static/js/escape.js

  function render(notifications) {
    if (!notifications.length) {
      list.innerHTML = '<div class="notif-empty">No notifications yet.</div>';
      return;
    }
    list.innerHTML = notifications.map((n) => `
      <a href="#" class="notif-item ${n.is_read ? '' : 'is-unread'}" data-id="${escapeHTML(n.id)}" data-url="${escapeHtml(n.action_url)}">
        <div class="notif-item-title">${escapeHtml(n.title)}</div>
        <div class="notif-item-message">${escapeHtml(n.message)}</div>
        <div class="notif-item-time">${escapeHTML(timeAgo(n.created_at))}</div>
      </a>
    `).join('');
  }

  function updateBadge(count) {
    if (count > 0) {
      if (!badge) {
        const b = document.createElement('span');
        b.className = 'notif-badge';
        b.id = 'notifBadge';
        bell.appendChild(b);
      }
      document.getElementById('notifBadge').textContent = count;
    } else if (badge) {
      badge.remove();
    }
  }

  function fetchList() {
    list.innerHTML = '<div class="notif-loading">Loading…</div>';
    fetch('/calibration/api/notifications/', { credentials: 'same-origin' })
      .then((r) => r.json())
      .then((data) => {
        if (!data.success) return;
        render(data.notifications);
        loaded = true;
      })
      .catch(() => {
        list.innerHTML = '<div class="notif-empty">Couldn’t load notifications.</div>';
      });
  }

  function markRead(id) {
    fetch(`/calibration/api/notifications/${id}/read/`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken },
    });
  }

  function closeDropdown() {
    dropdown.hidden = true;
    bell.setAttribute('aria-expanded', 'false');
  }

  bell.addEventListener('click', (e) => {
    e.stopPropagation();
    const opening = dropdown.hidden;
    dropdown.hidden = !opening;
    bell.setAttribute('aria-expanded', String(opening));
    if (opening && !loaded) fetchList();
  });

  document.addEventListener('click', (e) => {
    if (!dropdown.hidden && !dropdown.contains(e.target) && e.target !== bell) {
      closeDropdown();
    }
  });

  list.addEventListener('click', (e) => {
    const item = e.target.closest('.notif-item');
    if (!item) return;
    e.preventDefault();
    const id = item.dataset.id;
    const url = item.dataset.url;
    item.classList.remove('is-unread');
    markRead(id);
    updateBadge(document.querySelectorAll('.notif-item.is-unread').length);
    if (url) window.location.href = url;
  });

  markAllBtn?.addEventListener('click', () => {
    fetch('/calibration/api/notifications/read-all/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken },
    }).then(() => {
      list.querySelectorAll('.notif-item.is-unread').forEach((el) => el.classList.remove('is-unread'));
      updateBadge(0);
    });
  });
})();
