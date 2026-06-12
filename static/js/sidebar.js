/**
 * EquiperSidebarManager — Desktop only
 * ─────────────────────────────────────
 * Responsibilities:
 *   • Toggle sidebar open / closed via the hamburger button
 *   • Close when a nav link is clicked (page navigation)
 *   • Close on Escape key
 *   • Animate 2nd-level dropdown submenus (data-toggle="submenu")
 *   • Animate 3rd-level nested submenus  (data-toggle="nested-submenu")
 *   • Auto-expand the active menu branch on page load
 *   • Inject the copyright year
 *
 * Default state: CLOSED — opens on hamburger click.
 */

(function () {
  'use strict';

  class EquiperSidebarManager {

    constructor() {
      this.sidebar = document.getElementById('equiperSidebar');
      this.mainWrapper = document.getElementById('mainWrapper');
      this.toggleBtn = document.getElementById('sidebarToggle');

      if (!this.sidebar) return; // No sidebar on this page

      this.isOpen = false; // Default: closed

      this._init();
    }

    /* ─────────────────────────────────────────────────────────
       INIT
    ───────────────────────────────────────────────────────── */
    _init() {
      this._bindToggleButton();
      this._bindNavLinks();
      this._bindKeyboard();
      this._setupDropdownToggles();
      this._setupNestedToggles();
      this._autoExpandActiveMenus();
      this._injectCopyrightYear();

      // Always start closed
      this._close();
    }

    /* ─────────────────────────────────────────────────────────
       OPEN / CLOSE
    ───────────────────────────────────────────────────────── */
    _open() {
      this.sidebar.classList.add('sidebar-open');
      this.sidebar.classList.remove('hidden');
      this.mainWrapper?.classList.add('sidebar-visible');
      this.mainWrapper?.classList.remove('sidebar-hidden');
      this.toggleBtn?.classList.add('is-active');
      this.isOpen = true;
    }

    _close() {
      this.sidebar.classList.remove('sidebar-open');
      this.sidebar.classList.add('hidden');
      this.mainWrapper?.classList.remove('sidebar-visible');
      this.mainWrapper?.classList.add('sidebar-hidden');
      this.toggleBtn?.classList.remove('is-active');
      this.isOpen = false;
    }

    /* ─────────────────────────────────────────────────────────
       EVENT BINDINGS
    ───────────────────────────────────────────────────────── */
    _bindToggleButton() {
      if (!this.toggleBtn) return;
      this.toggleBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.toggle();
      });
    }

    // Close the sidebar when the user clicks any real nav link
    // (excludes toggle triggers which only open/close submenus)
    _bindNavLinks() {
      const links = this.sidebar.querySelectorAll(
        '.menu-link:not([data-toggle="submenu"]), .dropdown-link:not([data-toggle="nested-submenu"]), .nested-link'
      );
      links.forEach(link => {
        link.addEventListener('click', () => {
          if (this.isOpen) this._close();
        });
      });
    }

    _bindKeyboard() {
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.isOpen) this._close();
      });
    }

    /* ─────────────────────────────────────────────────────────
       DROPDOWN SUBMENUS  (2nd level)
       Uses scrollHeight measurement to avoid the height:0 bug.
    ───────────────────────────────────────────────────────── */
    _setupDropdownToggles() {
      document.querySelectorAll('[data-toggle="submenu"]').forEach(toggle => {
        toggle.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();

          const entry = toggle.closest('.menu-entry');
          const menu = entry?.querySelector('.dropdown-menu');
          const arrow = toggle.querySelector('.submenu-toggle-arrow');

          if (!entry || !menu) return;

          // Close every other open dropdown at this level first
          document.querySelectorAll('.menu-entry.dropdown-open').forEach(other => {
            if (other !== entry) {
              this._closeDropdown(
                other,
                other.querySelector('.dropdown-menu'),
                other.querySelector('.submenu-toggle-arrow')
              );
            }
          });

          entry.classList.contains('dropdown-open')
            ? this._closeDropdown(entry, menu, arrow)
            : this._openDropdown(entry, menu, arrow);
        });
      });
    }

    _openDropdown(entry, menu, arrow) {
      entry.classList.add('dropdown-open');

      // Temporarily unrestrict height to measure true scrollHeight
      menu.style.maxHeight = 'none';
      menu.style.display = 'block';
      menu.offsetHeight;                      // force reflow
      const h = menu.scrollHeight;
      menu.style.maxHeight = '0';
      menu.offsetHeight;                      // force reflow
      menu.style.maxHeight = h + 'px';

      if (arrow) arrow.style.transform = 'rotate(180deg)';
    }

    _closeDropdown(entry, menu, arrow) {
      entry.classList.remove('dropdown-open');

      if (menu) {
        menu.style.maxHeight = '0';
        // Clear inline style once transition finishes so CSS takes full control
        menu.addEventListener('transitionend', function handler() {
          if (!entry.classList.contains('dropdown-open')) {
            menu.style.maxHeight = '';
          }
          menu.removeEventListener('transitionend', handler);
        });
      }

      if (arrow) arrow.style.transform = 'rotate(0deg)';

      // Collapse any open nested menus inside this dropdown too
      entry?.querySelectorAll('.dropdown-entry.nested-open').forEach(ne => {
        this._closeNested(
          ne,
          ne.querySelector('.nested-dropdown'),
          ne.querySelector('.nested-toggle-arrow')
        );
      });
    }

    /* ─────────────────────────────────────────────────────────
       NESTED SUBMENUS  (3rd level)
    ───────────────────────────────────────────────────────── */
    _setupNestedToggles() {
      document.querySelectorAll('[data-toggle="nested-submenu"]').forEach(toggle => {
        toggle.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();

          const entry = toggle.closest('.dropdown-entry');
          const menu = entry?.querySelector('.nested-dropdown');
          const arrow = toggle.querySelector('.nested-toggle-arrow');

          if (!entry || !menu) return;

          // Close sibling nested menus first
          toggle.closest('.dropdown-menu')
            ?.querySelectorAll('.dropdown-entry.nested-open')
            .forEach(other => {
              if (other !== entry) {
                this._closeNested(
                  other,
                  other.querySelector('.nested-dropdown'),
                  other.querySelector('.nested-toggle-arrow')
                );
              }
            });

          entry.classList.contains('nested-open')
            ? this._closeNested(entry, menu, arrow)
            : this._openNested(entry, menu, arrow);
        });
      });
    }

    _openNested(entry, menu, arrow) {
      entry.classList.add('nested-open');

      menu.style.maxHeight = 'none';
      menu.style.display = 'block';
      menu.offsetHeight;
      const h = menu.scrollHeight;
      menu.style.maxHeight = '0';
      menu.offsetHeight;
      menu.style.maxHeight = h + 'px';

      if (arrow) arrow.style.transform = 'rotate(180deg)';

      // Grow the parent dropdown height to fit the newly revealed nested items
      const parentMenu = entry.closest('.dropdown-menu');
      if (parentMenu) {
        setTimeout(() => {
          parentMenu.style.maxHeight = 'none';
          parentMenu.offsetHeight;
          parentMenu.style.maxHeight = parentMenu.scrollHeight + 'px';
        }, 40);
      }
    }

    _closeNested(entry, menu, arrow) {
      entry.classList.remove('nested-open');

      if (menu) {
        menu.style.maxHeight = '0';
        menu.addEventListener('transitionend', function handler() {
          if (!entry.classList.contains('nested-open')) {
            menu.style.maxHeight = '';
          }
          menu.removeEventListener('transitionend', handler);
        });
      }

      if (arrow) arrow.style.transform = 'rotate(0deg)';
    }

    /* ─────────────────────────────────────────────────────────
       AUTO-EXPAND ACTIVE MENUS ON PAGE LOAD
    ───────────────────────────────────────────────────────── */
    _autoExpandActiveMenus() {
      // Active 2nd-level link → expand its parent dropdown
      const activeDropdownLink = document.querySelector('.dropdown-link.active-dropdown');
      if (activeDropdownLink) {
        const entry = activeDropdownLink.closest('.menu-entry');
        const menu = entry?.querySelector('.dropdown-menu');
        const arrow = entry?.querySelector('.submenu-toggle-arrow');
        if (entry && menu) this._openDropdown(entry, menu, arrow);
      }

      // Active 3rd-level link → expand both nested menu and top-level parent
      const activeNestedLink = document.querySelector('.nested-link.active-nested');
      if (activeNestedLink) {
        // 3rd-level nested menu
        const nEntry = activeNestedLink.closest('.dropdown-entry');
        const nMenu = nEntry?.querySelector('.nested-dropdown');
        const nArrow = nEntry?.querySelector('.nested-toggle-arrow');
        if (nEntry && nMenu) this._openNested(nEntry, nMenu, nArrow);

        // Top-level parent dropdown
        const topEntry = activeNestedLink.closest('.menu-entry');
        const topMenu = topEntry?.querySelector('.dropdown-menu');
        const topArrow = topEntry?.querySelector('.submenu-toggle-arrow');
        if (topEntry && topMenu) this._openDropdown(topEntry, topMenu, topArrow);
      }
    }

    /* ─────────────────────────────────────────────────────────
       COPYRIGHT YEAR
    ───────────────────────────────────────────────────────── */
    _injectCopyrightYear() {
      const el = document.getElementById('copyrightYear');
      if (el) el.textContent = new Date().getFullYear();
    }

    /* ─────────────────────────────────────────────────────────
       PUBLIC API
    ───────────────────────────────────────────────────────── */
    toggle() { this.isOpen ? this._close() : this._open(); }
    open() { this._open(); }
    close() { this._close(); }

    getState() {
      return { isOpen: this.isOpen };
    }

    closeAllDropdowns() {
      document.querySelectorAll('.menu-entry.dropdown-open').forEach(entry => {
        this._closeDropdown(
          entry,
          entry.querySelector('.dropdown-menu'),
          entry.querySelector('.submenu-toggle-arrow')
        );
      });
    }
  }

  /* ─────────────────────────────────────────────────────────
     BOOT
  ───────────────────────────────────────────────────────── */
  function boot() {
    if (!document.getElementById('equiperSidebar')) return;
    window.equiperSidebarManager = new EquiperSidebarManager();
    window.sidebarManager = window.equiperSidebarManager; // alias
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
