"""Ansur connection page: where Cirqen finds Ansur on this PC and which
Ansur template runs each calibration procedure.

One page, one form per job, each posted with an ``action`` so the page stays
a single place to set everything up and to see what still needs doing.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from CalSoft.ansur import setup
from CalSoft.models import AnsurSettings, AnsurTemplateMap, CalibrationProcedure
from users.control import role_required


class AnsurSettingsForm(forms.ModelForm):
    class Meta:
        model = AnsurSettings
        fields = ["program_path", "base_folder", "delete_job_files", "archive_years", "enabled"]
        labels = {
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


@login_required
@role_required("HOD")
def ansur_settings(request):
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

    return render(request, "Calibrition/ansur_settings.html", {
        "form": form,
        "map_form": map_form,
        "cfg": cfg,
        "maps": maps,
        "templates": templates,
        "checks": checks,
        "ready": all(c.ok for c in checks),
        "folders": setup.folder_paths(cfg.base_folder),
    })
