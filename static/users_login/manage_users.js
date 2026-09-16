// Replace the entire <script> section in manage_users.html with this

let users = [];
let currentEditUser = null;
let currentSignatureUser = null;
let departments = [];
let workshops = [];

let currentPage = 1;
const pageSize = 30;

// Current filter state
let currentFilters = {
  search: '',
  role: '',
  department: '',
  workshop: '',
  level: '',
  show_inactive: false,
};

// Apply filters by calling the API
async function applyFilters() {
  const searchTerm = document.getElementById('searchInput').value.trim();
  const roleFilter = document.getElementById('roleFilter').value;

  // Update filter state
  currentFilters.search = searchTerm;
  currentFilters.role = roleFilter;
  currentFilters.page = 1; // Reset to first page when filters change
  currentPage = 1;

  // Load users with filters
  await loadUsers();
}

// Initialize the page
document.addEventListener('DOMContentLoaded', function () {
  loadUsers();
  loadDepartments();
  setupEventListeners();
});

function setupEventListeners() {
  // Search functionality with debounce
  let searchTimeout;
  document.getElementById('searchInput').addEventListener('input', function () {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      applyFilters();
    }, 500); // Wait 500ms after user stops typing
  });

  // Role filter
  document.getElementById('roleFilter').addEventListener('change', applyFilters);

  // Modal close events
  document.querySelectorAll('.close').forEach(close => {
    close.addEventListener('click', function () {
      this.closest('.modal').style.display = 'none';
    });
  });

  // Close modal when clicking outside
  window.addEventListener('click', function (event) {
    if (event.target.classList.contains('modal')) {
      event.target.style.display = 'none';
    }
  });

  // Role change events
  document.getElementById('editRole').addEventListener('change', function () {
    toggleEditRoleFields(this.value);
  });

  document.getElementById('createRole').addEventListener('change', function () {
    toggleCreateRoleFields(this.value);
  });

  // Department change events
  document.getElementById('editDepartment').addEventListener('change', function () {
    loadWorkshopsByDepartment(this.value);
  });

  document.getElementById('createDepartment').addEventListener('change', function () {
    loadCreateWorkshopsByDepartment(this.value);
  });

  // Signature upload event
  document.getElementById('signatureUpload').addEventListener('change', function (e) {
    handleSignatureUpload(e);
  });

  // Create user form submission
  document.getElementById('userCreationForm').addEventListener('submit', function (e) {
    e.preventDefault();
    createUser();
  });
}

function showCreateUserTab() {
  const createTab = new bootstrap.Tab(document.getElementById('create-tab'));
  createTab.show();
}

function handleSignatureUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  // Validate file type
  if (!file.type.startsWith('image/')) {
    showAlert('Please select a valid image file', 'danger');
    event.target.value = '';
    return;
  }

  // Validate file size (2MB limit)
  if (file.size > 2 * 1024 * 1024) {
    showAlert('File size must be less than 2MB', 'danger');
    event.target.value = '';
    return;
  }

  // Show preview
  const reader = new FileReader();
  reader.onload = function (e) {
    const previewImg = document.getElementById('signaturePreviewImg');
    const previewContainer = document.getElementById('signaturePreview');
    const uploadLabel = document.querySelector('.file-upload-label');

    previewImg.src = e.target.result;
    previewContainer.classList.remove('hidden');
    uploadLabel.classList.add('has-file');
    uploadLabel.innerHTML = '<i class="fas fa-check"></i> Signature image selected';
  };
  reader.readAsDataURL(file);
}

function clearSignatureUpload() {
  const fileInput = document.getElementById('signatureUpload');
  const previewContainer = document.getElementById('signaturePreview');
  const uploadLabel = document.querySelector('.file-upload-label');

  fileInput.value = '';
  previewContainer.classList.add('hidden');
  uploadLabel.classList.remove('has-file');
  uploadLabel.innerHTML = `
        <i class="fas fa-cloud-upload-alt" style="font-size: 24px; margin-bottom: 10px; display: block;"></i>
        <span>Click to select signature image</span>
        <div style="font-size: 12px; color: #6c757d; margin-top: 5px;">
            PNG, JPG, or GIF (Max 2MB)
        </div>
    `;
}

async function loadUsers() {
  const grid = document.getElementById('usersGrid');

  // Show loading state
  grid.innerHTML = `
        <div class="loading">
            <i class="fas fa-spinner fa-spin"></i>
            <h3>Loading users...</h3>
            <p>Please wait while we fetch the user data</p>
        </div>
    `;

  try {
    // Build query parameters from current filters
    const params = new URLSearchParams();

    if (currentFilters.search) params.append('search', currentFilters.search);
    if (currentFilters.role) params.append('role', currentFilters.role);
    if (currentFilters.department) params.append('department', currentFilters.department);
    if (currentFilters.workshop) params.append('workshop', currentFilters.workshop);
    if (currentFilters.level) params.append('level', currentFilters.level);
    if (currentFilters.show_inactive) params.append('show_inactive', 'true');

    params.append('page', currentPage);
    params.append('per_page', pageSize);

    const response = await fetch(`/login/api/users/?${params.toString()}`);
    const data = await response.json();

    if (data.success) {
      users = data.users;
      displayUsers(users, data.pagination);
    } else {
      showAlert('Failed to load users: ' + data.error, 'danger');
      grid.innerHTML = `
                <div class="no-results">
                    <i class="fas fa-exclamation-triangle"></i>
                    <h3>Error Loading Users</h3>
                    <p>${escapeHTML(data.error)}</p>
                </div>
            `;
    }
  } catch (error) {
    showAlert('Error loading users: ' + error.message, 'danger');
    grid.innerHTML = `
            <div class="no-results">
                <i class="fas fa-exclamation-triangle"></i>
                <h3>Error Loading Users</h3>
                <p>${escapeHTML(error.message)}</p>
            </div>
        `;
  }
}

async function loadDepartments() {
  try {
    const response = await fetch('/login/api/departments/');
    const data = await response.json();
    if (data.success) {
      departments = data.departments;
      populateDepartmentSelects();
    }
  } catch (error) {
    console.error('Error loading departments:', error);
  }
}

async function loadWorkshopsByDepartment(departmentName) {
  if (!departmentName) {
    document.getElementById('editWorkshop').innerHTML = '<option value="">Select Workshop</option>';
    return;
  }
  try {
    const dept = departments.find(d => d.name === departmentName);
    if (dept) {
      const response = await fetch(`/login/api/workshops/?department_id=${dept.id}`);
      const data = await response.json();
      if (data.success) {
        const workshopSelect = document.getElementById('editWorkshop');
        workshopSelect.innerHTML = '<option value="">Select Workshop</option>';
        data.workshops.forEach(workshop => {
          const option = document.createElement('option');
          option.value = workshop.name;
          option.textContent = workshop.name;
          workshopSelect.appendChild(option);
        });
      }
    }
  } catch (error) {
    console.error('Error loading workshops:', error);
  }
}

async function loadCreateWorkshopsByDepartment(departmentId) {
  if (!departmentId) {
    document.getElementById('createWorkshop').innerHTML =
      '<option value="">Select Workshop</option>';
    return;
  }
  try {
    const response = await fetch(`/login/api/workshops/?department_id=${departmentId}`);
    const data = await response.json();
    if (data.success) {
      const workshopSelect = document.getElementById('createWorkshop');
      workshopSelect.innerHTML = '<option value="">Select Workshop</option>';
      data.workshops.forEach(workshop => {
        const option = document.createElement('option');
        option.value = workshop.id;
        option.textContent = workshop.name;
        workshopSelect.appendChild(option);
      });
    }
  } catch (error) {
    console.error('Error loading workshops:', error);
  }
}

function populateDepartmentSelects() {
  const editDepartmentSelect = document.getElementById('editDepartment');
  const createDepartmentSelect = document.getElementById('createDepartment');

  editDepartmentSelect.innerHTML = '<option value="">Select Department</option>';
  createDepartmentSelect.innerHTML = '<option value="">Select Department</option>';

  departments.forEach(dept => {
    const editOption = document.createElement('option');
    editOption.value = dept.name;
    editOption.textContent = dept.name;
    editDepartmentSelect.appendChild(editOption);

    const createOption = document.createElement('option');
    createOption.value = dept.id;
    createOption.textContent = dept.name;
    createDepartmentSelect.appendChild(createOption);
  });
}

function renderPagination(pagination) {
  const paginationContainer = document.getElementById('paginationControls');

  if (!pagination || pagination.total_pages <= 1) {
    paginationContainer.innerHTML = '';
    return;
  }

  const { page, total_pages, has_previous, has_next, total } = pagination;

  paginationContainer.innerHTML = `
        <button onclick="changePage(${page - 1})" ${escapeHTML(!has_previous ? 'disabled' : '')}>
            <i class="fas fa-chevron-left"></i> Prev
        </button>
        <span>Page ${page} of ${escapeHTML(total_pages)} (${total} users)</span>
        <button onclick="changePage(${page + 1})" ${escapeHTML(!has_next ? 'disabled' : '')}>
            Next <i class="fas fa-chevron-right"></i>
        </button>
    `;
}

function changePage(page) {
  currentPage = page;
  loadUsers();
  // Scroll to top
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function displayUsers(usersToShow, pagination) {
  const grid = document.getElementById('usersGrid');

  if (usersToShow.length === 0) {
    grid.innerHTML = `
            <div class="no-results">
                <i class="fas fa-users-slash"></i>
                <h3>No users found</h3>
                <p>Try adjusting your search criteria or filters</p>
            </div>
        `;
    document.getElementById('paginationControls').innerHTML = '';
    return;
  }

  // Render users
  grid.innerHTML = usersToShow.map(user => createUserCard(user)).join('');

  // Update pagination controls
  if (pagination) {
    renderPagination(pagination);
  }
}

function createUserCard(user) {
  const initials = (user.firstName.charAt(0) + user.lastName.charAt(0)).toUpperCase();
  const roleClass = `role-${user.role.toLowerCase()}`;
  const statusClass = user.isActive ? 'status-active' : 'status-inactive';
  const statusText = user.isActive ? 'Active' : 'Inactive';

  return `
        <div class="user-card">
            <div class="user-header">
                <div class="user-avatar">${escapeHTML(initials)}</div>
                <div class="user-info">
                    <h3>${escapeHTML(user.firstName)} ${escapeHTML(user.lastName)}</h3>
                    <div class="username">@${escapeHTML(user.username)}</div>
                </div>
            </div>
            <div class="user-details">
                <div class="detail-row">
                    <span class="detail-label">
                        <i class="fas fa-id-badge"></i> Employee ID
                    </span>
                    <span class="detail-value">${escapeHTML(user.employeeId)}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">
                        <i class="fas fa-envelope"></i> Email
                    </span>
                    <span class="detail-value">${escapeHTML(user.email)}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">
                        <i class="fas fa-user-tag"></i> Role
                    </span>
                    <span class="detail-value">
                        <span class="role-badge ${roleClass}">${escapeHTML(
    user.roleDisplay || user.role)
  }</span>
                    </span>
                </div>
                ${
                  user.department
                    ? `
                <div class="detail-row">
                    <span class="detail-label">
                        <i class="fas fa-building"></i> Department
                    </span>
                    <span class="detail-value">${user.department}</span>
                </div>
                `
                    : ''
                }
                ${
                  user.workshop
                    ? `
                <div class="detail-row">
                    <span class="detail-label">
                        <i class="fas fa-tools"></i> Workshop
                    </span>
                    <span class="detail-value">${user.workshop}</span>
                </div>
                `
                    : ''
                }
                ${
                  user.level
                    ? `
                <div class="detail-row">
                    <span class="detail-label">
                        <i class="fas fa-layer-group"></i> Level
                    </span>
                    <span class="detail-value">${user.levelDisplay || user.level}</span>
                </div>
                `
                    : ''
                }
                <div class="detail-row">
                    <span class="detail-label">
                        <i class="fas fa-toggle-on"></i> Status
                    </span>
                    <span class="detail-value">
                        <span class="status-badge ${statusClass}">${escapeHTML(statusText)}</span>
                    </span>
                </div>
            </div>
            <div class="user-actions">
                <button class="action-btn btn-primary" onclick="editUser('${escapeHTML(user.id)}')">
                    <i class="fas fa-edit"></i> Edit
                </button>
                <button class="action-btn btn-danger" onclick="deleteUser('${escapeHTML(user.id)}')">
                    <i class="fas fa-trash"></i> Delete
                </button>
            </div>
        </div>
    `;
}

async function editUser(userId) {
  const user = users.find(u => u.id === userId);
  if (!user) return;

  currentEditUser = user;

  // Populate basic form fields
  document.getElementById('editFirstName').value = user.firstName;
  document.getElementById('editLastName').value = user.lastName;
  document.getElementById('editEmail').value = user.email;
  document.getElementById('editRole').value = user.role;
  document.getElementById('editPhone').value = user.phone || '';
  document.getElementById('editDepartment').value = user.department || '';
  document.getElementById('editWorkshop').value = user.workshop || '';
  document.getElementById('editLevel').value = user.level || '';

  // Load current signature
  await loadCurrentSignature(userId);

  // Update form visibility based on role
  toggleEditRoleFields(user.role);
  if (user.department) {
    loadWorkshopsByDepartment(user.department);
  }

  // Clear any uploaded signature
  clearSignatureUpload();
  document.getElementById('editModal').style.display = 'block';
}

function toggleEditRoleFields(role) {
  const departmentGroup = document.getElementById('editDepartmentGroup');
  const workshopGroup = document.getElementById('editWorkshopGroup');
  const levelGroup = document.getElementById('editLevelGroup');

  // Hide all fields first
  departmentGroup.style.display = 'none';
  workshopGroup.style.display = 'none';
  levelGroup.style.display = 'none';

  // Show relevant fields based on role
  if (role === 'NIC') {
    departmentGroup.style.display = 'block';
  } else if (role === 'Tech') {
    workshopGroup.style.display = 'block';
    levelGroup.style.display = 'block';
  }
}

function toggleCreateRoleFields(role) {
  const departmentField = document.getElementById('createDepartmentField');
  const workshopField = document.getElementById('createWorkshopField');
  const levelField = document.getElementById('createLevelField');
  const hodInfo = document.getElementById('createHodInfo');
  const departmentSelect = document.getElementById('createDepartment');
  const workshopSelect = document.getElementById('createWorkshop');
  const levelSelect = document.getElementById('createLevel');

  // Reset & hide all
  [departmentField, workshopField, levelField, hodInfo].forEach(el => {
    el.classList.add('hidden');
  });
  [departmentSelect, workshopSelect, levelSelect].forEach(el => {
    el.removeAttribute('required');
    el.value = '';
  });

  // Show required fields based on role
  if (role === 'NIC') {
    departmentField.classList.remove('hidden');
    departmentSelect.setAttribute('required', 'required');
  } else if (role === 'Tech') {
    workshopField.classList.remove('hidden');
    levelField.classList.remove('hidden');
    workshopSelect.setAttribute('required', 'required');
    levelSelect.setAttribute('required', 'required');
  } else if (role === 'HOD') {
    hodInfo.classList.remove('hidden');
  }
}

async function createUser() {
  const overlay = document.getElementById('overlay');
  overlay.classList.remove('hidden');

  const formData = {
    firstName: document.getElementById('createFirstName').value.trim(),
    lastName: document.getElementById('createLastName').value.trim(),
    email: document.getElementById('createEmail').value.trim(),
    role: document.getElementById('createRole').value,
    department: document.getElementById('createDepartment').value,
    workshop: document.getElementById('createWorkshop').value,
    level: document.getElementById('createLevel').value,
  };

  if (!formData.firstName || !formData.lastName || !formData.email || !formData.role) {
    overlay.classList.add('hidden');
    showCreateAlert('Please fill in all required fields', 'danger');
    return;
  }

  try {
    const response = await fetch('/login/api/create-user/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      body: JSON.stringify(formData),
    });

    const data = await response.json();

    if (data.success) {
      showCreateAlert(`User ${data.user.username} created successfully!`, 'success');
      resetCreateForm();
      loadUsers();
      setTimeout(() => {
        const manageTab = new bootstrap.Tab(document.getElementById('manage-tab'));
        manageTab.show();
      }, 3000);
    } else {
      showCreateAlert('Failed to create user: ' + data.error, 'danger');
    }
  } catch (error) {
    showCreateAlert('Error creating user: ' + error.message, 'danger');
  } finally {
    overlay.classList.add('hidden');
  }
}

function resetCreateForm() {
  document.getElementById('userCreationForm').reset();
  toggleCreateRoleFields('');
}

async function saveUser() {
  if (!currentEditUser) return;

  const formData = new FormData();

  const appendIfValid = (fieldId, fieldName) => {
    const el = document.getElementById(fieldId);
    if (el && el.value.trim()) {
      formData.append(fieldName, el.value.trim());
    }
  };

  appendIfValid('editFirstName', 'firstName');
  appendIfValid('editLastName', 'lastName');
  appendIfValid('editEmail', 'email');
  appendIfValid('editRole', 'role');
  appendIfValid('editPhone', 'phone');
  appendIfValid('editDepartment', 'department');
  appendIfValid('editWorkshop', 'workshop');
  appendIfValid('editLevel', 'level');

  const signatureFile = document.getElementById('signatureUpload').files[0];
  if (signatureFile) {
    formData.append('signature', signatureFile);
  }

  try {
    const response = await fetch(`/login/api/users/${currentEditUser.id}/`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
      },
      body: formData,
    });

    const data = await response.json();

    if (data.success) {
      showAlert('User updated successfully!', 'success');
      closeEditModal();
      loadUsers();
    } else {
      showAlert('Failed to update user: ' + (data.error || 'Unknown error'), 'danger');
    }
  } catch (error) {
    showAlert('Error updating user: ' + error.message, 'danger');
  }
}

function closeEditModal() {
  document.getElementById('editModal').style.display = 'none';
  currentEditUser = null;
  clearSignatureUpload();
}

async function loadCurrentSignature(userId) {
  const signatureDisplay = document.getElementById('currentSignatureDisplay');
  try {
    const response = await fetch(`/login/api/users/${userId}/signature/`);
    const data = await response.json();
    if (data.success && data.hasSignature) {
      signatureDisplay.innerHTML = `
                <h4>Current Signature</h4>
                <img src="${escapeHTML(data.signatureUrl)}" alt="Current Signature" class="signature-preview" style="max-width: 200px; display: block; margin: 10px auto;">
                <div style="font-size: 12px; color: #6c757d; margin-top: 10px;">
                    <p><strong>ID:</strong> ${escapeHTML(data.signatureId)}</p>
                    <p><strong>Last Updated:</strong> ${new Date(
                      data.updatedAt,
                    ).toLocaleString()}</p>
                </div>
            `;
    } else {
      signatureDisplay.innerHTML = `
                <div style="text-align: center; color: #6c757d;">
                    <i class="fas fa-signature" style="font-size: 2rem; margin-bottom: 10px;"></i>
                    <p>No current signature available</p>
                </div>
            `;
    }
  } catch (error) {
    signatureDisplay.innerHTML = `
            <div style="text-align: center; color: #dc3545;">
                <i class="fas fa-exclamation-triangle"></i>
                <p>Error loading current signature</p>
            </div>
        `;
  }
}

async function deleteUser(userId) {
  const user = users.find(u => u.id === userId);
  if (!user) return;

  const confirmMessage = `Are you sure you want to delete ${user.firstName} ${user.lastName}?\n\nThis action cannot be undone and will permanently remove:\n- User account and profile\n- All associated data\n- Access permissions\n\nType "DELETE" to confirm:`;
  const confirmation = prompt(confirmMessage);

  if (confirmation !== 'DELETE') {
    return;
  }

  try {
    const response = await fetch(`/login/api/users/${userId}/delete/`, {
      method: 'DELETE',
      headers: {
        'X-CSRFToken': getCsrfToken(),
      },
    });

    const data = await response.json();
    if (data.success) {
      showAlert(`User ${user.firstName} ${user.lastName} deleted successfully.`, 'success');
      loadUsers();
    } else {
      showAlert('Failed to delete user: ' + data.error, 'danger');
    }
  } catch (error) {
    showAlert('Error deleting user: ' + error.message, 'danger');
  }
}

function refreshUsers() {
  currentPage = 1;
  currentFilters = { search: '', role: '' };
  document.getElementById('searchInput').value = '';
  document.getElementById('roleFilter').value = '';
  loadUsers();
}

function showAlert(message, type, container = 'alertContainer') {
  const alertContainer = document.getElementById(container);
  const alertDiv = document.createElement('div');
  alertDiv.className = `alert alert-${type}`;
  alertDiv.innerHTML = `
        <i class="fas fa-${escapeHTML(type === 'success' ? 'check-circle' : 'exclamation-circle')}"></i>
        ${escapeHTML(message)}
        <button style="float: right; background: none; border: none; font-size: 18px; cursor: pointer;" onclick="this.parentElement.remove()">&times;</button>
    `;
  alertContainer.appendChild(alertDiv);
  alertDiv.style.display = 'block';

  setTimeout(() => {
    if (alertDiv.parentNode) {
      alertDiv.remove();
    }
  }, 5000);

  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showCreateAlert(message, type) {
  showAlert(message, type, 'createUserAlerts');
}

function getCsrfToken() {
  const cookies = document.cookie.split(';');
  for (let cookie of cookies) {
    const [name, value] = cookie.trim().split('=');
    if (name === 'csrftoken') {
      return value;
    }
  }
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  if (csrfMeta) {
    return csrfMeta.getAttribute('content');
  }
  return '';
}

function exportUsers() {
  const csvContent =
    'data:text/csv;charset=utf-8,' +
    'Name,Username,Email,Role,Employee ID,Department,Workshop,Level,Status,Created At\n' +
    users
      .map(user => {
        return [
          `"${user.firstName} ${user.lastName}"`,
          user.username,
          user.email,
          user.role,
          user.employeeId,
          user.department || '',
          user.workshop || '',
          user.level || '',
          user.isActive ? 'Active' : 'Inactive',
          user.createdAt,
        ].join(',');
      })
      .join('\n');

  const encodedUri = encodeURI(csvContent);
  const link = document.createElement('a');
  link.setAttribute('href', encodedUri);
  link.setAttribute('download', `users_export_${new Date().toISOString().split('T')[0]}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  showAlert('Users exported successfully!', 'success');
}

document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') {
    const modals = document.querySelectorAll('.modal');
    modals.forEach(modal => {
      if (modal.style.display === 'block') {
        modal.style.display = 'none';
      }
    });
  }

  if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
    event.preventDefault();
    document.getElementById('searchInput').focus();
  }

  if (event.key === 'F5') {
    event.preventDefault();
    refreshUsers();
  }
});
