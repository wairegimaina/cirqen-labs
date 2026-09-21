/*
 * Shared HTML escaping — loaded in the <head> of base.html and base_auth.html
 * so every page script can use it.
 *
 * Anything that came from the server or a form (equipment names, notes,
 * remarks, user names, messages) must go through escapeHTML before it is
 * placed in an innerHTML string. It escapes all five significant characters,
 * so the result is safe in element text AND inside quoted attributes
 * (data-name="${escapeHTML(x)}"), which the old textContent-based helpers
 * were not: they left " and ' untouched.
 */
(function () {
  'use strict';

  var ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

  function escapeHTML(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, function (ch) {
      return ENTITIES[ch];
    });
  }

  window.escapeHTML = escapeHTML;
  // Older page scripts call it escapeHtml.
  window.escapeHtml = escapeHTML;
})();
