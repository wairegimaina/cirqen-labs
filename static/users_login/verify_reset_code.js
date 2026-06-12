/**
 * verify_reset_code.js
 * Handles both VERIFY CODE and RESET PASSWORD modes.
 * Requires PAGE_MODE, FIELD_PW1_ID, FIELD_PW2_ID, CODE_FIELD_ID
 * to be set as global variables before this script loads.
 */

/* ===== SHARED: password toggle ===== */
function togglePassword(fieldId, button) {
  var field = document.getElementById(fieldId);
  var icon = button.querySelector('i');
  if (field.type === 'password') {
    field.type = 'text';
    icon.className = 'fas fa-eye-slash';
  } else {
    field.type = 'password';
    icon.className = 'fas fa-eye';
  }
}

/* ===== SHARED: auto-dismiss alerts ===== */
document.addEventListener('DOMContentLoaded', function () {
  setTimeout(function () {
    document.querySelectorAll('.alert').forEach(function (alert) {
      var bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
      if (bsAlert) bsAlert.close();
    });
  }, 5000);
});

/* ===================================================
   RESET PASSWORD MODE
   =================================================== */
if (PAGE_MODE === 'reset_password') {

  function checkPasswordStrength(password) {
    var strength = 0;
    if (password.length >= 8) strength += 25;
    if (password.match(/[a-zA-Z]/)) strength += 25;
    if (password.match(/[0-9]/)) strength += 25;
    if (password.match(/[^a-zA-Z0-9]/)) strength += 25;

    if (strength <= 25) return { strength: strength, text: 'Weak', cls: 'strength-weak' };
    if (strength <= 50) return { strength: strength, text: 'Fair', cls: 'strength-fair' };
    if (strength <= 75) return { strength: strength, text: 'Good', cls: 'strength-good' };
    return { strength: strength, text: 'Strong', cls: 'strength-strong' };
  }

  function checkRequirements() {
    var p1 = document.getElementById(FIELD_PW1_ID).value;
    var p2 = document.getElementById(FIELD_PW2_ID).value;

    function setReq(id, valid) {
      var el = document.getElementById(id);
      if (!el) return;
      el.className = 'requirement ' + (valid ? 'valid' : 'invalid');
      el.querySelector('i').className = valid ? 'fas fa-check' : 'fas fa-times';
    }

    setReq('req-length', p1.length >= 8);
    setReq('req-letter', /[a-zA-Z]/.test(p1));
    setReq('req-number', /[0-9]/.test(p1));
    setReq('req-match', p1.length > 0 && p1 === p2);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var pw1 = document.getElementById(FIELD_PW1_ID);
    var pw2 = document.getElementById(FIELD_PW2_ID);
    var bar = document.getElementById('strengthBar');
    var barText = document.getElementById('strengthText');

    if (pw1 && pw2) {
      pw1.addEventListener('input', function () {
        var result = checkPasswordStrength(this.value);
        bar.className = 'strength-fill ' + result.cls;
        barText.textContent = result.text;
        checkRequirements();
      });
      pw2.addEventListener('input', checkRequirements);
    }
  });
}

/* ===================================================
   VERIFY CODE MODE
   =================================================== */
if (PAGE_MODE !== 'reset_password') {

  /* Countdown timer */
  var timeLeft = 30 * 60;
  var countdownEl = document.getElementById('countdown');

  function updateCountdown() {
    var mins = Math.floor(timeLeft / 60);
    var secs = timeLeft % 60;
    if (countdownEl) {
      countdownEl.textContent = mins + ':' + String(secs).padStart(2, '0');
    }

    if (timeLeft <= 0) {
      if (countdownEl) {
        countdownEl.textContent = 'EXPIRED';
        countdownEl.style.color = 'var(--danger)';
      }
      var verifyBtn = document.getElementById('verifyBtn');
      if (verifyBtn) {
        verifyBtn.disabled = true;
        verifyBtn.innerHTML = '<span>Code Expired</span><i class="fas fa-times"></i>';
      }
      clearInterval(countdownInterval);
    }

    if (timeLeft <= 300 && countdownEl) {
      countdownEl.style.color = 'var(--danger)';
    }

    timeLeft--;
  }

  updateCountdown();
  var countdownInterval = setInterval(updateCountdown, 1000);

  /* Auto-format & auto-submit code input */
  document.addEventListener('DOMContentLoaded', function () {
    var codeInput = document.getElementById(CODE_FIELD_ID);
    if (!codeInput) return;

    codeInput.focus();

    codeInput.addEventListener('input', function (e) {
      e.target.value = e.target.value.replace(/[^0-9]/g, '');
      if (e.target.value.length === 6) {
        setTimeout(function () {
          document.getElementById('verifyForm').submit();
        }, 500);
      }
    });
  });
}
