// static/jobcards/jobcards-hod.js

class JobCardSearch {
    constructor() {
        this.searchInput = document.getElementById("searchInput");
        this.tableRows = document.querySelectorAll("#jobCardsTable .job-card-row");
        this.noResultsMessage = document.getElementById("noResultsMessage");
        this.resultsInfo = document.getElementById("resultsInfo");
        this.resultsText = document.getElementById("resultsText");
        this.displayedCount = document.getElementById("displayedCount");

        this.bindEvents();
        this.updateResultsInfo();
    }

    bindEvents() {
        if (this.searchInput) {
            this.searchInput.addEventListener("input", () => this.search());
        }
    }

    search() {
        const query = this.searchInput.value.toLowerCase().trim();
        let matchCount = 0;

        this.tableRows.forEach(row => {
            const searchableText = row.getAttribute("data-search") || "";
            const isMatch = searchableText.includes(query);

            row.style.display = isMatch ? "" : "none";
            if (isMatch) matchCount++;
        });

        this.updateResults(matchCount, query);
    }

    updateResults(matchCount, query) {
        if (this.displayedCount) {
            this.displayedCount.textContent = matchCount;
        }

        if (matchCount === 0) {
            this.noResultsMessage.style.display = "block";
            this.resultsInfo.style.display = "none";
        } else {
            this.noResultsMessage.style.display = "none";
            this.resultsInfo.style.display = "block";
            this.resultsText.textContent = query
                ? `Showing ${matchCount} results matching "${query}"`
                : `Showing ${matchCount} job cards on this page`;
        }
    }

    updateResultsInfo() {
        if (this.resultsText && this.displayedCount) {
            const count = this.displayedCount.textContent;
            this.resultsText.textContent = `Showing ${count} job cards on this page`;
        }
    }
}

// Clear search
function clearSearch() {
    const input = document.getElementById("searchInput");
    if (input) {
        input.value = "";
        new JobCardSearch().search();
    }
}

// View job card details in modal
function viewDetails(jobCardId) {
    fetch(`/jobcard/details/${jobCardId}/`)
        .then(response => response.text())
        .then(html => {
            document.getElementById("modalContent").innerHTML = html;
            new bootstrap.Modal(document.getElementById("jobCardModal")).show();
        })
        .catch(err => console.error("Error loading job card details:", err));
}

// Initialize when page loads
document.addEventListener("DOMContentLoaded", () => {
    new JobCardSearch();
});
