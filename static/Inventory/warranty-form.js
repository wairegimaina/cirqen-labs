/* Warranty form fields (templates/Inventory/_warranty_fields.html).
 *
 * Picking a supplier shows its phone / email from the Supplier record and, when
 * the fields are still empty, fills its standard period and terms. The expiry
 * hint shows start date + period so the user sees the date that will be saved.
 */
(function () {
  'use strict';

  function addMonths(isoDate, months) {
    const [y, m, d] = isoDate.split('-').map(Number);
    const target = new Date(Date.UTC(y, m - 1 + months, 1));
    const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
    target.setUTCDate(Math.min(d, lastDay));
    return target.toISOString().slice(0, 10);
  }

  function formatDate(iso) {
    return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
    });
  }

  function setup(root) {
    const role = (name) => root.querySelector(`[data-role="${name}"]`);
    const supplier = role('supplier');
    const contact = role('supplier-contact');
    const defaultContact = contact.innerHTML;
    const start = role('start');
    const months = role('months');
    const expiry = role('expiry');
    const hint = role('expiry-hint');
    const defaultHint = hint.textContent;

    function showSupplier(prefill) {
      const opt = supplier.selectedOptions[0];
      if (!opt || !opt.value) {
        contact.innerHTML = defaultContact;
        return;
      }
      const parts = [];
      if (opt.dataset.contact) parts.push(`<i class="fas fa-user me-1"></i>${escapeHTML(opt.dataset.contact)}`);
      parts.push(`<i class="fas fa-phone me-1"></i>${escapeHTML(opt.dataset.phone || 'No phone on record')}`);
      if (opt.dataset.email) parts.push(`<i class="fas fa-envelope me-1"></i>${escapeHTML(opt.dataset.email)}`);
      contact.innerHTML = parts.join('<span class="mx-2">·</span>');
      if (prefill) {
        if (!months.value && !expiry.value && opt.dataset.months) months.value = opt.dataset.months;
        const terms = role('terms');
        if (terms && !terms.value && opt.dataset.terms) terms.value = opt.dataset.terms;
        updateHint();
      }
    }

    function updateHint() {
      const n = parseInt(months.value, 10);
      if (start.value && n > 0 && !expiry.value) {
        hint.textContent = `Expires ${formatDate(addMonths(start.value, n))} (start date + ${n} months).`;
      } else if (start.value && expiry.value && expiry.value < start.value) {
        hint.textContent = 'Expiry date is before the start date.';
      } else {
        hint.textContent = defaultHint;
      }
    }

    supplier.addEventListener('change', () => showSupplier(true));
    [start, months, expiry].forEach((el) => el.addEventListener('input', updateHint));
    showSupplier(false);
    updateHint();
  }

  document.querySelectorAll('[data-warranty-fields]').forEach(setup);
})();
