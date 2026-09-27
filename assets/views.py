"""Asset pages: KPIs, machine page, QR labels, service contracts and low stock.

Suppliers, warranties and failure risk belong to the Inventory and Machine
Reports modules; these pages link to them and read their records."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.scoping import for_user
from Inventory.models import Department, Equipment, Supplier, Warranty
from jobcard.models import jobcard
from parts_tools.models import Accessories
from users.control import get_user_role
from workshop.models import Workshop

from . import kpis as kpi_module
from .forms import AssetDetailsForm, ContractForm
from .models import ServiceContract

PERIODS = (3, 6, 12)


def can_manage(user):
    """HODs and a workshop's Engineer In-charge keep asset records."""
    role = get_user_role(user)
    profile = getattr(user, "userprofile", None)
    return role == "HOD" or (role == "Tech" and getattr(profile, "level", "") == "Engineer Incharge")


def _machines(request):
    return for_user(Equipment.objects.filter(active_status=True), request.user)


@login_required
def kpis(request):
    machines = _machines(request)
    workshops = Workshop.objects.none()
    selected_workshop = None
    if get_user_role(request.user) == "HOD":
        workshops = Workshop.objects.order_by("name")
        if request.GET.get("workshop"):
            selected_workshop = workshops.filter(pk=request.GET["workshop"]).first()
            if selected_workshop:
                machines = machines.filter(department__workshop=selected_workshop)
    try:
        months = int(request.GET.get("months", 12))
    except ValueError:
        months = 12
    months = months if months in PERIODS else 12
    return render(request, "assets/kpis.html", {
        "k": kpi_module.compute(machines, months=months),
        "months": months, "periods": PERIODS,
        "workshops": workshops, "selected_workshop": selected_workshop,
    })


def _machine_or_404(request, pk):
    try:
        machine = _machines(request).select_related("description", "department__workshop",
                                                    "manufacturer").filter(pk=pk).first()
    except (ValueError, ValidationError):
        machine = None
    if not machine:
        raise Http404("Machine not found in your area")
    return machine


@login_required
def machine(request, pk):
    from CalSoft.models import CalibrationSession

    item = _machine_or_404(request, pk)
    manager = can_manage(request.user)
    form = AssetDetailsForm(instance=item)
    if request.method == "POST":
        if not manager:
            raise PermissionDenied("Only the HOD or the workshop's Engineer In-charge can change asset details.")
        form = AssetDetailsForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Asset details saved.")
            return redirect("assets:machine", pk=item.pk)
    from machineReports.prediction import predict

    risk = next(iter(predict(Equipment.objects.filter(pk=item.pk))), None)
    today = timezone.localdate()
    warranty = (Warranty.objects.filter(equipment=item, active_status=True)
                .select_related("supplier").order_by("-expiry_date").first())
    return render(request, "assets/machine.html", {
        "m": item, "form": form, "can_manage": manager, "risk": risk, "today": today, "warranty": warranty,
        "contracts": item.service_contracts.filter(active_status=True).select_related("supplier"),
        "work_orders": jobcard.objects.filter(equipment=item).order_by("-date_issued", "-created_at")[:10],
        "calibrations": CalibrationSession.objects.filter(device_serial=item.serial_number, active_status=True)
        .exclude(status="rejected").order_by("-timestamp")[:5],
        "is_tech": get_user_role(request.user) == "Tech",
    })


@login_required
def labels(request):
    departments = for_user(Department.objects.order_by("name"), request.user)
    department = None
    if request.GET.get("department"):
        department = departments.filter(pk=request.GET["department"]).first()
    if department and request.GET.get("download"):
        machines = (_machines(request).filter(department=department)
                    .select_related("description", "department").order_by("description__name", "serial_number"))
        from django.conf import settings

        from .labels import labels_pdf

        base = getattr(settings, "SITE_URL", "") or request.build_absolute_uri("/").rstrip("/")

        def url_for(m):
            return f"{base.rstrip('/')}{reverse('assets:machine', args=[m.pk])}"

        response = HttpResponse(labels_pdf(machines, url_for), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="qr-labels-{department.name}.pdf"'
        return response
    return render(request, "assets/labels.html", {
        "departments": departments, "department": department,
        "count": _machines(request).filter(department=department).count() if department else None,
    })


@login_required
def contracts(request):
    machines = _machines(request)
    today = timezone.localdate()
    horizon = today + timedelta(days=60)
    manager = can_manage(request.user)
    form = ContractForm(equipment_qs=machines)
    if request.method == "POST":
        if not manager:
            raise PermissionDenied("Only the HOD or the workshop's Engineer In-charge can add contracts.")
        form = ContractForm(request.POST, equipment_qs=machines)
        if form.is_valid():
            contract = form.save()
            messages.success(request, f"Contract saved for S/N {contract.equipment.serial_number}.")
            return redirect("assets:contracts")
    rows = (ServiceContract.objects.filter(equipment__in=machines, active_status=True, end_date__gte=today)
            .select_related("equipment__description", "equipment__department", "supplier"))
    return render(request, "assets/contracts.html", {
        "form": form, "can_manage": manager, "today": today, "horizon": horizon,
        "contracts": Paginator(rows, 50).get_page(request.GET.get("page")),
    })


@login_required
def stock_alerts(request):
    parts = for_user(Accessories.objects.filter(active_status=True), request.user).select_related(
        "name", "workshop", "supplier")
    manager = can_manage(request.user)
    if request.method == "POST":
        if not manager:
            raise PermissionDenied("Only the HOD or the workshop's Engineer In-charge can set reorder levels.")
        part = parts.filter(pk=request.POST.get("part")).first()
        if part:
            try:
                part.reorder_level = max(0, int(request.POST.get("reorder_level", 0)))
            except ValueError:
                messages.error(request, "The reorder level must be a whole number.")
                return redirect("assets:stock_alerts")
            supplier_id = request.POST.get("supplier") or None
            part.supplier = Supplier.objects.filter(pk=supplier_id).first() if supplier_id else None
            part.save(update_fields=["reorder_level", "supplier", "updated_at"])
            messages.success(request, f"Reorder level for {part.name} set to {part.reorder_level}.")
        return redirect(f"{reverse('assets:stock_alerts')}?q={request.POST.get('q', '')}")
    query = request.GET.get("q", "").strip()
    listing = parts.order_by("name__name")
    if query:
        listing = listing.filter(Q(name__name__icontains=query) | Q(supplier__name__icontains=query))
    return render(request, "assets/stock_alerts.html", {
        "low": parts.filter(reorder_level__gt=0, stock_count__lte=F("reorder_level")).order_by("stock_count"),
        "parts": Paginator(listing, 40).get_page(request.GET.get("page")),
        "suppliers": Supplier.objects.filter(active_status=True), "q": query, "can_manage": manager,
    })
