"""Equipment transfer and reactivation views.

Also holds the two equipment-scoped API endpoints (dependency info and PPM
location check) so all equipment-dependency usage lives in one module.
Behaviour is unchanged from the original ``views.py``.
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from Inventory.models import Department, Equipment, EquipmentDescription, Manufacturer
from audit_log.models import AuditLog
from Inventory.equipment_dependencies import (
    get_equipment_dependencies_count,
    transfer_equipment_dependencies,
    validate_equipment_transfer,
    verify_dependency_transfer,
    check_ppm_location,
)

logger = logging.getLogger(__name__)


@login_required
@require_http_methods(["POST"])
def transfer_equipment(request, equipment_id):
    """
    Transfer equipment between departments (same or different workshops)
    ✅ NOW INCLUDES: Automatic dependency transfer + verification
    """
    try:
        equipment = get_object_or_404(Equipment, pk=equipment_id, active_status=True)
        profile = request.user.userprofile

        # Permission check - only Tech can transfer
        if profile.role != 'Tech':
            return JsonResponse({
                'success': False,
                'error': 'Only Technologists can transfer equipment.'
            }, status=403)

        # Get target department
        target_department_id = request.POST.get('target_department_id')
        if not target_department_id:
            return JsonResponse({
                'success': False,
                'error': 'Target department is required.'
            }, status=400)

        try:
            target_department = Department.objects.get(
                id=target_department_id,
                active_status=True
            )
        except Department.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Target department not found or inactive.'
            }, status=404)

        # Validate target department has an active workshop
        if not target_department.workshop or not target_department.workshop.active_status:
            return JsonResponse({
                'success': False,
                'error': 'Target department does not belong to an active workshop.'
            }, status=400)

        target_workshop = target_department.workshop

        # Get new status (optional)
        new_status = request.POST.get('status', equipment.status)
        if new_status not in ['Working', 'Not working', 'Under repair']:
            new_status = equipment.status

        # Store old values for audit
        old_department = equipment.department
        old_workshop = old_department.workshop if old_department else None
        old_status = equipment.status

        # Check if transferring to same department
        if old_department and old_department.id == target_department.id:
            return JsonResponse({
                'success': False,
                'error': 'Equipment is already in this department.'
            }, status=400)

        # Determine transfer type
        is_cross_workshop = old_workshop and old_workshop.id != target_workshop.id

        logger.info("=" * 80)
        logger.info(f"🔄 EQUIPMENT TRANSFER initiated by {request.user.username}")
        logger.info(f"Equipment: {equipment.serial_number} ({equipment.id})")
        logger.info(f"FROM: {old_workshop.name if old_workshop else 'Unknown'} / {old_department.name if old_department else 'Unknown'}")
        logger.info(f"TO: {target_workshop.name} / {target_department.name}")
        logger.info(f"Cross-workshop: {is_cross_workshop}")

        # ✅ CHECK FOR DEPENDENCIES
        dep_count, dep_breakdown = get_equipment_dependencies_count(equipment)

        if dep_count > 0:
            logger.info(f"📦 Found {dep_count} dependencies to transfer")

            # Validate transfer
            can_transfer, error_msg, dep_info = validate_equipment_transfer(
                equipment, target_department, target_workshop
            )

            if not can_transfer:
                logger.error(f"❌ Transfer validation failed: {error_msg}")
                return JsonResponse({
                    'success': False,
                    'error': error_msg,
                    'dependency_info': dep_info
                }, status=400)

        logger.info("=" * 80)

        # Perform transfer with dependencies
        try:
            with transaction.atomic():
                # ✅ STEP 1: Transfer dependencies FIRST
                dependencies_transferred = 0
                transfer_results = []

                if dep_count > 0:
                    logger.info(f"🔄 STEP 1: Transferring {dep_count} dependencies...")
                    dependencies_transferred, transfer_results = transfer_equipment_dependencies(
                        equipment, target_department, target_workshop
                    )
                    logger.info(f"✅ Dependencies transferred: {dependencies_transferred}")

                # ✅ STEP 2: Transfer the equipment itself
                logger.info(f"🔄 STEP 2: Transferring equipment...")
                equipment.department = target_department
                equipment.workshop = target_workshop  # ✅ FIX: update workshop so PPM init can find this equipment
                equipment.status = new_status
                equipment.updated_at = timezone.now()
                equipment.save(update_fields=['department', 'workshop', 'status', 'updated_at'])

                logger.info(f"✅ Equipment transferred successfully")

                # ✅ STEP 3: Create audit log (FIXED)
                try:
                    now = timezone.now()

                    audit_entry = AuditLog.objects.create(
                        table_name='public.Inventory_equipment',
                        row_id=equipment.id,
                        operation=AuditLog.OPERATION_TRANSFER,
                        source='django-backend',
                        user_id=request.user.id if request.user.is_authenticated else None,
                        received_at=now,
                        created_at=now,
                        metadata={
                            'old_department': old_department.name if old_department else None,
                            'new_department': target_department.name,
                            'old_workshop': old_workshop.name if old_workshop else None,
                            'new_workshop': target_workshop.name,
                            'is_cross_workshop': is_cross_workshop,
                            'dependencies_transferred': dependencies_transferred,
                            'old_status': old_status,
                            'new_status': new_status
                        }
                    )
                    logger.info(f"✅ Audit log created (event_id: {audit_entry.event_id})")

                except Exception as audit_error:
                    logger.error(f"⚠️ Audit log failed: {audit_error}")
                    # Don't raise - we don't want audit failure to block transfer

        except Exception as e:
            logger.error(f"❌ Transfer transaction failed: {str(e)}")
            logger.exception(e)
            raise

        # ✅ STEP 4: VERIFY all dependencies transferred correctly
        logger.info(f"🔍 STEP 4: Verifying dependency transfer...")

        all_correct, issues = verify_dependency_transfer(
            equipment, target_workshop, target_department
        )

        if not all_correct:
            logger.error(f"❌ Verification found {len(issues)} issues!")
            for issue in issues:
                logger.error(f"   • {issue['model']} {issue['record_id']}: {issue['field']} = {issue['actual']} (expected {issue['expected']})")

        logger.info("=" * 80)
        logger.info(f"🎉 TRANSFER COMPLETE")
        logger.info(f"   Equipment transferred: {equipment.serial_number}")
        logger.info(f"   Dependencies transferred: {dependencies_transferred}")
        logger.info(f"   Verification: {'✅ PASSED' if all_correct else f'❌ FAILED ({len(issues)} issues)'}")
        logger.info("=" * 80)

        # Success message
        if is_cross_workshop:
            message = f"✅ Equipment and {dependencies_transferred} dependencies transferred from {old_workshop.name} to {target_workshop.name} ({target_department.name})"
        else:
            message = f"✅ Equipment and {dependencies_transferred} dependencies moved to {target_department.name}"

        return JsonResponse({
            'success': True,
            'message': message,
            'equipment': {
                'id': str(equipment.id),
                'serial_number': equipment.serial_number,
                'old_department': old_department.name if old_department else 'Unknown',
                'old_workshop': old_workshop.name if old_workshop else 'Unknown',
                'new_department': target_department.name,
                'new_workshop': target_workshop.name,
                'status': equipment.status,
                'is_cross_workshop': is_cross_workshop,
                'dependencies_transferred': dependencies_transferred,
                'transfer_results': transfer_results,
                'verification_passed': all_correct,
                'verification_issues': issues if not all_correct else []
            }
        })

    except Exception as e:
        logger.error(f"❌ Transfer failed: {str(e)}")
        logger.exception(e)
        return JsonResponse({
            'success': False,
            'error': f'Transfer failed: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST"])
def reactivate_equipment(request, equipment_id):
    """
    Enhanced reactivation with cross-workshop transfer support
    ✅ NOW INCLUDES: Automatic dependency transfer + verification
    """
    try:
        equipment = Equipment.objects.select_related(
            'department',
            'department__workshop',
            'description',
            'manufacturer'
        ).get(pk=equipment_id)

    except Equipment.DoesNotExist:
        logger.error(f"❌ Equipment not found: {equipment_id}")
        return JsonResponse({
            'success': False,
            'error': 'Equipment not found.'
        }, status=404)

    try:
        profile = request.user.userprofile
    except AttributeError:
        logger.error(f"❌ User profile not found for user: {request.user.username}")
        return JsonResponse({
            'success': False,
            'error': 'User profile not found.'
        }, status=403)

    # Permission checks
    if profile.role != 'Tech':
        logger.warning(f"❌ Unauthorized reactivation attempt by {request.user.username} (role: {profile.role})")
        return JsonResponse({
            'success': False,
            'error': 'Only Technologists can reactivate equipment.'
        }, status=403)

    if not profile.workshop:
        logger.error(f"❌ No workshop assigned to user: {request.user.username}")
        return JsonResponse({
            'success': False,
            'error': 'Your profile is not associated with a workshop.'
        }, status=403)

    # Must specify target department
    new_department_id = request.POST.get('department_id')
    if not new_department_id:
        return JsonResponse({
            'success': False,
            'error': 'Department is required.'
        }, status=400)

    # Validate target department
    try:
        new_department = Department.objects.select_related('workshop').get(
            id=new_department_id,
            active_status=True
        )
    except Department.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Invalid or inactive department selected.'
        }, status=400)

    # Validate target department's workshop
    if not new_department.workshop or not new_department.workshop.active_status:
        return JsonResponse({
            'success': False,
            'error': 'Target department does not belong to an active workshop.'
        }, status=400)

    target_workshop = new_department.workshop

    # Tech can only reactivate TO their workshop
    if target_workshop.id != profile.workshop.id:
        return JsonResponse({
            'success': False,
            'error': f'You can only reactivate equipment to your workshop ({profile.workshop.name}).'
        }, status=403)

    # Determine new status
    new_status = request.POST.get('status', 'Working')
    if new_status not in ['Working', 'Not working', 'Under repair']:
        new_status = 'Working'

    # Store old data for audit
    old_department = equipment.department
    old_workshop = old_department.workshop if old_department else None
    old_active = equipment.active_status
    old_pending = equipment.pending_delete
    old_status = equipment.status

    # Determine operation type
    is_cross_workshop_transfer = old_workshop and old_workshop.id != target_workshop.id
    is_cross_department_transfer = old_department and old_department.id != new_department.id
    is_transfer = is_cross_workshop_transfer or is_cross_department_transfer

    logger.info("=" * 80)
    logger.info(f"🔄 REACTIVATION ATTEMPT by {request.user.username}")
    logger.info(f"Equipment: {equipment.serial_number} (ID: {equipment.id})")
    logger.info(f"Description: {equipment.description.name if equipment.description else 'N/A'}")
    logger.info(f"FROM: {old_workshop.name if old_workshop else 'Unknown'} / {old_department.name if old_department else 'Unknown'}")
    logger.info(f"TO: {target_workshop.name} / {new_department.name}")
    logger.info(f"Cross-workshop transfer: {is_cross_workshop_transfer}")
    logger.info(f"BEFORE: active={equipment.active_status}, pending_delete={equipment.pending_delete}, status={old_status}")

    # ✅ CHECK FOR DEPENDENCIES
    dep_count, dep_breakdown = get_equipment_dependencies_count(equipment)

    if dep_count > 0:
        logger.info(f"📦 Found {dep_count} dependencies to transfer")

        # Validate transfer
        can_transfer, error_msg, dep_info = validate_equipment_transfer(
            equipment, new_department, target_workshop
        )

        if not can_transfer:
            logger.error(f"❌ Reactivation validation failed: {error_msg}")
            return JsonResponse({
                'success': False,
                'error': error_msg,
                'dependency_info': dep_info
            }, status=400)

    logger.info("=" * 80)

    # Perform reactivation/transfer with dependencies
    try:
        with transaction.atomic():
            # ✅ STEP 1: Transfer dependencies FIRST (if any)
            dependencies_transferred = 0
            transfer_results = []

            if dep_count > 0:
                logger.info(f"🔄 STEP 1: Transferring {dep_count} dependencies...")
                dependencies_transferred, transfer_results = transfer_equipment_dependencies(
                    equipment, new_department, target_workshop
                )
                logger.info(f"✅ Dependencies transferred: {dependencies_transferred}")

            # ✅ STEP 2: Reactivate and transfer the equipment
            logger.info(f"🔄 STEP 2: Reactivating equipment...")
            equipment.department = new_department
            equipment.workshop = target_workshop  # ✅ FIX: update workshop so PPM init can find this equipment
            equipment.status = new_status
            equipment.active_status = True
            equipment.pending_delete = False
            equipment.updated_at = timezone.now()

            equipment.save(update_fields=[
                'department', 'workshop', 'status', 'active_status', 'pending_delete', 'updated_at'
            ])

            # Verify save
            equipment.refresh_from_db()

            if equipment.pending_delete or not equipment.active_status:
                raise Exception("Failed to update equipment status correctly")

            logger.info(f"✅ Equipment reactivated successfully")

            # ✅ STEP 3: Create audit log using Django ORM
            try:
                audit_entry = AuditLog.objects.create(
                    table_name='public.Inventory_equipment',
                    row_id=equipment.id,
                    operation=AuditLog.OPERATION_RESTORE,  # 'r' for reactivate
                    source='django-backend',
                    user_id=request.user.id if request.user.is_authenticated else None,
                    changed_fields=[
                        'department', 'status', 'active_status',
                        'pending_delete', 'updated_at'
                    ],
                    old_values={
                        'department_id': str(old_department.id) if old_department else None,
                        'department_name': old_department.name if old_department else None,
                        'workshop_id': str(old_workshop.id) if old_workshop else None,
                        'workshop_name': old_workshop.name if old_workshop else None,
                        'status': old_status,
                        'active_status': old_active,
                        'pending_delete': old_pending,
                    },
                    new_values={
                        'department_id': str(new_department.id),
                        'department_name': new_department.name,
                        'workshop_id': str(target_workshop.id),
                        'workshop_name': target_workshop.name,
                        'status': new_status,
                        'active_status': True,
                        'pending_delete': False,
                    },
                    metadata={
                        'operation_type': 'transfer' if is_transfer else 'reactivate',
                        'is_cross_workshop': is_cross_workshop_transfer,
                        'is_cross_department': is_cross_department_transfer,
                        'dependencies_transferred': dependencies_transferred,
                        'technologist': request.user.username,
                        'technologist_workshop': profile.workshop.name,
                    }
                )
                logger.info(f"✅ Audit log created (event_id: {audit_entry.event_id})")

            except Exception as audit_error:
                logger.error(f"⚠️ Audit log failed: {audit_error}")
                logger.exception(audit_error)
                # Don't raise - we don't want audit failure to block reactivation

        # ✅ STEP 4: VERIFY all dependencies transferred correctly
        logger.info(f"🔍 STEP 4: Verifying dependency transfer...")

        all_correct, issues = verify_dependency_transfer(
            equipment, target_workshop, new_department
        )

        if not all_correct:
            logger.error(f"❌ Verification found {len(issues)} issues!")
            for issue in issues:
                logger.error(f"   • {issue['model']} {issue['record_id']}: {issue['field']} = {issue['actual']} (expected {issue['expected']})")

        logger.info("=" * 80)
        logger.info(f"🎉 REACTIVATION COMPLETE")
        logger.info(f"   Equipment: {equipment.serial_number}")
        logger.info(f"   AFTER: active={equipment.active_status}, pending_delete={equipment.pending_delete}, status={equipment.status}")
        logger.info(f"   Department: {equipment.department.name}")
        logger.info(f"   Workshop: {equipment.department.workshop.name}")
        logger.info(f"   Dependencies transferred: {dependencies_transferred}")
        logger.info(f"   Verification: {'✅ PASSED' if all_correct else f'❌ FAILED ({len(issues)} issues)'}")
        logger.info("=" * 80)

        # Success message
        if is_cross_workshop_transfer:
            message = f"✅ Equipment and {dependencies_transferred} dependencies TRANSFERRED from {old_workshop.name} to {target_workshop.name} and reactivated in {new_department.name}"
        elif is_cross_department_transfer:
            message = f"✅ Equipment and {dependencies_transferred} dependencies moved from {old_department.name} to {new_department.name} and reactivated"
        else:
            message = f"✅ Equipment and {dependencies_transferred} dependencies reactivated in {new_department.name}"

        return JsonResponse({
            'success': True,
            'message': message,
            'operation_type': 'transfer' if is_transfer else 'reactivate',
            'equipment': {
                'id': str(equipment.id),
                'serial_number': equipment.serial_number,
                'description': equipment.description.name if equipment.description else 'N/A',
                'old_department': old_department.name if old_department else 'Unknown',
                'old_workshop': old_workshop.name if old_workshop else 'Unknown',
                'new_department': new_department.name,
                'new_workshop': target_workshop.name,
                'status': equipment.status,
                'active_status': equipment.active_status,
                'pending_delete': equipment.pending_delete,
                'is_cross_workshop': is_cross_workshop_transfer,
                'is_transfer': is_transfer,
                'dependencies_transferred': dependencies_transferred,
                'transfer_results': transfer_results,
                'verification_passed': all_correct,
                'verification_issues': issues if not all_correct else []
            }
        })

    except Exception as e:
        logger.error(f"❌ Reactivation failed: {str(e)}")
        logger.exception(e)
        return JsonResponse({
            'success': False,
            'error': f'Failed to reactivate: {str(e)}'
        }, status=500)
