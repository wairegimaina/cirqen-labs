/**
 * JobCardManager - Unified Logic for Searching, Filtering, and Stats
 */
class JobCardManager {
  constructor() {
    this.allCards = Array.from(document.querySelectorAll('.job-card-item'));
    this.searchInput = document.getElementById('searchInput');
    this.datePicker = document.getElementById('dateFilter');
    this.resultsText = document.getElementById('resultsText');
    this.serverQuery = document.body.dataset.searchQuery || "";

    this.init();
  }

  init() {
    this.updateStats();
    this.updateResultsInfo();
    this.setupEventListeners();
  }

  setupEventListeners() {
    // Client-side instant search for current page
    this.searchInput?.addEventListener('input', () => this.applyLocalFilters());
  }

  applyLocalFilters() {
    const searchTerm = this.searchInput.value.toLowerCase().trim();
    const visibleCount = filterElementsBySearch(this.allCards, searchTerm, 'block');
    this.updateStats(visibleCount);
    this.updateResultsInfo(searchTerm, visibleCount);
  }

  updateStats(localVisibleCount = null) {
    const displayedElement = document.getElementById('displayedCount');
    const todayElement = document.getElementById('todayCount');

    if (displayedElement) {
      displayedElement.textContent = localVisibleCount !== null ?
        localVisibleCount : this.allCards.length;
    }

    if (todayElement) {
      const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Africa/Nairobi' });
      const todayCount = this.allCards.filter(card => card.dataset.date === today).length;
      todayElement.textContent = todayCount;
    }
  }

  updateResultsInfo(searchTerm = "", count = null) {
    if (!this.resultsText) return;
    const finalCount = count !== null ? count : this.allCards.length;

    if (searchTerm) {
      this.resultsText.textContent = `Found ${finalCount} matches for "${searchTerm}" on this page.`;
    } else {
      this.resultsText.textContent = `Showing ${finalCount} approved work orders.`;
    }
  }
}

// Global Filter Functions
function applyDateFilter() {
  const dateVal = document.getElementById("dateFilter")?.value;
  const params = new URLSearchParams(window.location.search);
  if (dateVal) params.set("date", dateVal);
  params.delete("page"); // Reset pagination
  window.location.href = `${window.location.pathname}?${params.toString()}`;
}

function clearAllFilters() {
  window.location.href = window.location.pathname;
}

document.addEventListener('DOMContentLoaded', () => {
  window.jobManager = new JobCardManager();
});
