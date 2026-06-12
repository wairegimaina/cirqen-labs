from datetime import timezone
import logging
import random
from zoneinfo import ZoneInfo
from django.shortcuts import redirect, render
from Inventory.models import Equipment
from jobcard.models import JobCard
from django.contrib.auth.decorators import login_required
from ppms.models import PPMSchedule
logger = logging.getLogger(__name__)
from django.contrib.auth.decorators import login_required
from Inventory.models import Department
from django.contrib import messages
from ppms.models import PPMSchedule
from users.models import UserProfile


from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from workshop.models import Workshop
from Inventory.models import Equipment, Department
from jobcard.models import JobCard
from parts_tools.models import Tools, Accessories
from ppms.models import PPMSchedule
from reporthub.models import Report

def generate_greeting(user_first_name):
    from django.utils import timezone
    import random
    from zoneinfo import ZoneInfo

    # Force Nairobi timezone
    local_time = timezone.localtime(timezone.now(), ZoneInfo("Africa/Nairobi"))
    hour = local_time.hour

    if 5 <= hour < 12:  # Morning
        messages = [
            f"Good morning, {user_first_name}! Wishing you a bright and productive start 🌞",
            f"Rise and shine, {user_first_name}! Let’s make today count 🚀",
            f"Morning {user_first_name}! A new day brings new opportunities ✨",
            f"Good morning, {user_first_name}! Don’t forget your coffee ☕",
            f"Hello {user_first_name}, may your morning be filled with energy and focus 💡",
        ]
    elif 12 <= hour < 15:  # Afternoon
        messages = [
            f"Good afternoon, {user_first_name}! I hope your day is going smoothly 🌼",
            f"Hello {user_first_name}, wishing you a productive and positive afternoon ☀️",
            f"Good afternoon, {user_first_name}! Keep up the great work, you’re doing amazing 💪",
            f"Hi {user_first_name}, hope your afternoon is filled with focus and good energy ✨",
            f"Good afternoon, {user_first_name}! Remember to take a short break and recharge ☕",
        ]
    elif 15 <= hour < 22:  # Evening
        messages = [
            f"Good evening, {user_first_name}! Hope you had a successful day 🌆",
            f"Evening vibes, {user_first_name}! Time to wrap things up strong 💼",
            f"Good evening, {user_first_name}! You’ve done great today 👏",
            f"Relax and recharge, {user_first_name}. You’ve earned it ✨",
            f"Hello {user_first_name}, may your evening be peaceful and fulfilling 🌙",
        ]
    else:  # Late night
        messages = [
            f"Burning the midnight oil, {user_first_name}? Keep pushing 🔥",
            f"Late shift hero, {user_first_name}! Stay strong 🌙",
            f"Still going strong, {user_first_name}? Much respect 🙌",
            f"Midnight hustle mode: ON, {user_first_name} ⚡",
            f"Working under the stars, {user_first_name}. Don’t forget to rest ✨",
        ]

    return random.choice(messages)
@login_required
def Dashboard(request):
    greeting = generate_greeting(request.user.first_name)
    return render(request, 'dashboards/dashboard.html', {
        'greeting': greeting,
        'username': request.user.username,   # ✅ add username
    })


@login_required
def nic_dashboard(request):
    profile = request.user.userprofile

    if profile.role != 'NIC':
        return render(request, 'unauthorized.html')

    department = profile.department
    equipments = Equipment.objects.filter(department=department)
    jobcards = JobCard.objects.filter(equipment__department=department)
    PPM_Schedule = PPMSchedule.objects.filter(equipment__department=department)

    context = {
        'department': department,
        'equipments': equipments,
        'jobcards': jobcards,
        'PPM_Schedule': PPM_Schedule,
        'greeting': generate_greeting(request.user.first_name),
        'username': request.user.username,   # ✅ add username
    }
    return render(request, 'dashboards/nurse_dashboard.html', context)


@login_required
def nurse_inventory(request):
    profile = request.user.userprofile
    department = profile.department
    equipments = Equipment.objects.filter(department=department)

    return render(request, 'Inventory/nurse_inventory.html', {
        'equipments': equipments,
        'greeting': generate_greeting(request.user.first_name),
        'username': request.user.username,   # ✅ add username
    })


@login_required
def nurse_PPMs(request):
    try:
        profile = request.user.userprofile
        department = profile.department
    except AttributeError:
        logger.warning(f"User {request.user.username} has no profile or department.")
        messages.error(request, "You do not have a user profile or department assigned. Please contact the administrator.")
        return redirect('custom_login')

    schedules = PPMSchedule.objects.select_related('equipment__department').filter(
        equipment__department=department
    ).order_by('scheduled_month', 'equipment__description')

    if not schedules.exists():
        logger.info(f"No PPM schedules found for department {department.name} (ID: {department.id})")
        messages.info(request, f"No PPM schedules available for {department.name}.")

    return render(request, 'PPM/nurse_ppms.html', {
        'schedules': schedules,
        'department': department,
        'greeting': generate_greeting(request.user.first_name),
        'username': request.user.username,   # ✅ add username
    })


@login_required
def hod_dashboard(request):
    try:
        profile = UserProfile.objects.get(user=request.user)
        if profile.role != 'HOD':
            return redirect('custom_login')
    except UserProfile.DoesNotExist:
        return redirect('custom_login')

    workshops = Workshop.objects.all()
    context = {
        'workshops': workshops,
        'workshop_data': {},
        'greeting': generate_greeting(request.user.first_name),
        'username': request.user.username,   # ✅ add username
    }

    for workshop in workshops:
        workshop_info = {
            'equipment_count': Equipment.objects.filter(department__workshop=workshop).count(),
            'departments_count': Department.objects.filter(workshop=workshop).count(),
            'ppms_count': PPMSchedule.objects.filter(workshop=workshop).count(),
            'reports_count': Report.objects.filter(workshop=workshop).count(),
            'job_cards_count': JobCard.objects.filter(department__workshop=workshop).count(),
            'accessories_count': Accessories.objects.filter(workshop=workshop).count(),
            'tools_count': Tools.objects.filter(workshop=workshop).count(),
        }
        context['workshop_data'][workshop.id] = workshop_info

    context['totals'] = {
        'total_equipment': sum(data['equipment_count'] for data in context['workshop_data'].values()),
        'total_departments': sum(data['departments_count'] for data in context['workshop_data'].values()),
        'total_ppms': sum(data['ppms_count'] for data in context['workshop_data'].values()),
        'total_reports': sum(data['reports_count'] for data in context['workshop_data'].values()),
        'total_job_cards': sum(data['job_cards_count'] for data in context['workshop_data'].values()),
        'total_accessories': sum(data['accessories_count'] for data in context['workshop_data'].values()),
        'total_tools': sum(data['tools_count'] for data in context['workshop_data'].values()),
    }

    return render(request, 'hod_dashboard.html', context)
def hod_logout_view(request):
    logout(request)
    return redirect('custom_login')
