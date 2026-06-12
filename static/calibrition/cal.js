// ==========================================
// CALIBRATION SCHEDULES — JavaScript
// Version: 2.0 — Fully Standalone
// All logic lives here. No inline <script> in HTML.
// Django template vars injected once via window.CalibrationConfig
// and window.CalibrationURLs (set in a tiny <script> in the template).
// ==========================================

// ============================================
// DEPARTMENT DROPDOWN
// Functions called via onclick/oninput in HTML
// ============================================

window.toggleCalDeptDropdown = function (e) {
  e.stopPropagation();
  var dd = document.getElementById("calDeptDropdown");
  if (!dd) return;
  var btn = document.getElementById("calDeptDropdownBtn");
  var opening = !dd.classList.contains("open");
  dd.classList.toggle("open", opening);
  if (btn) btn.setAttribute("aria-expanded", opening ? "true" : "false");
  if (opening) {
    setTimeout(function () {
      var inp = document.getElementById("calDeptSearchInput");
      if (inp) inp.focus();
    }, 60);
  }
};

window.filterCalDepts = function (q) {
  var items = document.querySelectorAll("#calDeptList .cal-dept-item");
  var term = q.toLowerCase().trim();
  var anyVisible = false;

  items.forEach(function (item) {
    var name = item.dataset.name || "";
    var visible = !term || name.includes(term);
    item.style.display = visible ? "" : "none";
    if (visible) anyVisible = true;
  });

  var noRes = document.getElementById("calDeptNoResults");
  if (!anyVisible) {
    if (!noRes) {
      noRes = document.createElement("div");
      noRes.id = "calDeptNoResults";
      noRes.className = "cal-dept-no-results";
      noRes.innerHTML =
        '<i class="fas fa-search me-2 opacity-50"></i>No departments found';
      document.getElementById("calDeptList").appendChild(noRes);
    }
  } else if (noRes) {
    noRes.remove();
  }
};

// Close dropdown on outside click
document.addEventListener("click", function (e) {
  var dd = document.getElementById("calDeptDropdown");
  if (dd && !dd.contains(e.target)) {
    dd.classList.remove("open");
    var btn = document.getElementById("calDeptDropdownBtn");
    if (btn) btn.setAttribute("aria-expanded", "false");
  }
});

// Close on Escape
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") {
    var dd = document.getElementById("calDeptDropdown");
    if (dd) {
      dd.classList.remove("open");
      var btn = document.getElementById("calDeptDropdownBtn");
      if (btn) btn.setAttribute("aria-expanded", "false");
    }
  }
});

// ============================================
// MAIN INIT — runs after DOM is ready
// ============================================

document.addEventListener("DOMContentLoaded", function () {
  // Sidebar toggle
  var sidebarBtn = document.getElementById("btn");
  if (sidebarBtn) {
    sidebarBtn.addEventListener("click", function () {
      var sb = document.querySelector(".sidebar");
      if (sb) sb.classList.toggle("active");
    });
  }

  populateYearFilter();
  setupMonthYearNavigation();
  setupShowAllBtn();
  setupClearFilter();
  setupPlanningModal();
  handleScheduledCheckboxes();
  handleUnscheduledCheckboxes();
  handleActionButtons();
  handleBulkActions();
  handleSearch();
  initTooltips();
  highlightOverdueRows();
  startOverduePoller();
  checkWaitingGroups();
  setupSubTabs();
});

// ============================================
// YEAR FILTER — populate dynamically
// ============================================

function populateYearFilter() {
  var $year = document.getElementById("yearFilter");
  if (!$year) return;

  var cfg = window.CalibrationConfig || {};
  var currentYear = parseInt(cfg.currentYear) || new Date().getFullYear();
  var selectedYear = parseInt(cfg.selectedYear) || currentYear;

  for (var y = currentYear - 2; y <= currentYear + 3; y++) {
    var opt = document.createElement("option");
    opt.value = y;
    opt.textContent = y;
    if (y === selectedYear) opt.selected = true;
    $year.appendChild(opt);
  }
}

// ============================================
// MONTH / YEAR NAVIGATION
// Changing either dropdown navigates immediately,
// preserving all other active query params.
// ============================================

function setupMonthYearNavigation() {
  var monthSel = document.getElementById("monthFilter");
  var yearSel = document.getElementById("yearFilter");
  if (!monthSel || !yearSel) return;

  function navigate() {
    var url = buildUrl({
      month: monthSel.value,
      year: yearSel.value,
      page: null,
      unscheduled_page: null,
    });
    window.location.href = url;
  }

  monthSel.addEventListener("change", navigate);
  yearSel.addEventListener("change", navigate);
}

// ============================================
// GO TO CURRENT MONTH (called from onclick in HTML)
// ============================================

window.goToCurrentMonth = function () {
  var cfg = window.CalibrationConfig || {};
  var url = buildUrl({
    month: cfg.currentMonth || new Date().getMonth() + 1,
    year: cfg.currentYear || new Date().getFullYear(),
    page: null,
    unscheduled_page: null,
  });
  window.location.href = url;
};

// ============================================
// SHOW ALL — remove date filter, keep everything else
// ============================================

function setupShowAllBtn() {
  var btn = document.getElementById("showAllBtn");
  if (!btn) return;
  btn.addEventListener("click", function () {
    var url = buildUrl({
      month: null,
      year: null,
      page: null,
      unscheduled_page: null,
    });
    window.location.href = url;
  });
}

// ============================================
// CLEAR FILTER
// Strips department param, keeps month/year/search.
// Navigates to the base dashboard URL stored in
// window.CalibrationURLs.dashboard (set in HTML).
// ============================================

function setupClearFilter() {
  var btn = document.getElementById("clearFilterBtn");
  if (!btn) return;
  btn.addEventListener("click", function (e) {
    e.preventDefault();
    var base =
      (window.CalibrationURLs && window.CalibrationURLs.dashboard) ||
      window.location.pathname.replace(/\/department\/[^/]+\/?$/, "/");
    var url = buildUrl(
      { department: null, page: null, unscheduled_page: null },
      base,
    );
    window.location.href = url;
  });
}

// ============================================
// buildUrl — helper
// Merges a patch object into the current query string.
// Pass null for a key to remove it.
// Optionally provide a different base path.
// ============================================

function buildUrl(patch, basePath) {
  var params = new URLSearchParams(window.location.search);
  Object.keys(patch).forEach(function (key) {
    if (patch[key] === null || patch[key] === undefined || patch[key] === "") {
      params.delete(key);
    } else {
      params.set(key, patch[key]);
    }
  });
  var path = basePath || window.location.pathname;
  var qs = params.toString();
  return qs ? path + "?" + qs : path;
}

// ============================================
// PLANNING MODAL — card selection + logic-change warning
// ============================================

// Human-readable labels for each logic value
var LOGIC_LABELS = {
  department: "Department-Based",
  date_based: "Department-Based",
  description: "Description-Based",
  description_based: "Description-Based",
  "": "Not Set",
};

function getLogicLabel(val) {
  return LOGIC_LABELS[val] || val || "Unknown";
}

// Normalise for equivalence comparison
function normaliseLogic(v) {
  if (v === "department" || v === "date_based") return "department_group";
  if (v === "description" || v === "description_based")
    return "description_group";
  return v || "";
}

function setupPlanningModal() {
  var cards = document.querySelectorAll(".planning-card");
  var logicInput = document.getElementById("planningLogic");

  // ── Read the active server logic from the hidden element the template sets ──
  var currentLogicEl = document.getElementById("currentActivePlanningLogic");
  var currentLogic = currentLogicEl ? (currentLogicEl.value || "").trim() : "";

  // ── Warning UI elements ──
  var warningBox = document.getElementById("logicChangeInlineWarning");
  var warnFrom = document.getElementById("warnFromLogic");
  var warnTo = document.getElementById("warnToLogic");
  var initBtn = document.getElementById("initializeBtn");

  // ── Special-class section visibility ──
  var specialDeptSection = document.getElementById("specialDepartmentSection");
  var specialDescSection = document.getElementById("specialDescriptionSection");

  // ── Description search inside modal ──
  var descSearch = document.getElementById("descriptionSearchInput");
  if (descSearch) {
    descSearch.addEventListener("input", function () {
      var term = this.value.toLowerCase();
      document
        .querySelectorAll(".description-checkbox-item")
        .forEach(function (item) {
          var label = item.querySelector("label");
          var text = label ? label.textContent.toLowerCase() : "";
          item.style.display = text.includes(term) ? "" : "none";
        });
    });
  }

  // ── Core: show/hide the logic-change warning ──
  function checkAndWarn(selectedLogic) {
    if (!warningBox) return;

    // No existing logic → nothing to warn about
    if (!currentLogic) {
      warningBox.classList.add("d-none");
      resetInitBtn();
      return;
    }

    var isChange =
      normaliseLogic(selectedLogic) !== normaliseLogic(currentLogic);

    if (isChange) {
      if (warnFrom) warnFrom.textContent = getLogicLabel(currentLogic);
      if (warnTo) warnTo.textContent = getLogicLabel(selectedLogic);
      warningBox.classList.remove("d-none");
      if (initBtn) {
        initBtn.classList.remove("btn-primary");
        initBtn.classList.add("btn-danger");
        initBtn.innerHTML =
          '<i class="fas fa-exclamation-triangle me-1"></i>Initialize & Reorganize (Logic Change)';
      }
    } else {
      warningBox.classList.add("d-none");
      resetInitBtn();
    }
  }

  function resetInitBtn() {
    if (!initBtn) return;
    initBtn.classList.remove("btn-danger");
    initBtn.classList.add("btn-primary");
    initBtn.innerHTML =
      '<i class="fas fa-play-circle me-1"></i>Initialize & Normalize Schedules';
  }

  // ── Update special-class section visibility based on selected logic ──
  function updateSpecialSection(logic) {
    var isDept = normaliseLogic(logic) === "department_group";
    if (specialDeptSection)
      specialDeptSection.style.display = isDept ? "" : "none";
    if (specialDescSection)
      specialDescSection.style.display = isDept ? "none" : "";
  }

  // ── Select a card programmatically ──
  function selectCard(logic) {
    cards.forEach(function (c) {
      c.classList.remove("selected");
    });
    cards.forEach(function (c) {
      if (c.dataset.logic === logic) c.classList.add("selected");
    });
    if (logicInput) logicInput.value = logic;
    checkAndWarn(logic);
    updateSpecialSection(logic);
  }

  // ── Card click handler ──
  cards.forEach(function (card) {
    card.addEventListener("click", function () {
      selectCard(card.dataset.logic);
    });
  });

  // ── On modal open: restore the correct selected state ──
  var modalEl = document.getElementById("calibrationPlanningModal");
  if (modalEl) {
    modalEl.addEventListener("show.bs.modal", function () {
      // Pick whichever card matches the current server logic (or default first card)
      var matched = false;
      cards.forEach(function (c) {
        if (normaliseLogic(c.dataset.logic) === normaliseLogic(currentLogic)) {
          selectCard(c.dataset.logic);
          matched = true;
        }
      });
      if (!matched && cards.length > 0) {
        selectCard(cards[0].dataset.logic);
      }
    });
  }

  // ── Smart Reorganizer modal: pre-select the correct radio ──
  var smartReorgModal = document.getElementById("smartReorgModal");
  if (smartReorgModal) {
    smartReorgModal.addEventListener("show.bs.modal", function () {
      // Pre-tick the radio that matches the current active logic
      var radios = smartReorgModal.querySelectorAll(
        'input[name="new_planning_logic"]',
      );
      radios.forEach(function (r) {
        r.checked = normaliseLogic(r.value) === normaliseLogic(currentLogic);
      });
    });
  }

  // ── Period buttons ──
  var periodButtons = document.querySelectorAll(".period-btn");
  periodButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      periodButtons.forEach(function (btn) {
        btn.classList.remove("btn-primary", "active");
        btn.classList.add("btn-outline-primary");
      });
      button.classList.remove("btn-outline-primary");
      button.classList.add("btn-primary", "active");
      var periodInput = document.getElementById("calibrationPeriod");
      if (periodInput) periodInput.value = button.dataset.period;
    });
  });

  // ── Loading overlay on form submit ──
  var initForm = document.getElementById("calibrationInitForm");
  if (initForm) {
    initForm.addEventListener("submit", function () {
      showLoadingOverlay();
    });
  }

  // ── Smart Reorganizer form submit feedback ──
  var smartReorgForm = document.getElementById("smartReorgForm");
  var smartReorgBtn = document.getElementById("smartReorgBtn");
  if (smartReorgForm && smartReorgBtn) {
    smartReorgForm.addEventListener("submit", function () {
      var isDryRun = smartReorgForm.querySelector("#reorgDryRun");
      smartReorgBtn.disabled = true;
      if (isDryRun && isDryRun.checked) {
        smartReorgBtn.innerHTML =
          '<i class="fas fa-spinner fa-spin me-1"></i>Running Dry Run…';
      } else {
        smartReorgBtn.innerHTML =
          '<i class="fas fa-spinner fa-spin me-1"></i>Reorganizing…';
      }
    });
  }

  // ── Logic-change warning banner dismiss ──
  var bannerDismissBtn = document.querySelector(
    "#logicChangeWarningBanner .btn-outline-secondary",
  );
  if (bannerDismissBtn) {
    bannerDismissBtn.addEventListener("click", function () {
      var banner = document.getElementById("logicChangeWarningBanner");
      if (banner) banner.style.display = "none";
    });
  }
}

// ============================================
// LOADING OVERLAY
// ============================================

function showLoadingOverlay() {
  var overlay = document.getElementById("loadingOverlay");
  if (!overlay) return;
  overlay.style.display = "flex";

  var progress = 0;
  var bar = document.getElementById("initProgress");
  var msg = document.getElementById("statusMessage");

  var interval = setInterval(function () {
    progress += Math.random() * 12;
    if (progress >= 100) {
      progress = 100;
      clearInterval(interval);
    }
    if (bar) bar.style.width = progress + "%";
    if (msg) {
      if (progress < 30) msg.textContent = "Analyzing equipment...";
      else if (progress < 60) msg.textContent = "Creating schedules...";
      else if (progress < 90) msg.textContent = "Finalizing...";
      else msg.textContent = "Complete!";
    }
  }, 250);
}

// ============================================
// SCHEDULED CHECKBOXES + BULK BUTTON STATES
// ============================================

function handleScheduledCheckboxes() {
  var selectAll = document.getElementById("selectAll");
  var checkboxes = document.querySelectorAll(".schedule-checkbox");
  var bulkDelete = document.getElementById("bulkDeleteBtn");
  var bulkPush = document.getElementById("bulkPushSchedules");
  var bulkComplete = document.getElementById("bulkCompleteBtn");
  var bulkBar = document.getElementById("bulkActionsBar");

  function updateButtons() {
    var checked = document.querySelectorAll(".schedule-checkbox:checked");
    var hasChecked = checked.length > 0;
    var n = checked.length;

    if (bulkDelete) {
      bulkDelete.disabled = !hasChecked;
    }
    if (bulkPush) {
      bulkPush.disabled = !hasChecked;
      if (hasChecked)
        bulkPush.innerHTML =
          '<i class="fas fa-arrow-right me-1"></i>Push (' + n + ")";
      else bulkPush.innerHTML = '<i class="fas fa-arrow-right me-1"></i>Push';
    }
    if (bulkComplete) {
      bulkComplete.disabled = !hasChecked;
      if (hasChecked)
        bulkComplete.innerHTML =
          '<i class="fas fa-check me-1"></i>Complete (' + n + ")";
      else bulkComplete.innerHTML = '<i class="fas fa-check me-1"></i>Complete';
    }
    if (bulkBar) {
      bulkBar.style.display = hasChecked ? "flex" : "none";
    }

    window.selectedSchedules = Array.from(checked).map(function (cb) {
      return cb.value;
    });
  }

  if (selectAll) {
    selectAll.addEventListener("change", function () {
      checkboxes.forEach(function (cb) {
        cb.checked = selectAll.checked;
      });
      updateButtons();
    });
  }
  checkboxes.forEach(function (cb) {
    cb.addEventListener("change", updateButtons);
  });
  updateButtons();
}

// ============================================
// UNSCHEDULED CHECKBOXES
// ============================================

function handleUnscheduledCheckboxes() {
  var selectAll = document.getElementById("selectAllUnscheduled");
  var checkboxes = document.querySelectorAll(".unscheduled-checkbox");
  var scheduleBtn = document.getElementById("scheduleSelectedBtn");

  function updateButtons() {
    var checked = document.querySelectorAll(".unscheduled-checkbox:checked");
    var hasChecked = checked.length > 0;
    if (scheduleBtn) {
      scheduleBtn.disabled = !hasChecked;
      if (hasChecked) {
        scheduleBtn.innerHTML =
          '<i class="fas fa-calendar-plus me-1"></i>Schedule Selected (' +
          checked.length +
          ")";
      } else {
        scheduleBtn.innerHTML =
          '<i class="fas fa-calendar-plus me-1"></i>Schedule Selected';
      }
    }
    window.selectedUnscheduled = Array.from(checked).map(function (cb) {
      return cb.value;
    });
  }

  if (selectAll) {
    selectAll.addEventListener("change", function () {
      checkboxes.forEach(function (cb) {
        cb.checked = selectAll.checked;
      });
      updateButtons();
    });
  }
  checkboxes.forEach(function (cb) {
    cb.addEventListener("change", updateButtons);
  });
  updateButtons();
}

// ============================================
// SINGLE EQUIPMENT SCHEDULE BUTTONS
// ============================================

function handleActionButtons() {
  document.querySelectorAll(".schedule-single-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var equipmentId = btn.dataset.equipmentId;
      if (!confirm("Schedule this equipment for calibration?")) return;

      var base =
        (window.CalibrationURLs && window.CalibrationURLs.dashboard) ||
        window.location.pathname.replace(/\/department\/[^/]+\/?$/, "/");
      var url = base.replace(/\/+$/, "") + "/schedule/" + equipmentId + "/";
      var form = document.createElement("form");
      form.method = "POST";
      form.action = url;
      form.innerHTML =
        '<input type="hidden" name="csrfmiddlewaretoken" value="' +
        getCsrfToken() +
        '">';
      document.body.appendChild(form);
      form.submit();
    });
  });
}

// ============================================
// BULK ACTIONS
// URLs resolved on the server and injected via
// window.CalibrationURLs (set in the HTML <script> block).
// ============================================

function handleBulkActions() {
  var urls = window.CalibrationURLs || {};

  var actions = [
    { id: "bulkDeleteBtn", url: urls.bulkDelete, label: "delete" },
    { id: "bulkPushSchedules", url: urls.bulkPush, label: "push" },
    { id: "bulkCompleteBtn", url: urls.bulkComplete, label: "complete" },
  ];

  actions.forEach(function (action) {
    var btn = document.getElementById(action.id);
    if (!btn) return;
    btn.addEventListener("click", function () {
      var selected = window.selectedSchedules || [];
      if (selected.length === 0) {
        alert("Please select at least one schedule.");
        return;
      }
      if (
        !confirm(
          "Perform " +
            action.label +
            " on " +
            selected.length +
            " schedule(s)?",
        )
      )
        return;

      var form = document.createElement("form");
      form.method = "POST";
      form.action = action.url;
      form.innerHTML =
        '<input type="hidden" name="csrfmiddlewaretoken" value="' +
        getCsrfToken() +
        '">';
      selected.forEach(function (id) {
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = "schedule_ids";
        input.value = id;
        form.appendChild(input);
      });
      document.body.appendChild(form);
      form.submit();
    });
  });

  // Schedule selected unscheduled equipment
  var scheduleSelectedBtn = document.getElementById("scheduleSelectedBtn");
  if (scheduleSelectedBtn) {
    scheduleSelectedBtn.addEventListener("click", function () {
      var selected = window.selectedUnscheduled || [];
      if (selected.length === 0) {
        alert("Please select at least one equipment.");
        return;
      }
      var form = document.getElementById("unscheduledForm");
      if (form) form.submit();
    });
  }
}

// ============================================
// SEARCH — client-side row filter
// ============================================

function handleSearch() {
  var searchInput = document.getElementById("searchInput");
  if (!searchInput) return;

  // Client-side filtering for immediate feedback
  searchInput.addEventListener("input", function () {
    var term = this.value.toLowerCase();
    ["#schedulesTable", "#unscheduledTable"].forEach(function (tableId) {
      var rows = document.querySelectorAll(tableId + " .equipment-row");
      var anyVisible = false;
      rows.forEach(function (row) {
        var text = Array.from(row.cells)
          .map(function (c) {
            return c.textContent.toLowerCase();
          })
          .join(" ");
        var show = text.includes(term);
        row.style.display = show ? "" : "none";
        if (show) anyVisible = true;
      });
      // Show empty state if needed
      var table = document.querySelector(tableId);
      if (table) {
        var existing = table.querySelector(".js-no-results");
        if (!anyVisible && term.length > 0) {
          if (!existing) {
            var tr = document.createElement("tr");
            tr.className = "js-no-results";
            var td = document.createElement("td");
            td.colSpan = 10;
            td.className = "text-center py-4 text-muted";
            td.innerHTML =
              '<i class="fas fa-search fa-2x mb-2 d-block opacity-50"></i>No results for "' +
              term +
              '"';
            tr.appendChild(td);
            var tbody = table.querySelector("tbody");
            if (tbody) tbody.appendChild(tr);
          }
        } else if (existing) {
          existing.remove();
        }
      }
    });
  });

  // Server-side search on Enter or when field is cleared
  searchInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      var term = this.value.trim();
      var url = buildUrl({
        search: term || null,
        page: null,
        unscheduled_page: null,
      });
      window.location.href = url;
    }
  });

  // If the user clears a pre-filled server search, reload without the param
  searchInput.addEventListener("change", function () {
    var serverSearch = this.dataset.serverSearch || "";
    if (serverSearch && this.value.trim() === "") {
      var url = buildUrl({ search: null, page: null, unscheduled_page: null });
      window.location.href = url;
    }
  });
}

// ============================================
// TOOLTIPS
// ============================================

function initTooltips() {
  if (typeof bootstrap !== "undefined" && bootstrap.Tooltip) {
    document
      .querySelectorAll('[data-bs-toggle="tooltip"]')
      .forEach(function (el) {
        new bootstrap.Tooltip(el);
      });
  }
}

// ============================================
// OVERDUE ROW HIGHLIGHT
// ============================================

function highlightOverdueRows() {
  var rows = document.querySelectorAll("tr.table-danger");
  if (rows.length > 0) {
    console.log("Found " + rows.length + " overdue calibration(s).");
  }
}

// ============================================
// OVERDUE STATUS POLLER — every 5 minutes
// URL from window.CalibrationURLs.overdueStatus
// ============================================

function startOverduePoller() {
  var urls = window.CalibrationURLs || {};
  if (!urls.overdueStatus) return;

  setInterval(function () {
    fetch(urls.overdueStatus)
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.overdue_count > 0 || data.warning_count > 0) {
          console.log(
            "Overdue:",
            data.overdue_count,
            "Warning:",
            data.warning_count,
          );
        }
      })
      .catch(function (err) {
        console.error("Overdue poll error:", err);
      });
  }, 300000);
}

// ============================================
// WAITING GROUPS CHECK
// ============================================

function checkWaitingGroups() {
  var urls = window.CalibrationURLs || {};
  if (!urls.waitingGroups) return;

  fetch(urls.waitingGroups)
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      if (data.waiting_count > 0) {
        console.log(data.waiting_count + " group(s) waiting for completion.");
      }
    })
    .catch(function (err) {
      console.error("Waiting groups error:", err);
    });
}

// ============================================
// SUB-TABS  (Pending / Completed / Unscheduled / Last 30 Days)
// ============================================

var SubTab = (function () {
  var urls = window.CalibrationURLs || {};
  var activeTab = null;

  function init() {
    var tabs = document.querySelectorAll("#schedulesSubTabs .cs-tab");
    if (!tabs.length) return;

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        switchTab(tab, tabs);
      });
    });

    // Auto-load completed/unscheduled badge counts on init (pending is server-rendered)
    ["completed", "unscheduled"].forEach(function (name) {
      var tab = document.querySelector(
        '#schedulesSubTabs .cs-tab[data-subtab="' + name + '"]',
      );
      if (tab) loadBadgeOnly(tab);
    });
  }

  function switchTab(selected, allTabs) {
    allTabs.forEach(function (t) {
      t.classList.remove("active");
    });
    selected.classList.add("active");

    var subtab = selected.dataset.subtab;

    // Show correct panel (reports has no AJAX, just reveal)
    var panels = document.querySelectorAll(".cs-panel");
    panels.forEach(function (p) {
      p.classList.remove("active");
    });
    var panelId = subtab + "Panel";
    var panel = document.getElementById(panelId);
    if (panel) panel.classList.add("active");

    // Reports tab has no AJAX
    if (subtab === "reports") return;

    loadTab(selected, allTabs);
  }

  // Load badge count only (no panel switch)
  function loadBadgeOnly(tab) {
    var endpoint = tab.dataset.endpoint;
    var paramsStr = tab.dataset.params || "";
    var url = urls[endpoint];
    if (!url) return;
    var sep = url.indexOf("?") === -1 ? "?" : "&";
    fetch(url + sep + (paramsStr || ""))
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var subtab = tab.dataset.subtab;
        var badgeId = "badge-" + subtab;
        var el = document.getElementById(badgeId);
        if (el && data.meta) el.textContent = data.meta.count || 0;
        if (el && !data.meta && Array.isArray(data.results))
          el.textContent = data.results.length;
      })
      .catch(function () {});
  }

  function loadTab(tab, allTabs) {
    var endpoint = tab.dataset.endpoint;
    var paramsStr = tab.dataset.params || "";
    var urlKey = tab.dataset.urlKey;

    var url = urls[urlKey] || urls[endpoint];
    if (!url) return;

    var separator = url.indexOf("?") === -1 ? "?" : "&";
    var fullUrl = url + separator + paramsStr;

    // Choose target container
    var subtab = tab.dataset.subtab;
    var tableBody, countEl, paginationEl;

    if (subtab === "pending") {
      tableBody = document.getElementById("pendingTableBody");
      countEl = document.getElementById("badge-pending");
      paginationEl = null;
    } else if (subtab === "completed") {
      tableBody = document.getElementById("completedTableBody");
      countEl = document.getElementById("badge-completed");
      paginationEl = document.getElementById("completedPagination");
    } else if (subtab === "unscheduled") {
      tableBody = document.getElementById("unscheduledTableBody");
      countEl = document.getElementById("badge-unscheduled");
      paginationEl = document.getElementById("unscheduledPagination");
    } else {
      return;
    }

    if (!tableBody) return;

    // Show loading state
    var loadColspan = subtab === "pending" ? 8 : 6;
    tableBody.innerHTML =
      '<tr><td colspan="' +
      loadColspan +
      '" class="text-center py-4"><i class="fas fa-spinner fa-spin"></i> Loading...</td></tr>';

    fetch(fullUrl)
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        renderTable(
          subtab,
          tableBody,
          data.results || [],
          paginationEl,
          data.meta,
        );
        if (countEl && data.meta) countEl.textContent = data.meta.count || 0;
        if (countEl && !data.meta && Array.isArray(data.results))
          countEl.textContent = data.results.length;
      })
      .catch(function (err) {
        var errColspan = subtab === "pending" ? 8 : 6;
        tableBody.innerHTML =
          '<tr><td colspan="' +
          errColspan +
          '" class="text-center py-4 text-danger">Failed to load: ' +
          err.message +
          "</td></tr>";
      });
  }

  function renderTable(tab, tbody, rows, paginationEl, meta) {
    tbody.innerHTML = "";

    if (!rows.length) {
      var emptyColspan = tab === "pending" ? 8 : 6;
      tbody.innerHTML =
        '<tr><td colspan="' +
        emptyColspan +
        '" class="text-center py-4 text-muted">No records found</td></tr>';
      return;
    }

    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      var s = row.schedule || row;
      if (tab === "pending" || tab === "last30") {
        var overdueClass = row.is_overdue ? "table-danger" : "";
        var waitingAttr =
          row.waiting_status && row.waiting_status.is_waiting
            ? ' data-waiting="true"'
            : "";
        tr.className = "equipment-row " + overdueClass;
        if (waitingAttr) tr.setAttribute("data-waiting", "true");

        var statusHtml = "";
        var st = (s.status || "").toLowerCase();
        if (st === "pending") {
          statusHtml =
            '<span class="badgep"><i class="fas fa-clock me-1"></i>Pending</span>';
        } else if (st === "pushed") {
          statusHtml =
            '<span class="badgeu"><i class="fas fa-arrow-right me-1"></i>Pushed</span>';
        } else if (st === "completed") {
          statusHtml =
            '<span class="badgec"><i class="fas fa-check me-1"></i>Completed</span>';
        } else {
          statusHtml =
            '<span class="badgest">' +
            (s.status || "").toUpperCase() +
            "</span>";
        }

        var badges = "";
        if (row.is_overdue) {
          badges +=
            '<span class="badge bg-danger ms-2" data-bs-toggle="tooltip" title="Overdue"><i class="fas fa-exclamation-triangle"></i> OVERDUE</span>';
        }
        if (row.is_warning) {
          badges +=
            '<span class="badge bg-warning ms-2" data-bs-toggle="tooltip" title="Due this month"><i class="fas fa-clock"></i> DUE</span>';
        }
        if (row.waiting_status && row.waiting_status.is_waiting) {
          badges +=
            '<span class="badge bg-info ms-2"><i class="fas fa-users"></i> WAITING</span>';
        }

        // Build logic pill
        var logicVal = (s.planning_logic || "").toLowerCase();
        var logicHtml;
        if (logicVal === "date_based" || logicVal === "department") {
          logicHtml =
            '<span class="cs-logic-pill cs-logic-pill--dept" title="Department-Based"><i class="fas fa-building"></i> Dept</span>';
        } else if (
          logicVal === "description_based" ||
          logicVal === "description"
        ) {
          logicHtml =
            '<span class="cs-logic-pill cs-logic-pill--desc" title="Description-Based"><i class="fas fa-cog"></i> Desc</span>';
        } else {
          logicHtml =
            '<span class="cs-logic-pill cs-logic-pill--none">—</span>';
        }

        tr.innerHTML =
          '<td><input type="checkbox" class="form-check-input cs-check schedule-checkbox" value="' +
          s.id +
          '"></td>' +
          '<td><span class="cs-dept-pill">' +
          (s.department_name || "—") +
          "</span></td>" +
          '<td><span class="cs-equip-name">' +
          (s.equipment_description || "—") +
          "</span>" +
          badges +
          "</td>" +
          '<td><code class="cs-code">' +
          (s.serial_number || "N/A") +
          "</code></td>" +
          "<td>" +
          (s.scheduled_month_display || "N/A") +
          "</td>" +
          "<td>" +
          logicHtml +
          "</td>" +
          "<td>" +
          statusHtml +
          "</td>" +
          "<td></td>";
      } else if (tab === "completed") {
        var completedDate =
          s.completed_date_display || s.completed_date || "N/A";
        var completedStatus =
          '<span class="cs-badge cs-badge--done"><i class="fas fa-check me-1"></i>Done</span>';
        tr.innerHTML =
          '<td><span class="cs-equip-name">' +
          (s.equipment_description || "—") +
          "</span></td>" +
          '<td><code class="cs-code">' +
          (s.serial_number || "N/A") +
          "</code></td>" +
          '<td><span class="cs-dept-pill">' +
          (s.department_name || "—") +
          "</span></td>" +
          "<td>" +
          completedDate +
          "</td>" +
          "<td>" +
          completedStatus +
          "</td>";
      } else if (tab === "unscheduled") {
        var eqId = row.id || s.id || "";
        tr.innerHTML =
          '<td><input type="checkbox" class="form-check-input cs-check unscheduled-checkbox" value="' +
          eqId +
          '"></td>' +
          '<td><span class="cs-dept-pill">' +
          (row.department_name || s.department_name || "—") +
          "</span></td>" +
          '<td><span class="cs-equip-name">' +
          (row.description || s.equipment_description || "—") +
          "</span></td>" +
          '<td><code class="cs-code">' +
          (row.serial_number || s.serial_number || "N/A") +
          "</code></td>" +
          "<td>" +
          (row.model || s.model || "N/A") +
          "</td>" +
          "<td></td>";
      }
      tbody.appendChild(tr);
    });

    // Re-init tooltips for new elements
    if (typeof bootstrap !== "undefined" && bootstrap.Tooltip) {
      document
        .querySelectorAll('[data-bs-toggle="tooltip"]')
        .forEach(function (el) {
          new bootstrap.Tooltip(el);
        });
    }

    // Re-bind checkbox handlers
    handleScheduledCheckboxes();
    handleUnscheduledCheckboxes();
  }

  return { init: init };
})();

function setupSubTabs() {
  SubTab.init();
}

// ============================================
// CSRF TOKEN HELPER
// ============================================

function getCsrfToken() {
  // 1. Try meta tag (most reliable)
  var meta = document.querySelector('meta[name="csrf-token"]');
  if (meta) return meta.getAttribute("content");
  // 2. Try hidden form input
  var inp = document.querySelector('[name="csrfmiddlewaretoken"]');
  if (inp) return inp.value;
  // 3. Try cookie
  var cookies = document.cookie.split(";");
  for (var i = 0; i < cookies.length; i++) {
    var parts = cookies[i].trim().split("=");
    if (parts[0] === "csrftoken") return decodeURIComponent(parts[1]);
  }
  return "";
}

// ==========================================
// END OF CALIBRATION SCHEDULES JAVASCRIPT
// ==========================================
