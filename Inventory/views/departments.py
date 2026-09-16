"""Department CRUD, transfer, and dependency-management views.

Split out of the original monolithic ``views.py`` with behaviour preserved.

NOTE on ``delete_department``: the original module defined ``delete_department``
*twice* (a simple version, then a dependency-checking version). In Python the
second definition silently shadows the first, so only the dependency-checking
version was ever reachable through ``urls.py``. That reachable version is kept
here as ``delete_department``. The original dead first version is preserved for
reference as ``_delete_department_simple_UNUSED`` so no logic is lost, but it is
intentionally not wired to any URL (matching the original behaviour).
"""
import logging

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, models
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from core.scoping import get_for_user_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from Inventory.models import Department, Equipment
from workshop.models import Workshop

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency auto-discovery helpers (plain functions, not views)
# ---------------------------------------------------------------------------

def discover_department_dependencies():
    """Automatically discover all models that have ForeignKey to Department"""
    department_models = []

    for app_config in apps.get_app_configs():
        for model in app_config.get_models():
            for field in model._meta.get_fields():
                if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                    if hasattr(field, 'related_model') and field.related_model == Department:
                        is_nullable = field.null if hasattr(field, 'null') else False

                        department_models.append({
                            'app_label': app_config.label,
                            'model_name': model.__name__,
                            'field_name': field.name,
                            'verbose_name': model._meta.verbose_name,
                            'model': model,
                            'is_nullable': is_nullable,
                            'on_delete': getattr(field.remote_field, 'on_delete', None).__name__ if hasattr(field.remote_field, 'on_delete') else 'UNKNOWN'
                        })
                        logger.debug(f"✅ Found FK: {app_config.label}.{model.__name__}.{field.name} (nullable={is_nullable})")

    logger.info(f"🔍 Total discovered models with Department FK/O2O: {len(department_models)}")
    return department_models


def get_department_dependencies_count(department):
    """Calculate total dependencies for a department using auto-discovery"""
    count = 0
    logger.info(f"🔍 Counting dependencies for department: {department.name} (ID: {department.id})")

    department_models = discover_department_dependencies()

    for model_info in department_models:
        try:
            model = model_info['model']
            field_name = model_info['field_name']

            # ✅ Count ALL records (including inactive) for transfer purposes
            field_count = model.objects.filter(**{field_name: department}).count()

            count += field_count

            if field_count > 0:
                logger.info(f"   📊 {model_info['app_label']}.{model_info['model_name']}.{field_name}: {field_count} records")

        except Exception as e:
            logger.warning(f"   ⚠️ Error checking {model_info['app_label']}.{model_info['model_name']}: {e}")
            continue

    logger.info(f"📈 Total dependencies: {count}")
    return count


def transfer_department_dependencies_auto(from_department, to_department):
    """Automatically discover and transfer all Department dependencies"""
    logger.info(f"🔄 AUTO TRANSFER START: {from_department.name} → {to_department.name if to_department else 'NULL'}")

    transferred_count = 0
    set_null_count = 0
    transfer_results = []
    updated_records = []

    department_models = discover_department_dependencies()
    logger.info(f"🔍 Discovered {len(department_models)} models with Department FK")

    for model_info in department_models:
        try:
            model = model_info['model']
            field_name = model_info['field_name']
            is_nullable = model_info['is_nullable']
            on_delete_behavior = model_info['on_delete']

            # ❌ REMOVE active_status filter for transfers - transfer ALL records
            before_count = model.objects.filter(**{field_name: from_department}).count()
            affected_records = list(model.objects.filter(**{field_name: from_department}))

            if before_count > 0:
                logger.info(f"📊 {model_info['model_name']}.{field_name}: {before_count} records found")

                if is_nullable and on_delete_behavior == 'SET_NULL' and not to_department:
                    logger.info(f"   🔄 Setting {field_name} to NULL for {before_count} records")
                    updated = model.objects.filter(**{field_name: from_department}).update(
                        **{field_name: None, 'updated_at': timezone.now()}
                    )
                    set_null_count += updated
                    action = 'SET_NULL'
                else:
                    if to_department:
                        logger.info(f"   🔄 Transferring {before_count} records to {to_department.name}")
                        # ✅ Transfer ALL records (active and inactive)
                        updated = model.objects.filter(**{field_name: from_department}).update(
                            **{field_name: to_department, 'updated_at': timezone.now()}
                        )
                        transferred_count += updated
                        action = 'TRANSFER'
                    else:
                        logger.error(f"   ❌ Cannot transfer non-nullable field without target department")
                        updated = 0
                        action = 'ERROR'

                if updated > 0:
                    for record in affected_records:
                        updated_records.append((model, record))

                after_count = model.objects.filter(**{field_name: from_department}).count()

                transfer_results.append({
                    'model': f"{model_info['app_label']}.{model_info['model_name']}",
                    'field': field_name,
                    'before': before_count,
                    'updated': updated,
                    'after': after_count,
                    'action': action,
                    'success': updated == before_count and after_count == 0
                })

                if updated == before_count and after_count == 0:
                    logger.info(f"   ✅ SUCCESS: {action} {updated} records")
                else:
                    logger.error(f"   ❌ FAILED: Expected {before_count}, got {updated}, remaining {after_count}")

        except Exception as e:
            logger.error(f"   💥 Error processing {model_info['model_name']}: {e}")
            transfer_results.append({
                'model': f"{model_info['app_label']}.{model_info['model_name']}",
                'field': model_info['field_name'],
                'before': 0,
                'updated': 0,
                'after': 0,
                'action': 'ERROR',
                'success': False,
                'error': str(e)
            })

    # Save all updated records to trigger sync
    logger.info(f"💾 Saving {len(updated_records)} updated records to trigger sync...")
    for model, record in updated_records:
        try:
            record.updated_at = timezone.now()
            record.save(update_fields=['updated_at'])
        except Exception as e:
            logger.warning(f"   ⚠️ Failed to save {model.__name__} record {record.pk}: {e}")

    logger.info(f"🎉 AUTO TRANSFER COMPLETE: {transferred_count} transferred, {set_null_count} set to NULL")
    return transferred_count + set_null_count, transfer_results


# ---------------------------------------------------------------------------
# Department CRUD views
# ---------------------------------------------------------------------------

@login_required
def create_department(request, workshop_id=None):
    profile = request.user.userprofile
    target_workshop = None

    if profile.role == 'HOD':
        if workshop_id:
            target_workshop = get_object_or_404(Workshop, id=workshop_id)
        else:
            messages.error(request, "HODs must select a workshop to create a department.")
            return redirect('dashboard:hod_dashboard')
    elif profile.role == 'Tech':
        target_workshop = profile.workshop
        if not target_workshop:
            messages.error(request, "Your profile is not associated with a workshop. Cannot create department.")
            return redirect('create_department')
    else:
        messages.error(request, "Unauthorized to create departments.")
        return redirect('create_department')

    if request.method == 'POST':
        name = request.POST.get('department_name')

        # Check if department already exists in the selected workshop
        if Department.objects.filter(name__iexact=name, workshop=target_workshop).exists():
            messages.error(request, f"Department '{name}' already exists in {target_workshop.name}.")
        else:
            try:
                # Create the department, assigning it to the determined workshop
                Department.objects.create(name=name, workshop=target_workshop)
                messages.success(
                    request,
                    f"Department '{name}' created successfully in {target_workshop.name}.",
                    extra_tags="success create task"
                )

                # Redirect based on user role
                if profile.role == 'HOD':
                    return redirect('create_department_for_hod', workshop_id=target_workshop.id)
                else:  # Tech
                    return redirect('create_department')
            except ValidationError as e:
                error_msg = e.message_dict.get('__all__', e.messages)[0] if hasattr(e, 'message_dict') else str(e)
                messages.error(request, error_msg)
            except Exception as e:
                messages.error(request, f"An unexpected error occurred: {e}")

    # ✅ MOVED OUTSIDE POST BLOCK - Get departments with equipment count for the template
    from django.db.models import Prefetch

    departments_with_count = Department.objects.filter(
        workshop=target_workshop
    ).prefetch_related(
        Prefetch(
            'equipment_set',
            queryset=Equipment.objects.filter(active_status=True),
            to_attr='active_equipment'
        )
    ).order_by('name')

    # For GET request or if there was an error
    return render(request, 'Inventory/create_department.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'selected_workshop': target_workshop,
        'departments_in_workshop': departments_with_count
    })


@login_required
def edit_department(request, dept_id):
    profile = request.user.userprofile
    department = get_object_or_404(Department, id=dept_id)
    target_workshop = department.workshop

    # Permission checks
    if profile.role == 'HOD':
        if not target_workshop:
            messages.error(request, "Department is not associated with a valid workshop.")
            return redirect('inventory')
    elif profile.role == 'Tech':
        if not profile.workshop or profile.workshop != target_workshop:
            messages.error(request, "Unauthorized to edit departments in this workshop.")
            return redirect('inventory')
    else:
        messages.error(request, "Unauthorized to edit departments.")
        return redirect('create_department')

    if request.method == 'POST':
        name = request.POST.get('department_name', '').strip()

        if not name:
            messages.error(request, "Department name is required.")
        elif Department.objects.filter(name__iexact=name, workshop=target_workshop).exclude(id=dept_id).exists():
            messages.error(request, f"Department '{name}' already exists in {target_workshop.name}.")
        else:
            try:
                old_name = department.name
                department.name = name
                department.full_clean()
                department.save()
                messages.success(
                    request,
                    f"Department '{old_name}' updated to '{name}' successfully.",
                    extra_tags="success update task"
                )

            except ValidationError as e:
                error_msg = e.message_dict.get('__all__', e.messages)[0] if hasattr(e, 'message_dict') else str(e)
                messages.error(request, error_msg)
            except Exception as e:
                messages.error(request, f"An unexpected error occurred: {e}")

    # Always redirect back to the appropriate page after edit attempt - FIXED THIS PART
    if profile.role == 'HOD':
        return redirect('create_department_for_hod', workshop_id=target_workshop.id)
    else:
        return redirect('create_department')


@login_required
@require_http_methods(["POST"])
def _delete_department_simple_UNUSED(request, dept_id):
    """Original first ``delete_department`` definition.

    Dead code in the original module: it was immediately shadowed by the second
    ``delete_department`` definition below and was never reachable via a URL.
    Preserved verbatim for reference only. Do NOT wire this to a URL.
    """
    profile = request.user.userprofile
    department = get_object_or_404(Department, id=dept_id)
    target_workshop = department.workshop

    # Permission checks
    if profile.role == 'HOD':
        if not target_workshop:
            messages.error(request, "Department is not associated with a valid workshop.")
            return redirect('create_department')
    elif profile.role == 'Tech':
        if not profile.workshop or profile.workshop != target_workshop:
            messages.error(request, "Unauthorized to delete departments in this workshop.")
            return redirect('create_department')
    else:
        messages.error(request, "Unauthorized to delete departments.")
        return redirect('create_department')

    try:
        department_name = department.name

        # Count associated equipment before marking for deletion
        equipment_count = Equipment.objects.filter(department=department, pending_delete=False, active_status=True).count()

        if equipment_count > 0:
            # Mark all equipment in the department as pending_delete
            equipment_to_delete = Equipment.objects.filter(department=department, pending_delete=False)
            equipment_names = [f"{eq.description.name} ({eq.serial_number})" for eq in equipment_to_delete[:5]]  # Show first 5

            # Update equipment to pending_delete
            equipment_to_delete.update(pending_delete=True, updated_at=timezone.now())

            if equipment_count <= 5:
                equipment_list = ", ".join(equipment_names)
                messages.success(
                    request,
                    f"Department '{department_name}' and {equipment_count} equipment items marked for deletion (will sync soon): {equipment_list}",
                    extra_tags="success delete task"
                )
            else:
                equipment_list = ", ".join(equipment_names)
                messages.success(
                    request,
                    f"Department '{department_name}' and {equipment_count} equipment items marked for deletion (will sync soon). First 5: {equipment_list}...",
                    extra_tags="success delete task"
                )
        else:
            messages.success(
                request,
                f"Department '{department_name}' marked for deletion (will sync soon).",
                extra_tags="success delete task"
            )

        # Instead of deleting, mark department as pending_delete
        department.pending_delete = True
        department.updated_at = timezone.now()
        department.save(update_fields=['pending_delete', 'updated_at'])

    except Exception as e:
        messages.error(request, f"An error occurred while marking the department for deletion: {e}")

    # Redirect based on user role
    if profile.role == 'HOD':
        return redirect('create_department_for_hod', workshop_id=target_workshop.id)
    return redirect('create_department')


@login_required
@require_http_methods(["POST"])
def transfer_department(request, dept_id):
    """Transfer a department to another workshop"""
    profile = request.user.userprofile
    department = get_object_or_404(Department, id=dept_id)
    current_workshop = department.workshop

    # Permission checks - only HODs can transfer departments
    if profile.role != 'HOD':
        messages.error(request, "Only Heads of Department can transfer departments between workshops.")
        return redirect('create_department')

    target_workshop_id = request.POST.get('target_workshop')

    if not target_workshop_id:
        messages.error(request, "Please select a target workshop.")
        return redirect('create_department_for_hod', workshop_id=current_workshop.id)

    try:
        target_workshop = get_object_or_404(Workshop, id=target_workshop_id)

        # Check if department with same name already exists in target workshop
        if Department.objects.filter(name__iexact=department.name, workshop=target_workshop).exists():
            messages.error(request,
                f"A department named '{department.name}' already exists in {target_workshop.name}. "
                f"Please rename the department before transferring or choose a different workshop.")
            return redirect('create_department_for_hod', workshop_id=current_workshop.id)

        # ✅ Count ALL equipment before transfer (including inactive)
        equipment_count = Equipment.objects.filter(department=department).count()

        # Perform the transfer
        old_workshop_name = current_workshop.name
        department.workshop = target_workshop
        department.full_clean()
        department.save()

        # Success message
        if equipment_count > 0:
            messages.success(
                request,
                f"Department '{department.name}' and {equipment_count} equipment items "
                f"successfully transferred from {old_workshop_name} to {target_workshop.name}.",
                extra_tags="success task"
            )
        else:
            messages.success(
                request,
                f"Department '{department.name}' successfully transferred from "
                f"{old_workshop_name} to {target_workshop.name}.",
                extra_tags="success task"
            )

    except ValidationError as e:
        error_msg = e.message_dict.get('__all__', e.messages)[0] if hasattr(e, 'message_dict') else str(e)
        messages.error(request, f"Transfer failed: {error_msg}")
    except Exception as e:
        messages.error(request, f"An unexpected error occurred during transfer: {e}")

    # Redirect back to the original workshop's department management page
    return redirect('create_department_for_hod', workshop_id=current_workshop.id)


@login_required
@require_http_methods(["GET"])
def get_department_dependency_count(request, dept_id):
    """API endpoint to get dependency count for a department"""
    try:
        department = get_for_user_or_404(Department, request.user, id=dept_id)
        count = get_department_dependencies_count(department)

        return JsonResponse({
            'count': count,
            'department_name': department.name,
            'status': 'success'
        })
    except Exception as e:
        logger.error(f"Error getting dependency count: {str(e)}")
        return JsonResponse({
            'count': 0,
            'status': 'error',
            'message': str(e)
        })


@login_required
@require_http_methods(["POST"])
def transfer_department_dependencies(request, dept_id):
    """Transfer all dependencies from one department to another"""
    profile = request.user.userprofile
    source_department = get_object_or_404(Department, id=dept_id)
    target_workshop = source_department.workshop

    # Permission checks
    if profile.role == 'HOD':
        if not target_workshop:
            return JsonResponse({
                'success': False,
                'error': 'Department is not associated with a valid workshop.'
            }, status=400)
    elif profile.role == 'Tech':
        if not profile.workshop or profile.workshop != target_workshop:
            return JsonResponse({
                'success': False,
                'error': 'Unauthorized to transfer dependencies in this workshop.'
            }, status=403)
    else:
        return JsonResponse({
            'success': False,
            'error': 'Unauthorized to transfer dependencies.'
        }, status=403)

    target_department_id = request.POST.get('target_department_id')

    logger.info(f"🔄 TRANSFER REQUEST for: {source_department.name} (ID: {source_department.id})")
    logger.info(f"🎯 Transfer to: {target_department_id or 'NULL (for nullable fields)'}")

    # Count dependencies
    dependency_count = get_department_dependencies_count(source_department)
    logger.info(f"🧮 Dependencies found: {dependency_count}")

    if dependency_count == 0:
        return JsonResponse({
            'success': True,
            'message': 'No dependencies to transfer',
            'transferred_count': 0
        })

    department_models = discover_department_dependencies()
    nullable_fields_exist = any(m['is_nullable'] and m['on_delete'] == 'SET_NULL' for m in department_models)

    if dependency_count > 0 and not target_department_id and not nullable_fields_exist:
        return JsonResponse({
            'success': False,
            'error': f'Department has {dependency_count} dependencies. Please select a department to transfer them to.'
        }, status=400)

    try:
        transfer_start_time = timezone.now()

        with transaction.atomic():
            if target_department_id:
                target_department = get_object_or_404(
                    Department,
                    id=target_department_id,
                    workshop=target_workshop
                )

                # Prevent transferring to the same department
                if source_department.id == target_department.id:
                    return JsonResponse({
                        'success': False,
                        'error': 'Cannot transfer to the same department.'
                    }, status=400)

                logger.info(f"🔄 STARTING AUTO-TRANSFER: {source_department.name} → {target_department.name}")
                transferred_count, transfer_results = transfer_department_dependencies_auto(
                    source_department,
                    target_department
                )
            else:
                target_department = None
                logger.info(f"🔄 STARTING AUTO-CLEANUP: {source_department.name} → NULL")
                transferred_count = 0
                transfer_results = []

                for model_info in department_models:
                    if model_info['is_nullable'] and model_info['on_delete'] == 'SET_NULL':
                        model = model_info['model']
                        field_name = model_info['field_name']
                        before_count = model.objects.filter(**{field_name: source_department}).count()

                        if before_count > 0:
                            affected_records = list(model.objects.filter(**{field_name: source_department}))
                            updated = model.objects.filter(**{field_name: source_department}).update(
                                **{field_name: None, 'updated_at': timezone.now()}
                            )
                            transferred_count += updated

                            for record in affected_records:
                                record.updated_at = timezone.now()
                                record.save(update_fields=['updated_at'])

        # Verify transfer
        remaining_deps = get_department_dependencies_count(source_department)

        if remaining_deps > 0:
            return JsonResponse({
                'success': False,
                'error': f'Transfer incomplete! {remaining_deps} dependencies still exist.'
            }, status=400)

        logger.info(f"✅ Transfer successful! {transferred_count} records transferred")

        return JsonResponse({
            'success': True,
            'message': f'Successfully transferred {transferred_count} dependencies',
            'transferred_count': transferred_count,
            'transfer_time': transfer_start_time.isoformat()
        })

    except Department.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Target department not found.'
        }, status=404)
    except Exception as e:
        logger.error(f"❌ Transfer failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Transfer failed: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST"])
def delete_department(request, dept_id):
    """Delete department - expects dependencies already transferred and synced.

    This is the effective ``delete_department`` (the second definition in the
    original module, which shadowed the first).
    """
    profile = request.user.userprofile
    department = get_object_or_404(Department, id=dept_id)
    target_workshop = department.workshop

    # Permission checks
    if profile.role == 'HOD':
        if not target_workshop:
            messages.error(request, "Department is not associated with a valid workshop.")
            return redirect('create_department')
    elif profile.role == 'Tech':
        if not profile.workshop or profile.workshop != target_workshop:
            messages.error(request, "Unauthorized to delete departments in this workshop.")
            return redirect('create_department')
    else:
        messages.error(request, "Unauthorized to delete departments.")
        return redirect('create_department')

    logger.info(f"🗑️ DELETE REQUEST for: {department.name} (ID: {department.id})")

    # Verify no dependencies remain locally
    dependency_count = get_department_dependencies_count(department)

    if dependency_count > 0:
        error_msg = f'Department "{department.name}" still has {dependency_count} dependencies. Please transfer them first.'
        messages.error(request, error_msg)
        logger.error(f"❌ {error_msg}")

        if profile.role == 'HOD':
            return redirect('create_department_for_hod', workshop_id=target_workshop.id)
        return redirect('create_department')

    try:
        deletion_timestamp = timezone.now()

        with transaction.atomic():
            department_name = department.name

            # Final safety check
            final_check = get_department_dependencies_count(department)
            if final_check > 0:
                error_msg = f'Safety check failed! {final_check} dependencies still exist.'
                messages.error(request, error_msg)
                logger.error(f"❌ {error_msg}")

                if profile.role == 'HOD':
                    return redirect('create_department_for_hod', workshop_id=target_workshop.id)
                return redirect('create_department')

            # Mark for deletion
            department.refresh_from_db()
            department.pending_delete = True
            department.updated_at = deletion_timestamp
            department.save(update_fields=['pending_delete', 'updated_at'])

            logger.info(f"=" * 60)
            logger.info(f"🎉 SUCCESS: Department marked for deletion!")
            logger.info(f"=" * 60)
            logger.info(f"   📛 Department: {department_name}")
            logger.info(f"   🗑️ Deletion time: {deletion_timestamp}")
            logger.info(f"   ⏱️ Frontend ensured 45s+ gap from transfers")
            logger.info(f"=" * 60)

            messages.success(
                request,
                f'Department "{department_name}" marked for deletion successfully!',
                extra_tags="success delete task"
            )

    except Exception as e:
        error_msg = f'Error marking department for deletion: {str(e)}'
        logger.error(f"❌ {error_msg}")
        import traceback
        logger.error(traceback.format_exc())
        messages.error(request, error_msg)

    # Redirect based on user role
    if profile.role == 'HOD':
        return redirect('create_department_for_hod', workshop_id=target_workshop.id)
    return redirect('create_department')
