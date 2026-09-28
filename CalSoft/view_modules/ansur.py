"""Ansur connection page: where Cirqen finds Ansur on this PC and which
Ansur template runs each calibration procedure.

One page, one form per job, each posted with an ``action`` so the page stays
a single place to set everything up and to see what still needs doing.
"""
import hashlib
import logging
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from calSchedules.models import CalibrationSchedule
from CalSoft.ansur import jobfile, launcher, setup, template, watcher
from CalSoft.ansur.importer import ImportRefused, import_record, refuse
from CalSoft.ansur.parser import RecordError
from CalSoft.models import AnsurJob, AnsurSettings, AnsurTemplateMap, CalibrationProcedure, Standard
from CalSoft.view_modules.calibration_helpers import _reference_standard_status
from Inventory.models import Equipment
from users.control import get_user_role


class AnsurSettingsForm(forms.ModelForm):
    class Meta:
        model = AnsurSettings
        fields = ["program_path", "base_folder", "delete_job_files", "archive_years", "enabled",
                  "launch_arguments", "pdf_arguments"]
        labels = {
            "launch_arguments": "Start Ansur with",
            "pdf_arguments": "Make Ansur's PDF with",
            "program_path": "Ansur program",
            "base_folder": "Work folder",
            "delete_job_files": "Delete work orders after import",
            "archive_years": "Keep imported records (years)",
            "enabled": "Show Start with Ansur on Perform Calibration",
        }

    def clean_program_path(self):
        path = self.cleaned_data["program_path"].strip()
        error = setup.validate_program_path(path)
        if error:
            raise forms.ValidationError(error)
        return path

    def clean_base_folder(self):
        path = self.cleaned_data["base_folder"].strip().rstrip("\\/")
        error = setup.validate_base_folder(path)
        if error:
            raise forms.ValidationError(error)
        return path

    def clean_launch_arguments(self):
        value = self.cleaned_data["launch_arguments"].strip()
        if "{job}" not in value:
            raise forms.ValidationError("Include {job}, where the work order file goes.")
        return value

    def clean_pdf_arguments(self):
        value = self.cleaned_data["pdf_arguments"].strip()
        if "{record}" not in value:
            raise forms.ValidationError("Include {record}, where the test record goes.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("enabled") and not self.errors:
            draft = AnsurSettings(program_path=cleaned["program_path"], base_folder=cleaned["base_folder"])
            failing = [c for c in setup.readiness(draft, AnsurTemplateMap.objects.all()) if not c.ok]
            if failing:
                raise forms.ValidationError(
                    "Fix these before switching it on: " + "; ".join(c.name for c in failing) + ".")
        return cleaned


class TemplateMapForm(forms.ModelForm):
    class Meta:
        model = AnsurTemplateMap
        fields = ["procedure", "template_file", "ansur_standard", "service_event"]
        labels = {"template_file": "Ansur template", "ansur_standard": "Standard in Ansur"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["procedure"].queryset = (
            CalibrationProcedure.objects
            .filter(active_status=True, pending_delete=False, ansur_template__isnull=True)
            .order_by("name"))

    def clean_template_file(self):
        name = self.cleaned_data["template_file"].strip()
        if "/" in name or "\\" in name:
            raise forms.ValidationError("Give the file name only; it must be in the templates folder.")
        if not name.lower().endswith(setup.TEMPLATE_SUFFIX):
            raise forms.ValidationError("Ansur templates end in .mtt.")
        return name


def _save_parameter_links(procedure, post):
    """Ansur step, limit type and analyser accuracy for each parameter."""
    errors, updates = [], []
    for parameter in procedure.parameters.filter(active_status=True, pending_delete=False):
        prefix = f"p{parameter.pk}-"
        step = (post.get(prefix + "ansur_step") or "").strip()[:150]
        limit_type = post.get(prefix + "limit_type") or parameter.limit_type
        if limit_type not in ("two_sided", "upper", "lower"):
            errors.append(f"{parameter.name}: choose how it is judged.")
            continue
        values = {}
        for name in ("analyser_accuracy_pct", "analyser_accuracy_floor", "analyser_resolution",
                     "reference_uncertainty"):
            raw = (post.get(prefix + name) or "").strip()
            if raw == "":
                values[name] = None
                continue
            try:
                number = Decimal(raw)
            except InvalidOperation:
                number = None
            if number is None or not number.is_finite() or number < 0:
                errors.append(f"{parameter.name}: {raw} is not a valid number.")
                break
            values[name] = number
        else:
            if limit_type == "two_sided" and parameter.tolerance is None:
                errors.append(f"{parameter.name}: a ± limit needs a tolerance on the procedure.")
                continue
            updates.append((parameter, step, limit_type, values))
    if errors:
        return errors
    for parameter, step, limit_type, values in updates:
        parameter.ansur_step, parameter.limit_type = step, limit_type
        for name, value in values.items():
            if name == "reference_uncertainty" and value is None:
                continue  # left blank: keep what the procedure has
            setattr(parameter, name, value)
        parameter.save()
    return []


@login_required
def ansur_settings(request):
    # Set up by the calibration centre, who run calibrations with Ansur, or
    # the HOD: the same people who review calibration sessions.
    from CalSoft.view_modules.pending_sessions import can_review_calibrations
    if not can_review_calibrations(request.user):
        raise PermissionDenied("Only calibration-centre staff and the HOD can set up the Ansur connection.")
    cfg = AnsurSettings.load()
    form = AnsurSettingsForm(instance=cfg)
    map_form = TemplateMapForm()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save":
            form = AnsurSettingsForm(request.POST, instance=cfg)
            if form.is_valid():
                saved = form.save(commit=False)
                saved.updated_by = request.user
                saved.save()
                messages.success(request, "Ansur connection saved.")
                return redirect("calibration:ansur_settings")

        elif action == "detect":
            found = setup.detect_program()
            if found:
                cfg.program_path = found
                cfg.updated_by = request.user
                cfg.save()
                messages.success(request, f"Found Ansur at {found}.")
            else:
                messages.error(request, "Could not find Ansur on this PC. Paste the path to its .exe instead.")
            return redirect("calibration:ansur_settings")

        elif action == "create_folders":
            error = setup.validate_base_folder(cfg.base_folder)
            if error:
                messages.error(request, error)
            else:
                try:
                    created = setup.ensure_folders(cfg.base_folder)
                except OSError as exc:
                    messages.error(request, f"Could not create the folders: {exc.strerror or exc}.")
                else:
                    messages.success(request, f"Created: {', '.join(created)}." if created
                                     else "All folders already exist.")
            return redirect("calibration:ansur_settings")

        elif action == "add_map":
            map_form = TemplateMapForm(request.POST)
            if map_form.is_valid():
                link = map_form.save()
                messages.success(request, f"{link.procedure} now runs with {link.template_file}.")
                return redirect("calibration:ansur_settings")

        elif action == "from_template":
            file_name = (request.POST.get("template_file") or "").strip()
            name = (request.POST.get("procedure_name") or "").strip() or Path(file_name).stem
            if file_name not in setup.list_templates(cfg.base_folder):
                messages.error(request, "Choose a template from the templates folder.")
                return redirect("calibration:ansur_settings")
            if CalibrationProcedure.objects.filter(name__iexact=name, pending_delete=False).exists():
                messages.error(request, f"A procedure called {name} already exists. Give the new one another name.")
                return redirect("calibration:ansur_settings")
            standard = Standard.objects.filter(pk=request.POST.get("standard") or None, active_status=True).first()
            data = (Path(setup.folder_paths(cfg.base_folder)["templates"]) / file_name).read_bytes()
            try:
                procedure, found = template.create_procedure(
                    file_name, data, name=name, user=request.user, standard=standard,
                    service_event=(request.POST.get("service_event") or "PM").strip()[:60],
                    ansur_standard=(request.POST.get("ansur_standard") or "").strip()[:100])
            except RecordError as exc:
                messages.error(request, f"{file_name}: {exc}")
            else:
                messages.success(request, f"Created {procedure.name} with {len(found)} parameter(s) from "
                                          f"{file_name}, linked to it. Fill in the analyser accuracy and the "
                                          f"reference uncertainty below to switch it on.")
            return redirect("calibration:ansur_settings")

        elif action == "save_parameters":
            link = get_object_or_404(AnsurTemplateMap, pk=request.POST.get("map_id"))
            errors = _save_parameter_links(link.procedure, request.POST)
            if errors:
                for error in errors:
                    messages.error(request, error)
            else:
                messages.success(request, f"Ansur steps for {link.procedure} saved.")
            return redirect("calibration:ansur_settings")

        elif action == "remove_map":
            link = get_object_or_404(AnsurTemplateMap, pk=request.POST.get("map_id"))
            link.delete()
            if cfg.enabled and not AnsurTemplateMap.objects.exists():
                cfg.enabled = False
                cfg.save()
                messages.warning(request, "No procedure is linked any more, so Start with Ansur is switched off.")
            messages.success(request, f"{link.procedure} is no longer linked to Ansur.")
            return redirect("calibration:ansur_settings")

    maps = list(AnsurTemplateMap.objects.select_related("procedure"))
    templates = setup.list_templates(cfg.base_folder)
    available = {t.lower() for t in templates}
    for link in maps:
        link.file_found = link.template_file.lower() in available
    checks = setup.readiness(cfg, maps)
    for link in maps:
        link.problems = procedure_problems(link.procedure)
        link.parameters = list(link.procedure.parameters.filter(active_status=True, pending_delete=False)
                               .order_by("order", "name"))
    if maps:
        usable = [m for m in maps if not m.problems]
        checks.append(setup.Check(
            "Procedure set-up", bool(usable),
            f"{len(usable)} of {len(maps)} linked procedure(s) ready." if usable else
            "No linked procedure is fully set up yet.",
            "" if len(usable) == len(maps) else "Fill in the Ansur step and analyser accuracy for each "
                                                 "parameter below.",
        ))

    return render(request, "Calibrition/ansur_settings.html", {
        "form": form,
        "map_form": map_form,
        "cfg": cfg,
        "maps": maps,
        "templates": templates,
        "checks": checks,
        "ready": all(c.ok for c in checks),
        "folders": setup.folder_paths(cfg.base_folder),
        "unlinked_templates": [t for t in templates if t.lower() not in {m.template_file.lower() for m in maps}],
        "ready_count": sum(1 for m in maps if not m.problems),
        "standards": Standard.objects.filter(active_status=True).order_by("name"),
    })


# ── Start with Ansur (Perform Calibration) ───────────────────────────────────

logger = logging.getLogger(__name__)

STATUS_TEXT = {
    AnsurJob.PREPARED: "Work order written. Starting Ansur…",
    AnsurJob.SENT: "Ansur is open with this job. Run the test, then save it in Ansur.",
    AnsurJob.IMPORTED: "Result received and checked. The session is waiting for approval.",
    AnsurJob.REJECTED: "Ansur's record was refused. Fix the reason below, run the test again and save.",
    AnsurJob.CANCELLED: "Cancelled.",
}


def procedure_problems(procedure):
    """Why a procedure cannot run with Ansur yet (empty when it can)."""
    problems = []
    if not AnsurTemplateMap.objects.filter(procedure=procedure).exists():
        return ["The procedure is not linked to an Ansur template."]
    for parameter in procedure.parameters.filter(active_status=True, pending_delete=False):
        if not parameter.ansur_step:
            problems.append(f"{parameter.name} is not linked to an Ansur test step.")
        if parameter.analyser_accuracy_pct is None and parameter.analyser_accuracy_floor is None:
            problems.append(f"{parameter.name} has no analyser accuracy.")
        if not parameter.reference_uncertainty:
            problems.append(f"{parameter.name} has no reference uncertainty (from the analyser's certificate).")
        if parameter.sub_parameters.filter(active_status=True).exists():
            problems.append(f"{parameter.name} has sub-parameters, which Ansur results cannot fill.")
    return problems


def ansur_available(procedure_ids=None):
    """Procedure ids that show Start with Ansur, or an empty set when the
    connection is off or not ready."""
    cfg = AnsurSettings.load()
    maps = list(AnsurTemplateMap.objects.select_related("procedure"))
    if not cfg.enabled or not setup.is_ready(cfg, maps):
        return set()
    return {str(m.procedure_id) for m in maps if not procedure_problems(m.procedure)}


def job_payload(job):
    data = {
        "id": str(job.id),
        "job_number": job.job_number,
        "status": job.status,
        "label": job.get_status_display(),
        "text": STATUS_TEXT.get(job.status, ""),
        "error": job.error,
        "warning": job.warning,
        "open": job.is_open,
        "session_url": "",
    }
    if job.session_id:
        data["session_url"] = reverse("calibration:session_detail", args=[job.session_id])
    return data


def _decimal(value):
    try:
        return Decimal(str(value).strip()) if str(value or "").strip() else None
    except InvalidOperation:
        return None


def _error(message, status=400):
    return JsonResponse({"success": False, "error": message}, status=status)


@login_required
@require_POST
def ansur_start(request):
    equipment = Equipment.objects.filter(pk=request.POST.get("equipment") or None).first()
    procedure = CalibrationProcedure.objects.filter(
        pk=request.POST.get("procedure") or None, active_status=True).first()
    if equipment is None or procedure is None:
        return _error("Choose the equipment and the procedure first.")
    if str(procedure.pk) not in ansur_available():
        problems = procedure_problems(procedure)
        return _error("This procedure cannot run with Ansur yet"
                      + (": " + " ".join(problems) if problems else ". Check the Ansur connection page."))

    temperature = _decimal(request.POST.get("actual_temperature"))
    humidity = _decimal(request.POST.get("actual_humidity"))
    if temperature is None or humidity is None:
        return _error("Enter the room temperature and humidity before starting.")

    expired, _ = _reference_standard_status(procedure)
    if expired:
        return _error("A reference standard is past its calibration due date: "
                      + ", ".join(f"{s.name} (S/N {s.serial_number})" for s in expired) + ".")

    schedule = CalibrationSchedule.objects.filter(pk=request.POST.get("schedule") or None).first()
    cfg = AnsurSettings.load()
    link = AnsurTemplateMap.objects.get(procedure=procedure)

    try:
        with transaction.atomic():
            job = (AnsurJob.objects.select_for_update()
                   .filter(equipment=equipment, status__in=AnsurJob.OPEN).first())
            if job is None:
                job = AnsurJob(equipment=equipment, job_number=jobfile.new_job_number(), created_by=request.user)
            job.procedure, job.schedule, job.template_file = procedure, schedule, link.template_file
            job.actual_temperature, job.actual_humidity = temperature, humidity
            job.actual_pressure = _decimal(request.POST.get("actual_pressure"))
            job.notes = (request.POST.get("notes") or "").strip()
            job.status, job.error, job.warning = AnsurJob.PREPARED, "", ""
            job.save()
    except IntegrityError:
        return _error("Another Ansur job was just started for this equipment. Reload the page.")

    try:
        job.job_file = str(jobfile.write(job, cfg, link))
        launcher.start(cfg, job.job_file)
    except (OSError, launcher.LaunchError) as exc:
        job.error = str(exc)
        job.save(update_fields=["job_file", "error", "updated_at"])
        return JsonResponse({"success": False, "error": str(exc), "job": job_payload(job)}, status=502)

    job.status, job.sent_at = AnsurJob.SENT, timezone.now()
    job.save(update_fields=["job_file", "status", "sent_at", "updated_at"])
    return JsonResponse({"success": True, "job": job_payload(job)})


@login_required
@require_GET
def ansur_job_status(request):
    job = get_object_or_404(AnsurJob, pk=request.GET.get("job"))
    if job.status == AnsurJob.SENT:
        try:
            watcher.scan()
        except Exception:
            logger.exception("Ansur scan from the status check failed")
        job.refresh_from_db()
    payload = job_payload(job)
    if job.status == AnsurJob.SENT:
        # Lets the page say "Ansur is closed" when the technician shut it
        # without saving; None when it cannot tell (not Windows).
        payload["ansur_running"] = launcher.is_running(AnsurSettings.load())
    return JsonResponse({"success": True, "job": payload})


def _may_manage(user, job):
    """The person who started the job, the in-charge, or anyone who works the
    calibration centre's queue (so a colleague can finish a stuck job)."""
    from CalSoft.view_modules.pending_sessions import can_review_calibrations
    return (job.created_by_id == user.id or get_user_role(user) in ("HOD", "NIC")
            or can_review_calibrations(user))


@login_required
@require_POST
def ansur_job_action(request):
    job = get_object_or_404(AnsurJob, pk=request.POST.get("job"))
    if not _may_manage(request.user, job):
        return _error("Only the person who started this job, calibration-centre staff, the in-charge or the HOD "
                      "can change it.", 403)
    if not job.is_open:
        return _error(f"Job {job.job_number} is {job.get_status_display().lower()}.")

    action = request.POST.get("action")
    if action == "cancel":
        job.status = AnsurJob.CANCELLED
        job.save(update_fields=["status", "updated_at"])
        if job.job_file:
            Path(job.job_file).unlink(missing_ok=True)
        return JsonResponse({"success": True, "job": job_payload(job)})
    if action == "reopen":
        cfg = AnsurSettings.load()
        try:
            link = AnsurTemplateMap.objects.get(procedure=job.procedure)
            job.job_file = str(jobfile.write(job, cfg, link))
            launcher.start(cfg, job.job_file)
        except (AnsurTemplateMap.DoesNotExist, OSError, launcher.LaunchError) as exc:
            return _error(str(exc) or "The procedure is no longer linked to an Ansur template.", 502)
        job.status, job.error, job.sent_at = AnsurJob.SENT, "", timezone.now()
        job.save(update_fields=["job_file", "status", "error", "sent_at", "updated_at"])
        return JsonResponse({"success": True, "job": job_payload(job)})
    return _error("Unknown action.")


@login_required
@require_POST
def ansur_upload(request):
    """Fallback: import a record the technician picks by hand."""
    job = get_object_or_404(AnsurJob, pk=request.POST.get("job"))
    if not _may_manage(request.user, job):
        return _error("Only the person who started this job, calibration-centre staff, the in-charge or the HOD "
                      "can import for it.", 403)
    upload = request.FILES.get("record")
    if upload is None or not upload.name.lower().endswith(".mtr"):
        return _error("Choose the Ansur test record (.mtr) to import.")
    if not job.is_open:
        return _error(f"Job {job.job_number} is {job.get_status_display().lower()}.")
    try:
        import_record(job, upload.read(), file_name=upload.name)
    except ImportRefused as exc:
        refuse(job, exc.reasons)
        job.refresh_from_db()
        return JsonResponse({"success": False, "error": "The record was refused.", "job": job_payload(job)},
                            status=422)
    job.refresh_from_db()
    return JsonResponse({"success": True, "job": job_payload(job)})


@login_required
@require_GET
def ansur_session_pdf(request, pk):
    """Ansur's own detailed PDF for an Ansur session, as it was imported."""
    job = AnsurJob.objects.filter(session_id=pk).first()
    if job is None or not job.pdf_copy:
        raise Http404("No Ansur PDF for this session")
    with job.pdf_copy.open("rb") as handle:
        data = handle.read()
    if job.pdf_sha256 and hashlib.sha256(data).hexdigest() != job.pdf_sha256:
        logger.error("Ansur PDF for job %s no longer matches its fingerprint", job.job_number)
        raise Http404("The stored Ansur PDF has changed since it was imported")
    return FileResponse(BytesIO(data), content_type="application/pdf",
                        filename=f"Ansur-{job.job_number}.pdf")


def ansur_review_details(session):
    """What the review modal shows about an Ansur session, or None."""
    if session.source != "ansur":
        return None
    job = AnsurJob.objects.filter(session=session).first()
    pdf_url = reverse("calibration:ansur_session_pdf", args=[session.pk]) if job and job.pdf_copy else ""
    return {
        "job_number": session.ansur_job_number or (job.job_number if job else ""),
        "operator": session.ansur_operator,
        "disagreements": session.ansur_disagreements,
        "pdf_url": pdf_url,
        # The job, the record copy and Ansur's PDF stay on the PC that ran
        # Ansur; say so instead of showing nothing on any other PC.
        "pdf_note": "" if pdf_url else (
            "Ansur's PDF is kept on the Ansur PC. Open this session there to see it."
            if job is None else "Ansur's PDF could not be produced for this job."),
        "record_sha256": session.ansur_record_sha256,
        "checks": session.ansur_checks or [],
    }
