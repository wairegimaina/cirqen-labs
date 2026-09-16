/**
 * HOD Job Card Manager
 * Handles client-side searching, filtering, and UI updates.
 * Fully adaptive to theme.css variables.
 */
class HODJobCardManager {
  constructor() {
    // Cache DOM elements
    this.searchInput = document.getElementById('searchInput');
    this.tableBody = document.getElementById('jobcardsTable');
    this.allRows = Array.from(document.querySelectorAll('.job-card-row'));
    this.noResultsMsg = document.getElementById('noResultsMessage');
    this.resultsInfo = document.getElementById('resultsText');
    this.displayedCountEl = document.getElementById('displayedCount');
    this.tableContainer = document.querySelector('.table-responsive');

    // Initialize
    this.init();
  }

  init() {
    this.setupEventListeners();
    // Run once on load to set initial stats
    this.applyLocalSearch();
  }

  setupEventListeners() {
    // Instant search with debounce for performance
    this.searchInput?.addEventListener('input', () => this.applyLocalSearch());
  }

  applyLocalSearch() {
    const term = this.searchInput?.value.toLowerCase().trim() || "";
    const visibleCount = filterElementsBySearch(this.allRows, term, '');
    this.updateUI(visibleCount, term);
  }

  updateUI(count, term) {
    // Update Stats Counter
    if (this.displayedCountEl) {
      this.displayedCountEl.textContent = count;
    }

    // Toggle Table/No Results Visibility
    if (count === 0 && this.allRows.length > 0) {
      if (this.tableContainer) this.tableContainer.classList.add('d-none');
      if (this.noResultsMsg) this.noResultsMsg.style.display = 'block';
    } else {
      if (this.tableContainer) this.tableContainer.classList.remove('d-none');
      if (this.noResultsMsg) this.noResultsMsg.style.display = 'none';
    }

    // Update Info Text
    if (this.resultsInfo) {
      this.resultsInfo.innerHTML = term
        ? `Found <strong>${count}</strong> matches for "<em>${escapeHTML(term)}</em>"`
        : `Showing <strong>${count}</strong> records`;
    }
  }
}

// Global function for Modal (can be called from HTML)
window.viewJobCardDetails = function (id) {
  const modalBody = document.getElementById('modalContent');
  const modalTitle = document.getElementById('jobcardModalLabel');

  // Set loading state
  modalTitle.textContent = `Job Card #${id}`;
  modalBody.innerHTML = `
        <div class="text-center py-5">
            <div class="spinner-border text-primary" role="status"></div>
            <p class="mt-2 text-muted">Retrieving technical details...</p>
        </div>
    `;

  // Show Modal
  const modal = new bootstrap.Modal(document.getElementById('jobcardModal'));
  modal.show();

  // Simulate AJAX Fetch (Replace with actual fetch in production)
  setTimeout(() => {
    modalBody.innerHTML = `
            <div class="alert alert-light border">
                <strong><i class="fas fa-info-circle"></i> System Note:</strong>
                Full technical logs, spare parts used, and approval timestamps would be loaded here.
            </div>
            <ul class="list-group list-group-flush">
                <li class="list-group-item bg-transparent"><strong>Technician:</strong> John Doe</li>
                <li class="list-group-item bg-transparent"><strong>Duration:</strong> 2 Hours 15 Mins</li>
                <li class="list-group-item bg-transparent"><strong>Spare Parts:</strong> Power Supply Unit (PSU-500)</li>
            </ul>
        `;
  }, 800);
};

// Initialize on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  window.hodManager = new HODJobCardManager();
});
