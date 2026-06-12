/**
 * Sidebar Click Protection Script
 *
 * ADD THIS SCRIPT BEFORE inventory.js in your template:
 *
 * <script src="{% static 'js/sidebar-protection.js' %}"></script>
 * <script src="{% static 'Inventory/inventory.js' %}"></script>
 *
 * This ensures sidebar navigation always works, even with inventory.js event handlers
 */

(function() {
  'use strict';

  console.log('🛡️ Sidebar Protection: Initializing...');

  // Wait for DOM to be ready
  function init() {
    const sidebar = document.getElementById('appSidebar');

    if (!sidebar) {
      console.log('⚠️ Sidebar Protection: Sidebar not found on this page');
      return;
    }

    console.log('✅ Sidebar Protection: Active');

    // Intercept ALL clicks on the sidebar with high priority
    sidebar.addEventListener('click', function(e) {
      // Find if we clicked on a navigation link
      const navLink = e.target.closest('.nav-link');

      if (!navLink) {
        // Not a nav link, allow normal behavior
        return;
      }

      // Check if it's a submenu toggle (these should NOT navigate)
      const isSubmenuToggle = navLink.hasAttribute('data-toggle');

      if (isSubmenuToggle) {
        // Let the submenu script handle this
        console.log('🔽 Sidebar Protection: Submenu toggle clicked');
        return;
      }

      // It's a real navigation link
      const href = navLink.getAttribute('href');

      if (href && href !== '#' && href !== '') {
        // Stop any inventory.js handlers from interfering
        e.stopImmediatePropagation();

        console.log('🔗 Sidebar Protection: Navigating to:', href);

        // Navigate manually to be absolutely sure
        window.location.href = href;

        // Prevent any default behavior
        e.preventDefault();
      }
    }, true); // Use capture phase for highest priority

    // Also protect submenu links
    const submenuLinks = sidebar.querySelectorAll('.submenu-link, .nested-link');
    submenuLinks.forEach(link => {
      link.addEventListener('click', function(e) {
        const href = this.getAttribute('href');

        if (href && href !== '#' && href !== '') {
          e.stopImmediatePropagation();
          console.log('🔗 Sidebar Protection: Navigating to submenu link:', href);
          window.location.href = href;
          e.preventDefault();
        }
      }, true);
    });

    console.log('✅ Sidebar Protection: All navigation links protected');
  }

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
