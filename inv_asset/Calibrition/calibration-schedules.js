document.addEventListener('DOMContentLoaded', function() {
    setupPlanningModal();
    handleScheduledCheckboxes();
    handleUnscheduledCheckboxes();
    handleActionButtons();
    handleBulkActions();
    handleSearch();
});

// Sidebar toggle
document.getElementById('btn')?.addEventListener('click', () => {
    document.querySelector('.sidebar')?.classList.toggle('active');
});

// Planning modal setup
function setupPlanningModal() {
    const cards = document.querySelectorAll('.planning-card');
    const logicInput = document.getElementById('planningLogic');
    const periodButtons = document.querySelectorAll('.period-btn');

    cards.forEach(card => {
        card.addEventListener('click', () => {
            cards.forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            logicInput.value = card.dataset.logic;
        });
    });
    if (cards.length > 0) {
        cards[0].classList.add('selected');
    }
    periodButtons.forEach(button => {
        button.addEventListener('click', () => {
            periodButtons.forEach(btn => {
                btn.classList.remove('btn-primary', 'active');
                btn.classList.add('btn-outline-primary');
            });
            button.classList.remove('btn-outline-primary');
            button.classList.add('btn-primary', 'active');
            document.getElementById('calibrationPeriod').value = button.dataset.period;
        });
    });
}

// Scheduled checkboxes
function handleScheduledCheckboxes() {
    const selectAll = document.getElementById('selectAll');
    const checkboxes = document.querySelectorAll('.schedule-checkbox');
    const bulkDelete = document.getElementById('bulkDeleteBtn');
    const bulkComplete = document.getElementById('bulkMarkCompleted');
    const bulkPush = document.getElementById('bulkPushSchedules');

    function updateButtons() {
        const checked = document.querySelectorAll('.schedule-checkbox:checked');
        const hasChecked = checked.length > 0;
        bulkDelete.disabled = !hasChecked;
        bulkComplete.disabled = !hasChecked;
        bulkPush.disabled = !hasChecked;
        window.selectedSchedules = Array.from(checked).map(cb => cb.value);
    }

    if (selectAll) {
        selectAll.addEventListener('change', () => {
            checkboxes.forEach(cb => cb.checked = selectAll.checked);
            updateButtons();
        });
    }
    checkboxes.forEach(cb => cb.addEventListener('change', updateButtons));
    updateButtons();
}

// Unscheduled checkboxes
function handleUnscheduledCheckboxes() {
    const selectAll = document.getElementById('selectAllUnscheduled');
    const checkboxes = document.querySelectorAll('.unscheduled-checkbox');
    const scheduleBtn = document.getElementById('scheduleSelectedBtn');

    function updateButtons() {
        const checked = document.querySelectorAll('.unscheduled-checkbox:checked');
        const hasChecked = checked.length > 0;
        scheduleBtn.disabled = !hasChecked;
        window.selectedUnscheduled = Array.from(checked).map(cb => cb.value);
    }

    if (selectAll) {
        selectAll.addEventListener('change', () => {
            checkboxes.forEach(cb => cb.checked = selectAll.checked);
            updateButtons();
        });
    }
    checkboxes.forEach(cb => cb.addEventListener('change', updateButtons));
    updateButtons();
}

// Action buttons
function handleActionButtons() {
    document.querySelectorAll('.schedule-single-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const equipmentId = btn.dataset.equipmentId;
            if (confirm('Schedule this equipment for calibration?')) {
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = `/calibration/schedule/${equipmentId}/`;
                form.innerHTML = `<input type="hidden" name="csrfmiddlewaretoken" value="${getCsrfToken()}">`;
                document.body.appendChild(form);
                form.submit();
            }
        });
    });
}

// Bulk actions
function handleBulkActions() {
    const actions = [
        { btn: 'bulkDeleteBtn', url: '{% url "bulk_delete_calibration_schedules" %}' },
        { btn: 'bulkMarkCompleted', url: '{% url "bulk_mark_calibration_completed" %}' },
        { btn: 'bulkPushSchedules', url: '{% url "bulk_push_calibration_schedules" %}' }
    ];
    actions.forEach(({ btn, url }) => {
        document.getElementById(btn)?.addEventListener('click', () => {
            if (!window.selectedSchedules || window.selectedSchedules.length === 0) {
                alert('Please select at least one schedule.');
                return;
            }
            if (confirm(`Perform action on ${window.selectedSchedules.length} schedule(s)?`)) {
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = url;
                form.innerHTML = `<input type="hidden" name="csrfmiddlewaretoken" value="${getCsrfToken()}">`;
                window.selectedSchedules.forEach(id => {
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = 'schedule_ids';
                    input.value = id;
                    form.appendChild(input);
                });
                document.body.appendChild(form);
                form.submit();
            }
        });
    });
    document.getElementById('scheduleSelectedBtn')?.addEventListener('click', () => {
        if (!window.selectedUnscheduled || window.selectedUnscheduled.length === 0) {
            alert('Please select at least one equipment.');
            return;
        }
        document.getElementById('unscheduledForm').submit();
    });
}

// Search
function handleSearch() {
    const searchInput = document.getElementById('searchInput');
    if (!searchInput) return;
    searchInput.addEventListener('input', function() {
        const term = this.value.toLowerCase();
        ['#schedulesTable', '#unscheduledTable'].forEach(tableId => {
            document.querySelectorAll(`${tableId} .equipment-row`).forEach(row => {
                const text = Array.from(row.cells).map(c => c.textContent.toLowerCase()).join(' ');
                row.style.display = text.includes(term) ? '' : 'none';
            });
        });
    });
}

// Get CSRF
function getCsrfToken() {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
        const [name, value] = cookie.trim().split('=');
        if (name === 'csrftoken') return value;
    }
    const csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return csrfInput ? csrfInput.value : '';
}
