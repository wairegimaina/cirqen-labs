/* Start with Ansur on Perform Calibration.
 *
 * Shows the button for procedures set up on the Ansur connection page, starts
 * the job (Cirqen writes the work order and opens Ansur), then follows the job
 * every 2 seconds until Ansur's record has been imported. The status check
 * also picks up the record, so this works even if the background worker is
 * not running.
 */
(function () {
  "use strict";

  const form = document.getElementById("calibrationForm");
  const urls = document.getElementById("ansurUrls");
  const startBtn = document.getElementById("ansurStartBtn");
  const panel = document.getElementById("ansurPanel");
  if (!form || !urls || !startBtn || !panel) return;

  const read = (id) => {
    const el = document.getElementById(id);
    try { return el ? JSON.parse(el.textContent) : null; } catch (e) { return null; }
  };
  const procedures = new Set(read("ansur-procedures") || []);
  const procedureSelect = document.getElementById("procedure");
  const csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;

  const el = {
    number: document.getElementById("ansurJobNumber"),
    steps: document.getElementById("ansurSteps"),
    text: document.getElementById("ansurText"),
    error: document.getElementById("ansurError"),
    warning: document.getElementById("ansurWarning"),
    link: document.getElementById("ansurSessionLink"),
    reopen: document.getElementById("ansurReopen"),
    uploadLabel: document.getElementById("ansurUploadLabel"),
    upload: document.getElementById("ansurUpload"),
    cancel: document.getElementById("ansurCancel"),
  };

  let job = null;
  let timer = null;

  function toggleButton() {
    const show = procedures.has(procedureSelect ? procedureSelect.value : "");
    startBtn.hidden = !show || (job && job.open);
  }

  function setStep(name, state) {
    const li = el.steps.querySelector(`[data-step="${name}"]`);
    if (li) li.className = state || "";
  }

  function show(message, isError) {
    panel.hidden = false;
    el.error.hidden = !isError;
    el.error.textContent = isError ? message : "";
    if (!isError) el.text.textContent = message;
  }

  function render(data) {
    job = data;
    panel.hidden = false;
    el.number.textContent = data.job_number ? `Job ${data.job_number}` : "";
    el.text.textContent = data.text || "";
    el.error.hidden = !data.error;
    el.error.textContent = data.error || "";
    el.warning.hidden = !data.warning;
    el.warning.textContent = data.warning || "";

    ["sent", "run", "imported", "approval"].forEach((s) => setStep(s, ""));
    if (data.status === "prepared") setStep("sent", "active");
    if (data.status === "sent") { setStep("sent", "done"); setStep("run", "active"); }
    if (data.status === "rejected") { setStep("sent", "done"); setStep("run", "done"); setStep("imported", "failed"); }
    if (data.status === "imported") {
      ["sent", "run", "imported"].forEach((s) => setStep(s, "done"));
      setStep("approval", "active");
    }

    el.link.hidden = !data.session_url;
    if (data.session_url) el.link.href = data.session_url;
    el.reopen.hidden = !data.open;
    el.uploadLabel.hidden = !data.open;
    el.cancel.hidden = !data.open;
    toggleButton();

    clearTimeout(timer);
    if (data.status === "sent" || data.status === "prepared") timer = setTimeout(poll, 2000);
  }

  async function send(url, body) {
    body.append("csrfmiddlewaretoken", csrf);
    const response = await fetch(url, {
      method: "POST", body, credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest" },
    });
    let data = {};
    try { data = await response.json(); } catch (e) { data = { error: `Server error ${response.status}` }; }
    if (data.job) render(data.job);
    if (!data.success && data.error && !(data.job && data.job.error)) show(data.error, true);
    return data;
  }

  async function poll() {
    if (!job) return;
    try {
      const response = await fetch(`${urls.dataset.status}?job=${encodeURIComponent(job.id)}`,
        { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } });
      const data = await response.json();
      if (data.job) render(data.job);
      else timer = setTimeout(poll, 4000);
    } catch (e) {
      timer = setTimeout(poll, 4000); // offline for a moment; keep following
    }
  }

  startBtn.addEventListener("click", async () => {
    const missing = ["actual_temperature", "actual_humidity"].filter((name) => {
      const input = form.querySelector(`[name=${name}]`);
      return !input || !input.value.trim();
    });
    if (missing.length) {
      show("Enter the room temperature and humidity before starting Ansur.", true);
      form.querySelector(`[name=${missing[0]}]`)?.focus();
      return;
    }
    startBtn.disabled = true;
    show("Writing the work order and opening Ansur…", false);
    const body = new FormData();
    ["equipment", "schedule", "procedure", "actual_temperature", "actual_humidity", "actual_pressure", "notes"]
      .forEach((name) => {
        const input = form.querySelector(`[name=${name}]`);
        if (input) body.append(name, input.value);
      });
    try {
      await send(urls.dataset.start, body);
    } catch (e) {
      show("Could not reach Cirqen. Check the connection and try again.", true);
    } finally {
      startBtn.disabled = false;
    }
  });

  el.reopen.addEventListener("click", () => {
    const body = new FormData();
    body.append("job", job.id);
    body.append("action", "reopen");
    send(urls.dataset.action, body);
  });

  el.cancel.addEventListener("click", () => {
    if (!window.confirm(`Cancel Ansur job ${job.job_number}? A record saved for it later will not be imported.`)) return;
    const body = new FormData();
    body.append("job", job.id);
    body.append("action", "cancel");
    send(urls.dataset.action, body);
  });

  el.upload.addEventListener("change", () => {
    const file = el.upload.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("job", job.id);
    body.append("record", file);
    el.upload.value = "";
    show("Checking the record…", false);
    send(urls.dataset.upload, body);
  });

  if (procedureSelect) procedureSelect.addEventListener("change", toggleButton);
  const openJob = read("ansur-open-job");
  if (openJob) render(openJob);
  toggleButton();
})();
