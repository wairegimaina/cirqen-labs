import json
import logging
import random
from calendar import monthrange
from datetime import datetime
from zoneinfo import ZoneInfo

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib import messages
from django.db.models import Count, Sum, Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone

from Inventory.models import Equipment, Department
from workshop.models import Workshop
from jobcard.models import jobcard
from ppms.models import PPMSchedule
from parts_tools.models import Tools, Accessories
from reporthub.models import Report
from users.models import UserProfile
from users.control import get_user_role
from CalSoft.models import Standard

logger = logging.getLogger(__name__)


def generate_greeting(user_first_name):
    """Return a random greeting based on the time of day in Nairobi."""
    local_time = timezone.localtime(timezone.now(), ZoneInfo("Africa/Nairobi"))
    hour = local_time.hour

    if 5 <= hour < 12:
        messages_list = [
            f"Good morning, {user_first_name}",
            f"Morning, {user_first_name}. Here's where things stand",
        ]
    elif 12 <= hour < 15:
        messages_list = [
            f"Good afternoon, {user_first_name}",
            f"Afternoon, {user_first_name}. Here's where things stand",
        ]
    elif 15 <= hour < 22:
        messages_list = [
            f"Good evening, {user_first_name}",
            f"Evening, {user_first_name}. Here's where things stand",
        ]
    else:
        messages_list = [
            f"Working late, {user_first_name}",
            f"Hello, {user_first_name}. Here's where things stand",
        ]

    return random.choice(messages_list)


@login_required
def Dashboard(request):
    """
    Main CMMS dashboard – computes all three data sets (Equipment Analytics,
    PPM Summary, Inventory Overview) server‑side and embeds them as JSON.
    """
    greeting = generate_greeting(request.user.first_name)
    try:
        profile = request.user.userprofile
        workshop = profile.workshop if profile.workshop else None
    except Exception:
        workshop = None

    # ---------- 1. Equipment Analytics ----------
    analytics_data = {
        "total_equipment": 0,
        "active_equipment": 0,
        "total_departments": 0,
        "by_category": {},
        "top_equipment": [],
    }
    if workshop:
        equipments = Equipment.objects.filter(
            department__workshop=workshop, department__active_status=True, active_status=True
        )
        total_equipment = equipments.count()
        active_equipment = equipments.filter(status="Working").count()
        total_departments = Department.objects.filter(workshop=workshop, active_status=True).count()

        dept_counts = (
            equipments.values("department__name")
            .annotate(count=Count("id"))
            .order_by("department__name")
        )
        by_category = {item["department__name"]: item["count"] for item in dept_counts}

        equipment_ids = list(equipments.values_list("id", flat=True))
        top_equipment = []
        if equipment_ids:
            jobcard_counts = (
                jobcard.objects.filter(equipment_id__in=equipment_ids)
                .values("equipment_id")
                .annotate(job_count=Count("id"))
                .order_by("-job_count")[:8]
            )
            eq_ids_with_counts = {
                item["equipment_id"]: item["job_count"] for item in jobcard_counts
            }
            eq_objs = Equipment.objects.filter(id__in=eq_ids_with_counts.keys()).select_related(
                "department", "description"
            )
            for eq in eq_objs:
                top_equipment.append(
                    {
                        "name": eq.description.name if eq.description else "Unknown",
                        "department": eq.department.name if eq.department else "Unknown",
                        "job_cards": eq_ids_with_counts.get(eq.id, 0),
                        "active_status": eq.active_status,
                    }
                )

        analytics_data = {
            "total_equipment": total_equipment,
            "active_equipment": active_equipment,
            "total_departments": total_departments,
            "by_category": by_category,
            "top_equipment": top_equipment,
        }

    # ---------- 2. PPM Summary ----------
    ppm_data = {
        "total": 0,
        "completed": 0,
        "overdue": 0,
        "pending": 0,
        "upcoming": 0,
        "monthly_breakdown": {},
    }
    if workshop:
        schedules = PPMSchedule.objects.filter(workshop=workshop)
        total = schedules.count()
        completed = schedules.filter(status="completed").count()
        pending = schedules.filter(status="pending").count()
        # pushed is not shown as a KPI, but we could include it if needed

        # Overdue: scheduled_month's last day < today and status != 'completed'
        today = timezone.now().date()
        overdue = 0
        upcoming = 0
        for s in schedules:
            if s.scheduled_month and s.status != "completed":
                last_day = monthrange(s.scheduled_month.year, s.scheduled_month.month)[1]
                month_end = s.scheduled_month.replace(day=last_day)
                if month_end < today:
                    overdue += 1
                elif s.scheduled_month > today.replace(day=1):
                    upcoming += 1

        # Monthly breakdown of completed schedules (for the trend chart)
        monthly_completed = (
            schedules.filter(status="completed")
            .values("scheduled_month")
            .annotate(count=Count("id"))
            .order_by("scheduled_month")
        )
        monthly_breakdown = {}
        for item in monthly_completed:
            dt = item["scheduled_month"]
            key = dt.strftime("%b %Y")  # e.g., "Jan 2026"
            monthly_breakdown[key] = item["count"]

        ppm_data = {
            "total": total,
            "completed": completed,
            "overdue": overdue,
            "pending": pending,
            "upcoming": upcoming,
            "monthly_breakdown": monthly_breakdown,
        }

    # ---------- 3. Inventory Overview ----------
    inventory_data = {
        "equipment": 0,
        "equipment_working": 0,
        "equipment_not_working": 0,
        "equipment_under_repair": 0,
        "accessories": 0,
        "accessories_stock_count": 0,
        "tools": 0,
        "inactive": 0,
    }
    if workshop:
        equipment_qs = Equipment.objects.filter(department__workshop=workshop, active_status=True)
        equipment_count = equipment_qs.count()
        equipment_working = equipment_qs.filter(status="Working").count()
        equipment_not_working = equipment_qs.filter(status="Not working").count()
        equipment_under_repair = equipment_qs.filter(status="Under repair").count()
        inactive_count = Equipment.objects.filter(
            department__workshop=workshop, active_status=False
        ).count()
        accessories_qs = Accessories.objects.filter(workshop=workshop)
        accessories_count = accessories_qs.count()
        accessories_stock_count = accessories_qs.aggregate(total=Sum("stock_count"))["total"] or 0
        tools_count = Tools.objects.filter(workshop=workshop).count()

        inventory_data = {
            "equipment": equipment_count,
            "equipment_working": equipment_working,
            "equipment_not_working": equipment_not_working,
            "equipment_under_repair": equipment_under_repair,
            "accessories": accessories_count,
            "accessories_stock_count": accessories_stock_count,
            "tools": tools_count,
            "inactive": inactive_count,
        }

    # ---------- Context ----------
    context = {
        "greeting": greeting,
        "username": request.user.username,
        "analytics_data": json.dumps(analytics_data),
        "ppm_data": json.dumps(ppm_data),
        "inventory_data": json.dumps(inventory_data),
        "initial_kpis": (
            {
                "equipment": analytics_data["total_equipment"],
                "departments": analytics_data["total_departments"],
                "ppm_schedules": ppm_data["total"],
            }
            if workshop
            else {}
        ),
    }

    return render(request, "dashboards/dashboard.html", context)


# ---------- NIC, Nurse, and other views (unchanged) ----------


@login_required
def nic_dashboard(request):
    profile = request.user.userprofile
    if profile.role != "NIC":
        return render(request, "unauthorized.html")
    department = profile.department
    equipments = Equipment.objects.filter(department=department, active_status=True)
    jobcards = jobcard.objects.filter(equipment__department=department)
    ppm_schedules = PPMSchedule.objects.filter(equipment__department=department)

    equipment_status = {
        item["status"]: item["count"]
        for item in equipments.values("status").annotate(count=Count("id"))
    }
    jobcard_status = {
        item["status"]: item["count"]
        for item in jobcards.values("status").annotate(count=Count("id"))
    }

    context = {
        "department": department,
        "equipments": equipments,
        "jobcards": jobcards,
        "ppm_schedules": ppm_schedules,
        "greeting": generate_greeting(request.user.first_name),
        "username": request.user.username,
        "equipment_status_json": json.dumps(equipment_status),
        "jobcard_status_json": json.dumps(jobcard_status),
    }
    return render(request, "dashboards/nurse_dashboard.html", context)


@login_required
def nurse_inventory(request):
    profile = request.user.userprofile
    department = profile.department
    equipments = Equipment.objects.filter(department=department, active_status=True)
    return render(
        request,
        "inventory/nurse_inventory.html",
        {
            "equipments": equipments,
            "greeting": generate_greeting(request.user.first_name),
            "username": request.user.username,
        },
    )


@login_required
def nurse_ppms(request):
    try:
        profile = request.user.userprofile
        department = profile.department
    except AttributeError:
        logger.warning(f"User {request.user.username} has no profile or department.")
        messages.error(
            request,
            "You do not have a user profile or department assigned. Please contact the administrator.",
        )
        return redirect("custom_login")

    schedules = (
        PPMSchedule.objects.select_related("equipment__department")
        .filter(equipment__department=department)
        .order_by("scheduled_month", "equipment__description")
    )

    if not schedules.exists():
        logger.info(
            f"No PPM schedules found for department {department.name} (ID: {department.id})"
        )
        messages.info(request, f"No PPM schedules available for {department.name}.")

    # Simple, genuinely meaningful counts for the KPI strip — no new
    # queries beyond what's already filtered above, just aggregated.
    today = timezone.localdate()
    total_count = schedules.count()
    completed_count = schedules.filter(status="completed").count()
    overdue_count = schedules.filter(status="pending", scheduled_month__lt=today.replace(day=1)).count()

    return render(
        request,
        "PPM/nurse_ppms.html",
        {
            "schedules": schedules,
            "department": department,
            "greeting": generate_greeting(request.user.first_name),
            "username": request.user.username,
            "total_ppm_count": total_count,
            "completed_ppm_count": completed_count,
            "overdue_ppm_count": overdue_count,
            # The template already referenced these two (current_month_name/
            # current_year) and access_context.department_name without this
            # view ever providing them — rendered silently blank. Fixed here;
            # department_name below now matches the template's own reference.
            "current_month_name": today.strftime("%B"),
            "current_year": today.year,
            "access_context": {"department_name": department.name},
        },
    )


# ---------- HOD Dashboard with corrected PPM monthly breakdown ----------


@login_required
def hod_dashboard(request):
    try:
        profile = UserProfile.objects.get(user=request.user)
        if profile.role != "HOD":
            return redirect("custom_login")
    except UserProfile.DoesNotExist:
        return redirect("custom_login")

    workshops = Workshop.objects.all()
    context = {
        "workshops": workshops,
        "workshop_data": {},
        "greeting": generate_greeting(request.user.first_name),
        "username": request.user.username,
        "current_year": datetime.now().year,
    }

    total_monthly_breakdown = {}
    per_workshop_monthly_by_key = {}  # workshop.name -> {"YYYY-MM": count}
    month_label_by_key = {}  # "YYYY-MM" -> "Mon YYYY", for chronological sorting

    for workshop in workshops:
        # ---- Job card counts ----
        if workshop.category == "calibration_center":
            job_cards_count = jobcard.objects.filter(workshop=workshop).count()
            waiting_approval_count = jobcard.objects.filter(
                workshop=workshop, status="Waiting Approval"
            ).count()
            approved_count = jobcard.objects.filter(workshop=workshop, status="Approved").count()
            declined_count = jobcard.objects.filter(workshop=workshop, status="Declined").count()
        else:
            job_cards_count = (
                jobcard.objects.filter(department__workshop=workshop)
                .exclude(workshop__category="calibration_center")
                .count()
            )
            waiting_approval_count = (
                jobcard.objects.filter(department__workshop=workshop, status="Waiting Approval")
                .exclude(workshop__category="calibration_center")
                .count()
            )
            approved_count = (
                jobcard.objects.filter(department__workshop=workshop, status="Approved")
                .exclude(workshop__category="calibration_center")
                .count()
            )
            declined_count = (
                jobcard.objects.filter(department__workshop=workshop, status="Declined")
                .exclude(workshop__category="calibration_center")
                .count()
            )
            calibration_work_count = jobcard.objects.filter(
                department__workshop=workshop, workshop__category="calibration_center"
            ).count()

        # ---- PPM monthly breakdown (completed only) ----
        ppm_schedules = PPMSchedule.objects.filter(workshop=workshop)
        monthly_completed = (
            ppm_schedules.filter(status="completed")
            .values("scheduled_month")
            .annotate(count=Count("id"))
            .order_by("scheduled_month")
        )
        workshop_monthly = {}
        workshop_monthly_by_key = {}
        for item in monthly_completed:
            dt = item["scheduled_month"]
            key = dt.strftime("%b %Y")
            workshop_monthly[key] = item["count"]

            sort_key = dt.strftime("%Y-%m")
            workshop_monthly_by_key[sort_key] = item["count"]
            month_label_by_key[sort_key] = key

        per_workshop_monthly_by_key[workshop.name] = workshop_monthly_by_key

        # Aggregate totals
        for key, count in workshop_monthly.items():
            total_monthly_breakdown[key] = total_monthly_breakdown.get(key, 0) + count

        # ---- Workshop info ----
        workshop_info = {
            "equipment_count": Equipment.objects.filter(
                department__workshop=workshop, active_status=True
            ).count(),
            "departments_count": Department.objects.filter(workshop=workshop).count(),
            "ppms_count": ppm_schedules.count(),
            "reports_count": Report.objects.filter(workshop=workshop).count(),
            "accessories_count": Accessories.objects.filter(workshop=workshop).count(),
            "tools_count": Tools.objects.filter(workshop=workshop).count(),
            "job_cards_count": job_cards_count,
            "waiting_approval_count": waiting_approval_count,
            "approved_count": approved_count,
            "declined_count": declined_count,
            "is_calibration_center": workshop.category == "calibration_center",
            "ppm_monthly_breakdown": workshop_monthly,
        }
        if workshop.category != "calibration_center":
            workshop_info["calibration_work_count"] = calibration_work_count

        context["workshop_data"][workshop.id] = workshop_info

    context["totals"] = {
        "total_equipment": sum(
            data["equipment_count"] for data in context["workshop_data"].values()
        ),
        "total_departments": sum(
            data["departments_count"] for data in context["workshop_data"].values()
        ),
        "total_ppms": sum(data["ppms_count"] for data in context["workshop_data"].values()),
        "total_reports": sum(data["reports_count"] for data in context["workshop_data"].values()),
        "total_job_cards": sum(
            data["job_cards_count"] for data in context["workshop_data"].values()
        ),
        "total_accessories": sum(
            data["accessories_count"] for data in context["workshop_data"].values()
        ),
        "total_tools": sum(data["tools_count"] for data in context["workshop_data"].values()),
        "total_waiting_approval": sum(
            data["waiting_approval_count"] for data in context["workshop_data"].values()
        ),
        "total_approved": sum(data["approved_count"] for data in context["workshop_data"].values()),
        "total_declined": sum(data["declined_count"] for data in context["workshop_data"].values()),
        "total_ppm_monthly_breakdown": total_monthly_breakdown,
    }

    context["jobcard_status_json"] = json.dumps(
        {
            "Approved": context["totals"]["total_approved"],
            "Waiting Approval": context["totals"]["total_waiting_approval"],
            "Declined": context["totals"]["total_declined"],
        }
    )
    context["ppm_monthly_json"] = json.dumps(total_monthly_breakdown)

    # Per-workshop PPM completions, one colour-coded series per workshop,
    # sharing a single chronologically-sorted month axis.
    sorted_month_keys = sorted(month_label_by_key.keys())
    context["ppm_monthly_labels_json"] = json.dumps(
        [month_label_by_key[k] for k in sorted_month_keys]
    )
    context["ppm_monthly_by_workshop_json"] = json.dumps(
        {
            name: [counts.get(k, 0) for k in sorted_month_keys]
            for name, counts in per_workshop_monthly_by_key.items()
        }
    )

    return render(request, "dashboards/hod_dashboard.html", context)


def hod_logout_view(request):
    logout(request)
    return redirect("custom_login")


@login_required
def global_search(request):
    """
    Cross-app search endpoint for the header command palette (Ctrl+K).
    Scoped by role the same way the existing list views are: HODs see
    everything, Techs/NICs see only their own workshop/department.
    """
    query = request.GET.get("q", "").strip()
    results = {"equipment": [], "jobcards": [], "standards": []}

    if len(query) < 2:
        return JsonResponse({"results": results})

    try:
        profile = request.user.userprofile
    except AttributeError:
        return JsonResponse({"results": results})

    role = get_user_role(request.user)

    equipment_qs = Equipment.objects.filter(active_status=True).select_related(
        "description", "department"
    )
    jobcard_qs = jobcard.objects.filter(active_status=True).select_related(
        "equipment", "department"
    )

    if role != "HOD":
        if getattr(profile, "workshop", None):
            equipment_qs = equipment_qs.filter(department__workshop=profile.workshop)
            jobcard_qs = jobcard_qs.filter(department__workshop=profile.workshop)
        elif getattr(profile, "department", None):
            equipment_qs = equipment_qs.filter(department=profile.department)
            jobcard_qs = jobcard_qs.filter(department=profile.department)
        else:
            equipment_qs = equipment_qs.none()
            jobcard_qs = jobcard_qs.none()

    for eq in equipment_qs.filter(
        Q(serial_number__icontains=query)
        | Q(model__icontains=query)
        | Q(description__name__icontains=query)
    )[:8]:
        results["equipment"].append(
            {
                "title": f"{eq.description.name if eq.description else 'Equipment'} — {eq.serial_number}",
                "subtitle": eq.department.name if eq.department else "",
                "url": reverse("edit_inventory", args=[eq.id]),
            }
        )

    for jc in jobcard_qs.filter(
        Q(job_description__icontains=query)
        | Q(equipment__serial_number__icontains=query)
        | Q(equipment__description__name__icontains=query)
    )[:8]:
        results["jobcards"].append(
            {
                "title": f"{jc.get_action_taken_display()} — {jc.equipment.description.name if jc.equipment and jc.equipment.description else 'Job card'}",
                "subtitle": f"{jc.status} · {jc.equipment.serial_number if jc.equipment else ''}",
                "url": reverse("jobcard:download_jobcard_pdf", args=[jc.id]),
            }
        )

    for std in Standard.objects.filter(active_status=True).filter(
        Q(name__icontains=query)
        | Q(serial_number__icontains=query)
        | Q(certificate_number__icontains=query)
    )[:8]:
        results["standards"].append(
            {
                "title": std.name,
                "subtitle": f"S/N {std.serial_number}",
                "url": reverse("calibration:standard_edit", args=[std.id]),
            }
        )

    return JsonResponse({"results": results})


# well
