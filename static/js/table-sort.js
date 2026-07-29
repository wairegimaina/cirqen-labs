/**
 * Generic click-to-sort table headers.
 *
 * Opt-in, additive — a table gets sorting by adding data-sort="text" (or
 * "number" / "date") to whichever <th> elements should be sortable.
 * No other markup change needed; works against whatever <tbody> rows
 * are already there (including rows added later, e.g. by search-filter
 * JS, since sorting re-reads the DOM each click rather than caching rows).
 *
 * Example:
 *   <table>
 *     <thead><tr>
 *       <th data-sort="text">Description</th>
 *       <th data-sort="number">Qty</th>
 *       <th data-sort="date">Date Issued</th>
 *       <th>Actions</th>  <!-- no data-sort = not sortable -->
 *     </tr></thead>
 *     ...
 *
 * A cell can override what gets compared via data-sort-value, useful when
 * the displayed text isn't directly sortable (e.g. a formatted date or a
 * badge whose text alone doesn't reflect priority order):
 *   <td data-sort-value="2026-07-25">Jul 25, 2026</td>
 */
(function () {
  function cellValue(row, columnIndex, type) {
    const cell = row.children[columnIndex];
    if (!cell) return "";
    const raw = cell.dataset.sortValue !== undefined ? cell.dataset.sortValue : cell.textContent;
    const trimmed = raw.trim();
    if (type === "number") return parseFloat(trimmed.replace(/[^0-9.-]/g, "")) || 0;
    if (type === "date") return new Date(trimmed).getTime() || 0;
    return trimmed.toLowerCase();
  }

  function sortTable(table, th) {
    const type = th.dataset.sort;
    const headerRow = th.parentElement;
    const columnIndex = Array.from(headerRow.children).indexOf(th);
    const tbody = table.tBodies[0];
    if (!tbody) return;

    const ascending = th.getAttribute("aria-sort") !== "ascending";

    headerRow.querySelectorAll("[data-sort]").forEach((h) => h.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", ascending ? "ascending" : "descending");

    const rows = Array.from(tbody.querySelectorAll("tr"));
    rows.sort((a, b) => {
      const va = cellValue(a, columnIndex, type);
      const vb = cellValue(b, columnIndex, type);
      if (va < vb) return ascending ? -1 : 1;
      if (va > vb) return ascending ? 1 : -1;
      return 0;
    });
    rows.forEach((row) => tbody.appendChild(row));
  }

  function initSortableTables(root) {
    (root || document).querySelectorAll("table").forEach((table) => {
      table.querySelectorAll("th[data-sort]").forEach((th) => {
        if (th.dataset.sortBound) return; // avoid double-binding on re-init
        th.dataset.sortBound = "1";
        th.style.cursor = "pointer";
        th.setAttribute("role", "button");
        th.setAttribute("tabindex", "0");
        th.addEventListener("click", () => sortTable(table, th));
        th.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            sortTable(table, th);
          }
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => initSortableTables());
  window.initSortableTables = initSortableTables; // callable again after AJAX-loaded rows
})();
