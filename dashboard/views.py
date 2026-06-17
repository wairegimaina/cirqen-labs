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
from django.db.models import Count, Sum
from django.utils import timezone

from Inventory.models import Equipment, Department
from workshop.models import Workshop
from jobcard.models import jobcard
from ppms.models import PPMSchedule
from parts_tools.models import Tools, Accessories
from reporthub.models import Report
from users.models import UserProfile

logger = logging.getLogger(__name__)


def generate_greeting(user_first_name):
    """Return a random greeting based on the time of day in Nairobi."""
    local_time = timezone.localtime(timezone.now(), ZoneInfo("Africa/Nairobi"))
    hour = local_time.hour

    if 5 <= hour < 12:
        messages_list = [
            f"Good morning, {user_first_name}! Wishing you a bright and productive start 🌞",
            f"Rise and shine, {user_first_name}! Let’s make today count 🚀",
            f"Morning {user_first_name}! A new day brings new opportunities ✨",
            f"Good morning, {user_first_name}! Don’t forget your coffee ☕",
            f"Hello {user_first_name}, may your morning be filled with energy and focus 💡",
        ]
    elif 12 <= hour < 15:
        messages_list = [
            f"Good afternoon, {user_first_name}! I hope your day is going smoothly 🌼",
            f"Hello {user_first_name}, wishing you a productive and positive afternoon ☀️",
            f"Good afternoon, {user_first_name}! Keep up the great work, you’re doing amazing 💪",
            f"Hi {user_first_name}, hope your afternoon is filled with focus and good energy ✨",
            f"Good afternoon, {user_first_name}! Remember to take a short break and recharge ☕",
        ]
    elif 15 <= hour < 22:
        messages_list = [
            f"Good evening, {user_first_name}! Hope you had a successful day 🌆",
            f"Evening vibes, {user_first_name}! Time to wrap things up strong 💼",
            f"Good evening, {user_first_name}! You’ve done great today 👏",
            f"Relax and recharge, {user_first_name}. You’ve earned it ✨",
            f"Hello {user_first_name}, may your evening be peaceful and fulfilling 🌙",
        ]
    else:
        messages_list = [
            f"Burning the midnight oil, {user_first_name}? Keep pushing 🔥",
            f"Late shift hero, {user_first_name}! Stay strong 🌙",
            f"Still going strong, {user_first_name}? Much respect 🙌",
            f"Midnight hustle mode: ON, {user_first_name} ⚡",
            f"Working under the stars, {user_first_name}. Don’t forget to rest ✨",
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
    context = {
        "department": department,
        "equipments": equipments,
        "jobcards": jobcards,
        "ppm_schedules": ppm_schedules,
        "greeting": generate_greeting(request.user.first_name),
        "username": request.user.username,
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

    return render(
        request,
        "PPM/nurse_ppms.html",
        {
            "schedules": schedules,
            "department": department,
            "greeting": generate_greeting(request.user.first_name),
            "username": request.user.username,
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
        for item in monthly_completed:
            dt = item["scheduled_month"]
            key = dt.strftime("%b %Y")
            workshop_monthly[key] = item["count"]

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

    return render(request, "dashboards/hod_dashboard.html", context)


def hod_logout_view(request):
    logout(request)
    return redirect("custom_login")


# well
