var signaturePad = null;

/* ===== Signature Pad Init ===== */
if (NEEDS_SIGNATURE) {
  document.addEventListener('DOMContentLoaded', function () {
    var canvas = document.getElementById('signature-pad');
    if (!canvas) return;

    signaturePad = new SignaturePad(canvas, {
      backgroundColor: 'rgba(12, 21, 37, 1)',   /* matches --bg-input */
      penColor: 'rgb(224, 231, 255)'             /* light lavender on dark */
    });

    function resizeCanvas() {
      var ratio = Math.max(window.devicePixelRatio || 1, 1);
      canvas.width = canvas.offsetWidth * ratio;
      canvas.height = canvas.offsetHeight * ratio;
      canvas.getContext('2d').scale(ratio, ratio);
      signaturePad.clear();
    }

    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();
  });
}

function clearSignature() {
  if (signaturePad) signaturePad.clear();
}

/* ===== Password Toggle ===== */
function togglePass(fieldId, button) {
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

/* ===== Password Validation (live) ===== */
if (NEEDS_PASSWORD) {
  document.addEventListener('DOMContentLoaded', function () {
    var pw1 = document.getElementById('new_password');
    var pw2 = document.getElementById('confirm_password');
    var fill = document.querySelector('.progress-fill');

    if (!pw1 || !pw2) return;

    pw1.addEventListener('input', function () {
      var val = this.value;
      var score = 0;
      if (val.length >= 8) score++;
      if (/[a-zA-Z]/.test(val)) score++;
      if (/[0-9]/.test(val)) score++;
      if (/[^a-zA-Z0-9]/.test(val)) score++;

      /* Border colour feedback */
      if (score >= 3) {
        this.style.borderColor = 'var(--success)';
      } else if (score >= 2) {
        this.style.borderColor = 'var(--warning)';
      } else {
        this.style.borderColor = score ? 'var(--danger)' : '';
      }

      /* Update progress bar if both steps pending */
      if (fill && NEEDS_SIGNATURE) {
        fill.style.width = 25 + (score / 4) * 25 + '%';
      } else if (fill) {
        fill.style.width = (score / 4) * 100 + '%';
      }
    });

    pw2.addEventListener('input', function () {
      if (!pw1.value || !this.value) return;
      this.style.borderColor = pw1.value === this.value ? 'var(--success)' : 'var(--danger)';
    });
  });
}

/* ===== Form Submit ===== */
document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('setupForm');
  if (!form) return;

  form.addEventListener('submit', function (e) {
    var valid = true;
    var submitBtn = document.getElementById('submitBtn');

    if (NEEDS_PASSWORD) {
      var pw1 = document.getElementById('new_password').value;
      var pw2 = document.getElementById('confirm_password').value;

      if (pw1.length < 8) {
        alert('Password must be at least 8 characters long.');
        valid = false;
      } else if (pw1 !== pw2) {
        alert('Passwords do not match.');
        valid = false;
      }
    }

    if (NEEDS_SIGNATURE && signaturePad && !signaturePad.isEmpty()) {
      document.getElementById('signature_data').value = signaturePad.toDataURL('image/png');
    }

    if (valid && submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span>Setting up account…</span><i class="fas fa-spinner fa-spin"></i>';
    } else if (!valid) {
      e.preventDefault();
    }
  });

  /* Auto-dismiss non-info alerts */
  setTimeout(function () {
    document.querySelectorAll('.alert:not(.alert-info)').forEach(function (el) {
      el.style.transition = 'opacity 0.3s, transform 0.3s';
      el.style.opacity = '0';
      el.style.transform = 'translateY(-16px)';
      setTimeout(function () { el.remove(); }, 350);
    });
  }, 5000);
});
