/**
 * department_logic.js
 * Logic for Department Creation, Deletion, and Transfer
 */

document.addEventListener('DOMContentLoaded', function () {
  // Close modals on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeAllModals();
    }
  });

  // Close modals when clicking outside
  window.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay')) {
      closeAllModals();
    }
  });
});

// State Variables
let currentDeleteDeptId = null;
let currentDeleteUrl = null;

/* ============================
   TOGGLE FORM LOGIC
   ============================ */

function toggleAddForm() {
  const form = document.getElementById('addDepartmentForm');
  const btn = document.getElementById('toggleFormBtn');
  const icon = btn.querySelector('i');
  const span = btn.querySelector('span');

  if (form.style.display === 'none' || form.style.display === '') {
    form.style.display = 'block';
    icon.classList.remove('fa-plus');
    icon.classList.add('fa-minus');
    span.textContent = 'Cancel';
    btn.classList.remove('btn-primary');
    btn.classList.add('btn-secondary');
    setTimeout(() => document.getElementById('department_name').focus(), 100);
  } else {
    closeAddForm();
  }
}

function closeAddForm() {
  const form = document.getElementById('addDepartmentForm');
  const btn = document.getElementById('toggleFormBtn');
  const icon = btn.querySelector('i');
  const span = btn.querySelector('span');

  form.style.display = 'none';
  icon.classList.remove('fa-minus');
  icon.classList.add('fa-plus');
  span.textContent = 'Add New Department';
  btn.classList.remove('btn-secondary');
  btn.classList.add('btn-primary');
  document.getElementById('department_name').value = '';
}

/* ============================
   DELETE & TRANSFER LOGIC
   ============================ */

function confirmDelete(deptId, deptName, count) {
  currentDeleteDeptId = deptId;
  currentDeleteUrl = `/Inventory/department/delete/${deptId}/`;

  const modal = document.getElementById('deleteModal');
  const warningBox = document.getElementById('warningBox');
  const transferSection = document.getElementById('transferSection');
  const transferBtn = document.getElementById('transferEquipmentBtn');
  const deleteBtn = document.getElementById('confirmDeleteBtn');
  const transferSelect = document.getElementById('transferDepartment');
  const successBox = document.getElementById('successBox');

  // Reset UI
  document.getElementById('deleteDepartmentName').textContent = deptName;
  document.getElementById('equipmentCount').textContent = count;
  successBox.style.display = 'none';

  if (count > 0) {
    warningBox.style.display = 'block';
    transferSection.style.display = 'block';
    transferBtn.style.display = 'inline-flex';

    // Hide current department in dropdown
    Array.from(transferSelect.options).forEach(opt => {
      opt.style.display = (opt.value === deptId) ? 'none' : 'block';
    });

    // Disable Delete
    deleteBtn.disabled = true;
    deleteBtn.innerHTML = '<i class="fas fa-ban me-2"></i> Resolve Dependencies First';
    deleteBtn.classList.remove('btn-danger');
    deleteBtn.classList.add('btn-secondary');
  } else {
    warningBox.style.display = 'none';
    transferSection.style.display = 'none';
    transferBtn.style.display = 'none';

    deleteBtn.disabled = false;
    deleteBtn.innerHTML = '<i class="fas fa-trash-alt me-2"></i> Delete Department';
    deleteBtn.classList.add('btn-danger');
    deleteBtn.classList.remove('btn-secondary');
  }

  modal.style.display = 'flex';
}

async function handleEquipmentTransfer() {
  const targetId = document.getElementById('transferDepartment').value;
  if (!targetId) {
    alert('Please select a department to transfer items to.');
    return;
  }

  if (!confirm('Are you sure? This will move all records to the selected department.')) {
    return;
  }

  const transferBtn = document.getElementById('transferEquipmentBtn');
  const successBox = document.getElementById('successBox');

  transferBtn.disabled = true;
  transferBtn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i> Moving...';

  try {
    const formData = new FormData();
    formData.append('target_department_id', targetId);
    formData.append('csrfmiddlewaretoken', document.querySelector('[name=csrfmiddlewaretoken]').value);

    const response = await fetch(`/Inventory/department/${currentDeleteDeptId}/transfer_dependencies/`, {
      method: 'POST',
      body: formData
    });

    const data = await response.json();

    if (data.success) {
      document.getElementById('warningBox').style.display = 'none';
      document.getElementById('transferSection').style.display = 'none';
      transferBtn.style.display = 'none';

      successBox.style.display = 'block';
      let countdown = 45;

      const interval = setInterval(() => {
        successBox.innerHTML = `
                    <div class="d-flex align-items-center">
                        <i class="fas fa-sync fa-spin me-3 text-success fs-4"></i>
                        <div>
                            <strong>Transfer Initiated. Syncing with HQ...</strong>
                            <div>Time remaining: ${countdown}s</div>
                        </div>
                    </div>
                `;
        countdown--;

        if (countdown < 0) {
          clearInterval(interval);
          successBox.innerHTML = `
                        <div class="text-success fw-bold">
                            <i class="fas fa-check-circle me-2"></i> Sync Complete! Department is now empty.
                        </div>
                    `;

          const deleteBtn = document.getElementById('confirmDeleteBtn');
          deleteBtn.disabled = false;
          deleteBtn.innerHTML = '<i class="fas fa-trash-alt me-2"></i> Delete Empty Department';
          deleteBtn.classList.remove('btn-secondary');
          deleteBtn.classList.add('btn-danger');
        }
      }, 1000);

    } else {
      alert('Transfer failed: ' + data.error);
      transferBtn.disabled = false;
      transferBtn.innerHTML = 'Try Again';
    }
  } catch (e) {
    alert('Network error during transfer.');
    transferBtn.disabled = false;
    transferBtn.innerHTML = 'Try Again';
  }
}

function handleDepartmentDelete() {
  if (confirm('Are you sure you want to delete this department permanently?')) {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = currentDeleteUrl;

    const csrf = document.createElement('input');
    csrf.type = 'hidden';
    csrf.name = 'csrfmiddlewaretoken';
    csrf.value = document.querySelector('[name=csrfmiddlewaretoken]').value;

    form.appendChild(csrf);
    document.body.appendChild(form);
    form.submit();
  }
}

/* ============================
   EDIT & MODAL HELPERS
   ============================ */

function editDepartment(id, name) {
  const modal = document.getElementById('editModal');
  const input = document.getElementById('edit_department_name');
  const form = document.getElementById('editForm');

  input.value = name;
  form.action = `/Inventory/department/edit/${id}/`;
  modal.style.display = 'flex';
}

function submitEdit() {
  const input = document.getElementById('edit_department_name');
  if (input.value.trim()) {
    document.getElementById('editForm').submit();
  } else {
    alert('Name cannot be empty');
  }
}

function transferDepartment(id, name, count) {
  const modal = document.getElementById('transferModal');
  document.getElementById('transferMessage').textContent = `Move "${name}" to another Workshop`;
  document.getElementById('transferForm').action = `/Inventory/department/transfer/${id}/`;
  modal.style.display = 'flex';
}

function submitTransfer() {
  if (document.getElementById('target_workshop').value) {
    document.getElementById('transferForm').submit();
  } else {
    alert('Select a workshop');
  }
}

function closeDeleteModal() { document.getElementById('deleteModal').style.display = 'none'; }
function closeEditModal() { document.getElementById('editModal').style.display = 'none'; }
function closeTransferModal() { document.getElementById('transferModal').style.display = 'none'; }

function closeAllModals() {
  closeDeleteModal();
  closeEditModal();
  closeTransferModal();
  closeAddForm();
}
