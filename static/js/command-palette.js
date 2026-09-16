/**
 * Global search / command palette — Ctrl+K (Cmd+K on Mac).
 * Queries dashboard:global_search and renders grouped results.
 */
(function () {
  "use strict";

  const overlay = document.getElementById("cmdkOverlay");
  if (!overlay) return; // Not authenticated / not on a page with the palette

  const input = document.getElementById("cmdkInput");
  const resultsEl = document.getElementById("cmdkResults");
  const trigger = document.getElementById("searchTrigger");

  const GROUP_LABELS = {
    equipment: "Equipment",
    jobcards: "Job Cards",
    standards: "Calibration Standards",
  };
  const GROUP_ICONS = {
    equipment: "fa-boxes",
    jobcards: "fa-clipboard-list",
    standards: "fa-flask",
  };

  let debounceTimer = null;
  let activeIndex = -1;
  let flatResults = [];

  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  function open() {
    overlay.hidden = false;
    document.body.style.overflow = "hidden";
    input.value = "";
    activeIndex = -1;
    flatResults = [];
    resultsEl.innerHTML = '<div class="cmdk-empty">Start typing to search — equipment, job cards, and calibration standards.</div>';
    setTimeout(() => input.focus(), 0);
  }

  function close() {
    overlay.hidden = true;
    document.body.style.overflow = "";
  }

  function renderResults(results) {
    flatResults = [];
    const groups = Object.keys(results).filter((key) => results[key].length);

    if (!groups.length) {
      resultsEl.innerHTML = '<div class="cmdk-empty">No matches found.</div>';
      return;
    }

    resultsEl.innerHTML = groups
      .map((group) => {
        const items = results[group]
          .map((item) => {
            const idx = flatResults.length;
            flatResults.push(item);
            return `
              <a href="${escapeHTML(item.url)}" class="cmdk-result" data-index="${idx}">
                <div class="cmdk-result-title">${escapeHTML(item.title)}</div>
                ${item.subtitle ? `<div class="cmdk-result-subtitle">${escapeHTML(item.subtitle)}</div>` : ""}
              </a>`;
          })
          .join("");
        return `
          <div class="cmdk-group">
            <div class="cmdk-group-label"><i class="fas ${escapeHTML(GROUP_ICONS[group] || "fa-circle")}" aria-hidden="true"></i> ${escapeHTML(GROUP_LABELS[group] || group)}</div>
            ${items}
          </div>`;
      })
      .join("");
  }

  function runSearch(query) {
    if (query.length < 2) {
      resultsEl.innerHTML = '<div class="cmdk-empty">Keep typing — need at least 2 characters.</div>';
      return;
    }
    fetch(`/dashboard/search/?q=${encodeURIComponent(query)}`, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((res) => res.json())
      .then((data) => renderResults(data.results || {}))
      .catch(() => {
        resultsEl.innerHTML = '<div class="cmdk-empty">Search failed. Try again.</div>';
      });
  }

  function setActive(index) {
    const items = resultsEl.querySelectorAll(".cmdk-result");
    items.forEach((el) => el.classList.remove("is-active"));
    if (index >= 0 && items[index]) {
      items[index].classList.add("is-active");
      items[index].scrollIntoView({ block: "nearest" });
    }
    activeIndex = index;
  }

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const query = input.value.trim();
    debounceTimer = setTimeout(() => runSearch(query), 200);
  });

  input.addEventListener("keydown", (e) => {
    const items = resultsEl.querySelectorAll(".cmdk-result");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(Math.min(activeIndex + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(Math.max(activeIndex - 1, 0));
    } else if (e.key === "Enter") {
      if (activeIndex >= 0 && flatResults[activeIndex]) {
        window.location.href = flatResults[activeIndex].url;
      } else if (items.length) {
        window.location.href = items[0].getAttribute("href");
      }
    }
  });

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  document.addEventListener("keydown", (e) => {
    const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
    if (isCmdK) {
      e.preventDefault();
      overlay.hidden ? open() : close();
    } else if (e.key === "Escape" && !overlay.hidden) {
      close();
    }
  });

  trigger?.addEventListener("click", open);
})();
