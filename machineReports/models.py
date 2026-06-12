import uuid
from django.db import models
from django.contrib.auth import get_user_model
User = get_user_model()

from django.utils import timezone
from datetime import datetime, timedelta
from django.db.models import Sum, Count, Q
from django.core.exceptions import ValidationError
from Inventory.models import Equipment, Department, Workshop
from CalSoft.models import CalibrationSession
from jobcard.models import jobcard, SparePartUsed


class EquipmentCategory(models.Model):
    active_status = models.BooleanField(default=True)
    """Categories for equipment (Critical vs General)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_critical = models.BooleanField(default=False)

    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    pending_delete = models.BooleanField(default=False)




    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:
        verbose_name_plural = "Equipment Categories"
        ordering = ['-is_critical', 'name']
        db_table = 'machineReports_equipmentcategory'

    def __str__(self):
        return f"{self.name} ({'Critical' if self.is_critical else 'General'})"

    def save(self, *args, **kwargs):
        # Run validations + normalization before saving
        self.needs_sync = True
        self.full_clean()
        super().save(*args, **kwargs)

    def to_payload(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description or "",
            "is_critical": self.is_critical,
            "updated_at": self.updated_at.isoformat(),  # keep this for conflict resolution
        }

class EquipmentStatusReport(models.Model):
    active_status = models.BooleanField(default=True)
    """Snapshot of equipment status at a point in time"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now)
    total_equipment = models.PositiveIntegerField()
    working_equipment = models.PositiveIntegerField()
    under_repair_equipment = models.PositiveIntegerField()
    not_working_equipment = models.PositiveIntegerField()
    pending_delete = models.BooleanField(default=False)


    # Critical equipment metrics
    critical_equipment_total = models.PositiveIntegerField()
    critical_equipment_working = models.PositiveIntegerField()
    critical_equipment_under_repair = models.PositiveIntegerField()
    critical_equipment_not_working = models.PositiveIntegerField()
        # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)



    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:
        ordering = ['-timestamp']
        get_latest_by = 'timestamp'

    @classmethod
    def generate_report(cls):
        """Generate a new status report snapshot"""
        # Get all equipment counts
        equipment_qs = Equipment.objects.all()
        total_equipment = equipment_qs.count()

        # Get status counts
        status_counts = equipment_qs.values('status').annotate(count=Count('id'))
        status_map = {s['status']: s['count'] for s in status_counts}

        # Get critical equipment counts
        critical_qs = equipment_qs.filter(category__is_critical=True)
        critical_total = critical_qs.count()
        critical_status_counts = critical_qs.values('status').annotate(count=Count('id'))
        critical_status_map = {s['status']: s['count'] for s in critical_status_counts}



        return cls.objects.create(
            timestamp=timezone.now(),
            total_equipment=total_equipment,
            working_equipment=status_map.get('Working', 0),
            under_repair_equipment=status_map.get('Under repair', 0),
            not_working_equipment=status_map.get('Not working', 0),
            critical_equipment_total=critical_total,
            critical_equipment_working=critical_status_map.get('Working', 0),
            critical_equipment_under_repair=critical_status_map.get('Under repair', 0),
            critical_equipment_not_working=critical_status_map.get('Not working', 0),
        )



    def __str__(self):
        return f"Equipment Status Report - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

class MachineRepairHistory(models.Model):
    """Aggregated repair history for equipment"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name='repair_history')
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()
    repair_count = models.PositiveIntegerField(default=0)
    total_downtime_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    job_cards = models.ManyToManyField('jobcard.jobcard', related_name='repair_history')
    active_status = models.BooleanField(default=True)
        # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    pending_delete = models.BooleanField(default=False)



    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:
        unique_together = ('equipment', 'year', 'month')
        ordering = ['-year', '-month']
        verbose_name_plural = "Machine Repair Histories"

    @classmethod
    def update_repair_history(cls, year=None, month=None):
        """Update repair history for a specific month/year or current month if not specified"""
        now = timezone.now()
        year = year or now.year
        month = month or now.month

        # Get all approved repair job cards for the period
        job_cards = jobcard.objects.filter(
            date_issued__year=year,
            date_issued__month=month,
            action_taken='Repair',
            status='Approved'
        )

        # Group by equipment and calculate metrics
        for equipment in Equipment.objects.filter(jobcard__in=job_cards).distinct():
            equipment_job_cards = job_cards.filter(equipment=equipment)

            # Calculate total downtime
            total_downtime = timedelta()
            for jc in equipment_job_cards:
                if jc.time_started and jc.time_completed:
                    start_time = datetime.combine(jc.date_issued, jc.time_started)
                    end_time = datetime.combine(jc.date_issued, jc.time_completed)
                    downtime = end_time - start_time
                    total_downtime += downtime

            # Create or update repair history
            repair_history, created = cls.objects.update_or_create(
                equipment=equipment,
                year=year,
                month=month,
                defaults={
                    'repair_count': equipment_job_cards.count(),
                    'total_downtime_hours': total_downtime.total_seconds() / 3600,
                }
            )

            # Link job cards
            repair_history.job_cards.set(equipment_job_cards)

    def get_spare_parts(self):
        """Get all spare parts used in repairs for this period"""
        return SparePartUsed.objects.filter(
            job_card__in=self.job_cards.all()
        ).select_related('part', 'job_card')

    def get_calibration_sessions(self):
        """Get calibration sessions for this equipment during this period"""
        from CalSoft.models import CalibrationSession
        from datetime import date

        # Create date range for the month/year
        start_date = date(self.year, self.month, 1)
        if self.month == 12:
            end_date = date(self.year + 1, 1, 1)
        else:
            end_date = date(self.year, self.month + 1, 1)

        # Query calibration sessions through schedules reverse relationship
        # CalibrationSchedule.calibration_session -> CalibrationSession (related_name='schedules')
        return CalibrationSession.objects.filter(
            schedules__equipment=self.equipment,
            timestamp__gte=start_date,
            timestamp__lt=end_date,
            active_status=True
        ).select_related('performed_by', 'procedure').distinct().order_by('-timestamp')

    def __str__(self):
        return f"{self.equipment} - {self.year}-{self.month}: {self.repair_count} repairs"


class WorkshopEquipmentReport(models.Model):
    active_status = models.BooleanField(default=True)
    """Aggregated equipment report by workshop"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='equipment_reports')
    report_date = models.DateField(default=timezone.now)
    total_equipment = models.PositiveIntegerField()
    critical_equipment = models.PositiveIntegerField()
    general_equipment = models.PositiveIntegerField()
    pending_delete = models.BooleanField(default=False)


    # Status breakdown
    working_count = models.PositiveIntegerField()
    under_repair_count = models.PositiveIntegerField()
    not_working_count = models.PositiveIntegerField()

    # Maintenance metrics
    last_month_repairs = models.PositiveIntegerField()
    last_month_calibrations = models.PositiveIntegerField()

        # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)


    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:
        unique_together = ('workshop', 'report_date')
        ordering = ['-report_date']

    @classmethod
    def generate_workshop_report(cls, workshop_id=None, date=None):
        """Generate workshop equipment report"""
        date = date or timezone.now().date()
        last_month = date - timezone.timedelta(days=30)

        if workshop_id:
            workshops = Workshop.objects.filter(id=workshop_id)
        else:
            workshops = Workshop.objects.all()

        reports = []
        for workshop in workshops:
            # Equipment counts
            equipment_qs = Equipment.objects.filter(workshop=workshop)
            total_equipment = equipment_qs.count()

            critical_equipment = equipment_qs.filter(category__is_critical=True).count()
            general_equipment = total_equipment - critical_equipment

            # Status counts
            status_counts = equipment_qs.values('status').annotate(count=Count('id'))
            status_map = {s['status']: s['count'] for s in status_counts}

            # Maintenance counts
            repairs = jobcard.objects.filter(
                equipment__workshop=workshop,
                date_issued__gte=last_month,
                action_taken='Repair',
                status='Approved'
            ).count()

            calibrations = CalibrationSession.objects.filter(
                workshop_name=workshop.name,
                timestamp__gte=last_month
            ).count()

            report = cls.objects.create(
                workshop=workshop,
                report_date=date,
                total_equipment=total_equipment,
                critical_equipment=critical_equipment,
                general_equipment=general_equipment,
                working_count=status_map.get('Working', 0),
                under_repair_count=status_map.get('Under repair', 0),
                not_working_count=status_map.get('Not working', 0),
                last_month_repairs=repairs,
                last_month_calibrations=calibrations
            )
            reports.append(report)

        return reports

    def __str__(self):
        return f"{self.workshop.name} Equipment Report - {self.report_date}"
