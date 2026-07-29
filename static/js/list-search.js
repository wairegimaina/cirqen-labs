/**
 * Shared list/table search-filter primitive.
 *
 * Extracted from 3 near-identical implementations that had each grown
 * their own copy of the same "does this element's data-search match the
 * typed term" loop: jobcards-common.js (JobCardListManager),
 * jobcard-approved.js (JobCardManager), and hod-jobcards.js
 * (HODJobCardManager). Each of those files keeps its own page-specific
 * orchestration (stats, date filters, results text) — only the actual
 * matching/toggling primitive moved here, since that part was byte-for-
 * byte identical across all three.
 *
 * Usage:
 *   const visibleCount = filterElementsBySearch(elements, term);
 *   // elements: NodeList/Array of elements with a data-search attribute
 *   // term:     the raw search input value (case-insensitivity handled here)
 *   // returns:  number of elements left visible
 */
function filterElementsBySearch(elements, term, displayValue) {
  const needle = (term || "").toLowerCase().trim();
  const shown = displayValue === undefined ? "" : displayValue;
  let visibleCount = 0;

  Array.from(elements).forEach((el) => {
    const haystack = (el.dataset.search || "").toLowerCase();
    const isMatch = haystack.includes(needle);
    el.style.display = isMatch ? shown : "none";
    if (isMatch) visibleCount++;
  });

  return visibleCount;
}
