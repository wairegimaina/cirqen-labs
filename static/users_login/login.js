/**
 * Password Toggle Functionality
 * Adds show/hide password toggle button to password input fields
 *
 * @version 1.0.0
 * @author Cirqen Development Team
 */

document.addEventListener('DOMContentLoaded', function () {
  // Find the password input field
  const passwordField = document.querySelector('input[type="password"]');

  if (!passwordField) {
    console.warn('Password field not found');
    return;
  }

  // Get the parent input group
  const inputGroup = passwordField.closest('.input-group');

  if (!inputGroup) {
    console.warn('Input group not found for password field');
    return;
  }

  // Add password-field class to the input group
  inputGroup.classList.add('password-field');

  // Use the existing toggle button already rendered in the HTML
  const toggleButton = inputGroup.querySelector('.toggle-password');

  if (!toggleButton) {
    console.warn('Toggle button not found in HTML');
    return;
  }

  // Add click event listener
  toggleButton.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();

    const isPassword = passwordField.type === 'password';
    passwordField.type = isPassword ? 'text' : 'password';

    // Update the existing eyeIcon element already in the HTML
    const eyeIcon = document.getElementById('eyeIcon');
    if (eyeIcon) {
      eyeIcon.className = isPassword ? 'fas fa-eye-slash' : 'fas fa-eye';
    }

    this.setAttribute('data-tooltip', isPassword ? 'Hide password' : 'Show password');
    this.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
    this.classList.toggle('active', isPassword);
  });

  console.log('Password toggle initialized successfully');
});
