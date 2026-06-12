// static/Dutys/duty.js

document.addEventListener("DOMContentLoaded", () => {
    // =========================
    // GLOBAL STATE
    // =========================
    const ALL_TECHNOLOGISTS = JSON.parse('{{ technologists_json|escapejs }}');
    let currentShiftId = null;
    let currentShiftDate = null;
    let currentShiftType = null;
    let selectedWorkers = new Set();
    let pendingAction = null;
    let currentSection = "roster";
    let allEmployees = [];

    // =========================
    // HELPER: MESSAGES
    // =========================
    function showSuccessMessage(msg) {
        alert(msg); // replace with nicer UI if available
    }
    function showErrorMessage(msg) {
        alert(msg); // replace with nicer UI if available
    }

    // =========================
    // SPA NAVIGATION
    // =========================
    function showSection(sectionName, event = null) {
        document.querySelectorAll(".spa-section").forEach(s => s.classList.remove("active"));
        document.querySelectorAll(".spa-nav-btn").forEach(b => b.classList.remove("active"));

        const section = document.getElementById(sectionName + "-section");
        if (section) section.classList.add("active");
        if (event && event.target) event.target.classList.add("active");

        currentSection = sectionName;
        const monthNav = document.getElementById("monthNavigation");
        if (monthNav) monthNav.style.display = sectionName === "roster" ? "flex" : "none";

        if (sectionName === "my-leave") loadMyLeaveRequests();
        else if (sectionName === "manage-leave") {
            loadPendingLeaveRequests();
            loadEmployees();
        } else if (sectionName === "settings") loadHolidays();
    }
    window.showSection = showSection;

    // =========================
    // INIT
    // =========================
    const today = new Date().toISOString().split("T")[0];
    document.querySelectorAll("input[type='date']").forEach(input => {
        if (input.id.includes("StartDate") || input.id.includes("EndDate")) input.min = today;
    });
    if (document.getElementById("holidaysTableBody")) loadHolidays();

    // =========================
    // TECH LEAVE FORM
    // =========================
    const requestLeaveForm = document.getElementById("requestLeaveForm");
    if (requestLeaveForm) {
        requestLeaveForm.addEventListener("submit", async e => {
            e.preventDefault();
            const start = document.getElementById("leaveStartDate");
            const end = document.getElementById("leaveEndDate");
            const type = document.getElementById("leaveType");
            const reason = document.getElementById("leaveReason");
            if (!start || !end || !type) return;

            const formData = new FormData();
            formData.append("start_date", start.value);
            formData.append("end_date", end.value);
            formData.append("leave_type", type.value);
            formData.append("reason", reason ? reason.value : "");

            try {
                const res = await fetch("/view-roster/request-leave/", {
                    method: "POST",
                    body: formData,
                    headers: { "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value }
                });
                const data = await res.json();
                if (data.success) {
                    showSuccessMessage(data.message);
                    requestLeaveForm.reset();
                    loadMyLeaveRequests();
                } else showErrorMessage("Error: " + data.error);
            } catch (err) {
                console.error(err);
                showErrorMessage("Error submitting leave");
            }
        });
    }
    window.clearLeaveForm = () => requestLeaveForm && requestLeaveForm.reset();

    // =========================
    // LOAD MY LEAVE REQUESTS
    // =========================
    async function loadMyLeaveRequests() {
        try {
            const res = await fetch("/view-roster/get-my-leave-requests/");
            const data = await res.json();
            const tbody = document.getElementById("myLeaveRequestsBody");
            if (!tbody) return;
            tbody.innerHTML = "";

            if (!data.success) return showErrorMessage("Error: " + data.error);
            if (!data.leave_requests.length) {
                tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;">No leave requests</td></tr>`;
                return;
            }

            data.leave_requests.forEach(l => {
                const cls = l.approved === null ? "pending" : l.approved ? "approved" : "rejected";
                const txt = l.approved === null ? "Pending" : l.approved ? "Approved" : "Rejected";
                tbody.innerHTML += `
                  <tr>
                    <td>${new Date(l.start_date).toLocaleDateString()}</td>
                    <td>${new Date(l.end_date).toLocaleDateString()}</td>
                    <td>${l.duration_days} day${l.duration_days !== 1 ? "s" : ""}</td>
                    <td>${l.leave_type}</td>
                    <td><span class="leave-status-badge leave-status-${cls}">${txt}</span></td>
                    <td>${l.approved_by || "-"}</td>
                    <td>
                      <button class="btn btn-info btn-sm" onclick="viewLeaveDetails(${l.id})">Details</button>
                      ${l.approved === null ? `<button class="btn btn-danger btn-sm" onclick="cancelLeaveRequest(${l.id})">Cancel</button>` : ""}
                    </td>
                  </tr>`;
            });
        } catch (err) {
            console.error(err);
            showErrorMessage("Error loading leave requests");
        }
    }
    window.loadMyLeaveRequests = loadMyLeaveRequests;

    async function cancelLeaveRequest(id) {
        if (!confirm("Cancel this request?")) return;
        try {
            const res = await fetch("/view-roster/cancel-leave-request/", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value },
                body: JSON.stringify({ leave_id: id })
            });
            const data = await res.json();
            if (data.success) {
                showSuccessMessage(data.message);
                loadMyLeaveRequests();
            } else showErrorMessage("Error: " + data.error);
        } catch (err) {
            console.error(err);
            showErrorMessage("Error cancelling leave");
        }
    }
    window.cancelLeaveRequest = cancelLeaveRequest;

    window.viewLeaveDetails = id => showSuccessMessage("Details coming soon for leave " + id);

    // =========================
    // HOD: ADD LEAVE FORM
    // =========================
    const addEmployeeLeaveForm = document.getElementById("addEmployeeLeaveForm");
    if (addEmployeeLeaveForm) {
        addEmployeeLeaveForm.addEventListener("submit", async e => {
            e.preventDefault();
            const empId = document.getElementById("selectedEmployeeId");
            if (!empId || !empId.value) return showErrorMessage("Select employee");

            try {
                const res = await fetch("/add-leave/", {
                    method: "POST",
                    body: new FormData(addEmployeeLeaveForm),
                    headers: { "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value }
                });
                if (res.ok) {
                    showSuccessMessage("Leave added");
                    addEmployeeLeaveForm.reset();
                    clearEmployeeLeaveForm();
                    loadPendingLeaveRequests();
                } else showErrorMessage("Error adding leave");
            } catch (err) {
                console.error(err);
                showErrorMessage("Error adding leave");
            }
        });
    }
    window.clearEmployeeLeaveForm = () => {
        if (addEmployeeLeaveForm) addEmployeeLeaveForm.reset();
        const empId = document.getElementById("selectedEmployeeId");
        if (empId) empId.value = "";
        const dd = document.getElementById("employeeDropdown");
        if (dd) dd.style.display = "none";
    };

    // =========================
    // LOAD PENDING LEAVE (HOD)
    // =========================
    async function loadPendingLeaveRequests() {
        try {
            const res = await fetch("/view-roster/get-pending-leave-requests/");
            const data = await res.json();
            const tbody = document.getElementById("pendingLeaveRequestsBody");
            if (!tbody) return;
            tbody.innerHTML = "";

            if (!data.success) return showErrorMessage("Error: " + data.error);
            if (!data.pending_requests.length) {
                tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;">No pending requests</td></tr>`;
                return;
            }

            data.pending_requests.forEach(l => {
                tbody.innerHTML += `
                  <tr>
                    <td>${l.employee_name}</td>
                    <td>${l.employee_level}</td>
                    <td>${new Date(l.start_date).toLocaleDateString()}</td>
                    <td>${new Date(l.end_date).toLocaleDateString()}</td>
                    <td>${l.duration_days} days</td>
                    <td>${l.leave_type}</td>
                    <td>${l.reason || "-"}</td>
                    <td><button class="btn btn-success btn-sm" onclick="openLeaveApprovalModal(${l.id})">Review</button></td>
                  </tr>`;
            });
        } catch (err) {
            console.error(err);
            showErrorMessage("Error loading pending leave");
        }
    }
    window.loadPendingLeaveRequests = loadPendingLeaveRequests;

    // =========================
    // APPROVE / REJECT LEAVE
    // =========================
    window.openLeaveApprovalModal = id => {
        const modal = document.getElementById("leaveApprovalModal");
        if (modal) modal.style.display = "flex";
        const hidden = document.getElementById("approvalLeaveId");
        if (hidden) hidden.value = id;
    };
    window.approveLeaveRequest = async () => {
        const id = document.getElementById("approvalLeaveId").value;
        try {
            const res = await fetch("/view-roster/approve-leave-request/", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value },
                body: JSON.stringify({ leave_id: parseInt(id), action: "approve" })
            });
            const data = await res.json();
            if (data.success) {
                showSuccessMessage(data.message);
                closeModal("leaveApprovalModal");
                loadPendingLeaveRequests();
            } else showErrorMessage("Error: " + data.error);
        } catch (err) {
            console.error(err);
            showErrorMessage("Error approving leave");
        }
    };
    window.rejectLeaveRequest = async () => {
        const id = document.getElementById("approvalLeaveId").value;
        const reason = document.getElementById("rejectionReason").value.trim();
        if (!reason) return showErrorMessage("Reason required");
        try {
            const res = await fetch("/view-roster/approve-leave-request/", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value },
                body: JSON.stringify({ leave_id: parseInt(id), action: "reject", rejection_reason: reason })
            });
            const data = await res.json();
            if (data.success) {
                showSuccessMessage(data.message);
                closeModal("leaveApprovalModal");
                loadPendingLeaveRequests();
            } else showErrorMessage("Error: " + data.error);
        } catch (err) {
            console.error(err);
            showErrorMessage("Error rejecting leave");
        }
    };
    window.showRejectionReason = () => {
        document.getElementById("rejectionReasonGroup").style.display = "block";
        document.getElementById("confirmRejectBtn").style.display = "inline-block";
    };

    // =========================
    // CLOSE MODALS
    // =========================
    window.closeModal = id => {
        const m = document.getElementById(id);
        if (m) m.style.display = "none";
    };

    // =========================
    // SHIFT EDITING / ASSIGN
    // =========================
    window.openEditModal = (shiftId, shiftDate, shiftType) => {
        currentShiftId = shiftId;
        currentShiftDate = shiftDate;
        currentShiftType = shiftType;
        selectedWorkers = new Set();

        const modal = document.getElementById("editShiftModal");
        if (modal) modal.style.display = "flex";

        fetchEligibleWorkers(shiftDate, shiftType);
    };

    async function fetchEligibleWorkers(date, type) {
        try {
            const res = await fetch(`/view-roster/get-eligible-workers/?date=${date}&type=${type}`);
            const data = await res.json();
            if (!data.success) return showErrorMessage("Error: " + data.error);
            renderWorkerSelection(data.workers);
        } catch (err) {
            console.error(err);
            showErrorMessage("Error fetching eligible workers");
        }
    }

    function renderWorkerSelection(workers) {
        const container = document.getElementById("workerSelectionContainer");
        if (!container) return;
        container.innerHTML = "";

        workers.forEach(w => {
            const item = document.createElement("div");
            item.className = "worker-option";
            item.innerHTML = `
              <label>
                <input type="checkbox" onchange="updateWorkerEligibility(${w.id}, this.checked)">
                ${w.name} (${w.level})
              </label>`;
            container.appendChild(item);
        });
    }
    window.updateWorkerEligibility = (id, checked) => {
        if (checked) selectedWorkers.add(id);
        else selectedWorkers.delete(id);
    };

    window.showConfirmation = () => {
        if (!selectedWorkers.size) return showErrorMessage("Select at least one worker");
        const confirmBox = document.getElementById("confirmSelectionBox");
        if (confirmBox) confirmBox.style.display = "block";
    };

    window.rescheduleFromNextWeek = async () => {
        try {
            const res = await fetch("/view-roster/reschedule-shift/", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value },
                body: JSON.stringify({
                    shift_id: currentShiftId,
                    workers: [...selectedWorkers],
                    action: pendingAction || "assign"
                })
            });
            const data = await res.json();
            if (data.success) {
                showSuccessMessage(data.message);
                closeModal("editShiftModal");
                location.reload();
            } else showErrorMessage("Error: " + data.error);
        } catch (err) {
            console.error(err);
            showErrorMessage("Error rescheduling");
        }
    };

    // =========================
    // QUICK ASSIGN
    // =========================
    window.openQuickAssignModal = () => {
        const m = document.getElementById("quickAssignModal");
        if (m) m.style.display = "flex";
        loadQuickAssignData();
    };

    async function loadQuickAssignData() {
        try {
            const res = await fetch("/view-roster/get-quick-assign-data/");
            const data = await res.json();
            const tbody = document.getElementById("quickAssignTableBody");
            if (!tbody) return;
            tbody.innerHTML = "";

            if (!data.success) return showErrorMessage("Error: " + data.error);
            data.assignments.forEach(a => {
                tbody.innerHTML += `
                  <tr>
                    <td>${a.name}</td>
                    <td>${a.level}</td>
                    <td>${a.days.join(", ")}</td>
                  </tr>`;
            });
        } catch (err) {
            console.error(err);
            showErrorMessage("Error loading quick assign data");
        }
    }

    // =========================
    // SCHEDULE SUMMARY
    // =========================
    window.openScheduleSummaryModal = () => {
        const m = document.getElementById("scheduleSummaryModal");
        if (m) m.style.display = "flex";
        loadScheduleSummary();
    };

    async function loadScheduleSummary() {
        try {
            const res = await fetch("/view-roster/get-schedule-summary/");
            const data = await res.json();
            const tbody = document.getElementById("scheduleSummaryTableBody");
            if (!tbody) return;
            tbody.innerHTML = "";

            if (!data.success) return showErrorMessage("Error: " + data.error);
            data.summary.forEach(s => {
                tbody.innerHTML += `
                  <tr>
                    <td>${s.name}</td>
                    <td>${s.level}</td>
                    <td>${s.total_shifts}</td>
                  </tr>`;
            });
        } catch (err) {
            console.error(err);
            showErrorMessage("Error loading summary");
        }
    }

    // =========================
    // HOLIDAYS (SETTINGS)
    // =========================
    async function loadHolidays() {
        try {
            const res = await fetch("/view-roster/get-holidays/");
            const data = await res.json();
            const tbody = document.getElementById("holidaysTableBody");
            if (!tbody) return;
            tbody.innerHTML = "";

            if (!data.success) return showErrorMessage("Error: " + data.error);
            data.holidays.forEach(h => {
                tbody.innerHTML += `
                  <tr>
                    <td>${new Date(h.date).toLocaleDateString()}</td>
                    <td>${h.name}</td>
                  </tr>`;
            });
        } catch (err) {
            console.error(err);
            showErrorMessage("Error loading holidays");
        }
    }
    window.loadHolidays = loadHolidays;
});
