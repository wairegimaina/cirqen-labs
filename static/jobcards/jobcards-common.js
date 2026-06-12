/**
 * Unified Job Card Manager
 * Handles search and stats for both Waiting and Declined views.
 */
class JobCardListManager {
  constructor() {
    this.cardItems = Array.from(document.querySelectorAll('.job-card-item'));
    this.searchInput = document.getElementById('searchInput');
    this.displayedCountEl = document.getElementById('displayedCount');
    this.resultsInfo = document.getElementById('resultsText');
    this.noResultsMsg = document.getElementById('noResultsMessage');
    this.cardsContainer = document.getElementById('jobcardsList');

    this.init();
  }

  init() {
    this.setupEventListeners();
    this.updateStats(); // Initial count
  }

  setupEventListeners() {
    this.searchInput?.addEventListener('input', () => this.handleSearch());
  }

  handleSearch() {
    const term = this.searchInput?.value.toLowerCase().trim() || "";
    let visibleCount = 0;

    this.cardItems.forEach(card => {
      const searchData = card.dataset.search || "";
      const isMatch = searchData.includes(term);

      card.style.display = isMatch ? 'block' : 'none';
      if (isMatch) visibleCount++;
    });

    this.updateUI(visibleCount, term);
  }

  updateUI(count, term) {
    // Update Stats
    if (this.displayedCountEl) this.displayedCountEl.textContent = count;

    // Toggle No Results Message
    if (count === 0 && this.cardItems.length > 0) {
      this.noResultsMsg.style.display = 'block';
      this.noResultsMsg.innerHTML = `
                <div class="py-5 text-center">
                    <i class="fas fa-search fa-3x mb-3 text-muted opacity-50"></i>
                    <h5 style="color: var(--text-primary)">No matches found</h5>
                    <p class="text-muted">No cards match "<strong>${term}</strong>"</p>
                </div>
            `;
    } else {
      this.noResultsMsg.style.display = 'none';
    }

    // Update Info Text
    if (this.resultsInfo) {
      this.resultsInfo.textContent = term
        ? `Found ${count} matches for "${term}"`
        : `Showing all ${count} records`;
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.jobCardManager = new JobCardListManager();
});
