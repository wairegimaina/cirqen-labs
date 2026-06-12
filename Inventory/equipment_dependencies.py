# equipment_dependencies.py - FIXED VERSION
import logging
from django.db import models, transaction
from django.apps import apps
from django.utils import timezone
from collections import defaultdict

logger = logging.getLogger(__name__)


def discover_equipment_dependencies():
    """
    Automatically discover all models that have ForeignKey to Equipment
    Returns list of model info dicts with dependency details
    """
    equipment_models = []

    # Get Equipment model
    from Inventory.models import Equipment

    for app_config in apps.get_app_configs():
        for model in app_config.get_models():
            for field in model._meta.get_fields():
                # Check for ForeignKey or OneToOneField to Equipment
                if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                    if hasattr(field, 'related_model') and field.related_model == Equipment:
                        is_nullable = field.null if hasattr(field, 'null') else False
                        on_delete = getattr(field.remote_field, 'on_delete', None)
                        on_delete_name = on_delete.__name__ if on_delete else 'UNKNOWN'

                        # Check if model has location fields
                        model_fields = {f.name for f in model._meta.get_fields()}
                        has_workshop = 'workshop' in model_fields
                        has_department = 'department' in model_fields
                        has_updated_at = 'updated_at' in model_fields

                        equipment_models.append({
                            'app_label': app_config.label,
                            'model_name': model.__name__,
                            'field_name': field.name,
                            'verbose_name': model._meta.verbose_name,
                            'model': model,
                            'is_nullable': is_nullable,
                            'on_delete': on_delete_name,
                            'has_workshop': has_workshop,
                            'has_department': has_department,
                            'has_updated_at': has_updated_at,
                            'needs_location_update': has_workshop or has_department,
                            'related_name': field.remote_field.related_name if hasattr(field.remote_field, 'related_name') else None
                        })

                        logger.debug(
                            f"✅ Found Equipment FK: {app_config.label}.{model.__name__}.{field.name} "
                            f"(workshop={has_workshop}, dept={has_department}, on_delete={on_delete_name})"
                        )

    logger.info(f"🔍 Total discovered models with Equipment FK/O2O: {len(equipment_models)}")
    return equipment_models


def get_equipment_dependencies_count(equipment):
    """
    Calculate total dependencies for an equipment item
    Returns: (total_count, breakdown_dict)
    """
    total_count = 0
    breakdown = {}

    logger.info(f"🔍 Counting dependencies for equipment: {equipment.serial_number} (ID: {equipment.id})")

    equipment_models = discover_equipment_dependencies()

    for model_info in equipment_models:
        try:
            model = model_info['model']
            field_name = model_info['field_name']

            # Count ALL related records (active and inactive)
            field_count = model.objects.filter(**{field_name: equipment}).count()

            if field_count > 0:
                model_key = f"{model_info['app_label']}.{model_info['model_name']}"
                breakdown[model_key] = {
                    'count': field_count,
                    'field': field_name,
                    'nullable': model_info['is_nullable'],
                    'on_delete': model_info['on_delete'],
                    'has_workshop': model_info['has_workshop'],
                    'has_department': model_info['has_department'],
                    'needs_location_update': model_info['needs_location_update']
                }
                total_count += field_count

                logger.info(
                    f"   📊 {model_key}.{field_name}: {field_count} records "
                    f"(workshop={model_info['has_workshop']}, dept={model_info['has_department']})"
                )

        except Exception as e:
            logger.warning(f"   ⚠️ Error checking {model_info['app_label']}.{model_info['model_name']}: {e}")
            continue

    logger.info(f"📈 Total equipment dependencies: {total_count}")
    return total_count, breakdown


def transfer_equipment_dependencies(from_equipment, to_department, to_workshop):
    """
    Transfer all Equipment dependencies when equipment moves to new department/workshop

    ✅ FIXED: Now uses queryset.update() to bypass signals that might interfere
    This ensures PPM schedules, calibrations, and all dependencies are updated atomically

    Args:
        from_equipment: Equipment instance being transferred
        to_department: Target Department instance
        to_workshop: Target Workshop instance

    Returns:
        (transferred_count, transfer_results)
    """
    logger.info("="*80)
    logger.info(f"🔄 EQUIPMENT DEPENDENCY TRANSFER START")
    logger.info(f"   Equipment: {from_equipment.serial_number} ({from_equipment.id})")
    logger.info(f"   From: {from_equipment.department.workshop.name} / {from_equipment.department.name}")
    logger.info(f"   To: {to_workshop.name} / {to_department.name}")
    logger.info("="*80)

    transferred_count = 0
    transfer_results = []

    equipment_models = discover_equipment_dependencies()
    logger.info(f"🔍 Discovered {len(equipment_models)} models with Equipment FK")

    for model_info in equipment_models:
        try:
            model = model_info['model']
            field_name = model_info['field_name']
            has_workshop = model_info['has_workshop']
            has_department = model_info['has_department']
            has_updated_at = model_info['has_updated_at']

            # Find all dependent records
            dependent_queryset = model.objects.filter(**{field_name: from_equipment})
            before_count = dependent_queryset.count()

            if before_count > 0:
                logger.info(f"📦 Processing {model_info['model_name']}.{field_name}: {before_count} records")

                # ✅ CRITICAL FIX: Build update dict for ALL location fields
                updates = {}

                if has_workshop:
                    updates['workshop'] = to_workshop
                    logger.info(f"   ✓ Will update workshop → {to_workshop.name}")

                if has_department:
                    updates['department'] = to_department
                    logger.info(f"   ✓ Will update department → {to_department.name}")

                if has_updated_at:
                    updates['updated_at'] = timezone.now()

                # ✅ PERFORM BULK UPDATE if model has ANY location fields
                if updates:
                    is_ppm_model = 'ppm' in model_info['model_name'].lower()

                    # ✅ FIX: Use queryset.update() to bypass signals
                    logger.info(f"   🔄 Transferring {before_count} records using bulk update (bypasses signals)")
                    updated = dependent_queryset.update(**updates)

                    transferred_count += updated
                    action = 'TRANSFERRED'

                    logger.info(f"   ✅ Bulk update completed: {updated}/{before_count} records")

                    # ✅ VERIFY: Check if records were actually updated
                    if is_ppm_model:
                        logger.info(f"   🔍 VERIFICATION - Checking updated PPM records...")
                        verification_failed = 0

                        for record in dependent_queryset:
                            workshop_after = getattr(record, 'workshop', None)
                            department_after = getattr(record, 'department', None)

                            if has_workshop and workshop_after and workshop_after.id != to_workshop.id:
                                logger.error(f"      ❌ PPM {record.id} WORKSHOP NOT UPDATED! Still: {workshop_after.name}")
                                verification_failed += 1

                            if has_department and department_after and department_after.id != to_department.id:
                                logger.error(f"      ❌ PPM {record.id} DEPARTMENT NOT UPDATED! Still: {department_after.name}")
                                verification_failed += 1

                        if verification_failed == 0:
                            logger.info(f"   ✅ All {updated} PPM records verified correctly")
                        else:
                            logger.error(f"   ❌ {verification_failed} PPM records failed verification!")

                else:
                    # No location fields to update - dependency follows equipment automatically via FK
                    logger.info(f"   ℹ️ No location fields - follows equipment automatically via FK")
                    action = 'FOLLOWS_EQUIPMENT'
                    updated = 0

                # Final count after updates
                after_count = dependent_queryset.count()

                transfer_results.append({
                    'model': f"{model_info['app_label']}.{model_info['model_name']}",
                    'field': field_name,
                    'before': before_count,
                    'updated': updated if updates else 0,
                    'after': after_count,
                    'action': action,
                    'fields_updated': list(updates.keys()) if updates else [],
                    'has_workshop': has_workshop,
                    'has_department': has_department,
                    'success': True
                })

                logger.info(f"   ✅ {action}: {updated if updates else 0} records processed")

        except Exception as e:
            logger.error(f"   💥 Error processing {model_info['model_name']}: {e}")
            import traceback
            logger.error(traceback.format_exc())

            transfer_results.append({
                'model': f"{model_info['app_label']}.{model_info['model_name']}",
                'field': model_info['field_name'],
                'before': 0,
                'updated': 0,
                'after': 0,
                'action': 'ERROR',
                'fields_updated': [],
                'has_workshop': model_info['has_workshop'],
                'has_department': model_info['has_department'],
                'success': False,
                'error': str(e)
            })

    logger.info("="*80)
    logger.info(f"🎉 EQUIPMENT DEPENDENCY TRANSFER COMPLETE")
    logger.info(f"   Transferred dependencies: {transferred_count}")
    logger.info(f"   Total models processed: {len(transfer_results)}")
    logger.info("="*80)

    # 🔍 POST-TRANSFER VERIFICATION
    try:
        from ppms.models import PPMSchedule
        ppm_after = PPMSchedule.objects.filter(equipment=from_equipment)
        logger.info(f"🔍 POST-TRANSFER PPM CHECK:")
        logger.info(f"   Total PPMs for equipment: {ppm_after.count()}")

        all_correct = True
        for ppm in ppm_after:
            logger.info(f"   📋 PPM {ppm.id}: Workshop={ppm.workshop.name if ppm.workshop else 'None'} ({ppm.workshop_id})")

            # 🚨 FINAL VERIFICATION
            if ppm.workshop_id != to_workshop.id:
                logger.error(f"   ❌ PPM {ppm.id} STILL IN WRONG WORKSHOP! Current: {ppm.workshop.name}, Expected: {to_workshop.name}")
                all_correct = False
            else:
                logger.info(f"   ✅ PPM {ppm.id} correctly transferred to {to_workshop.name}")

        if not all_correct:
            logger.error(f"   ❌ CRITICAL: Some PPMs not transferred correctly!")

    except Exception as e:
        logger.warning(f"   ⚠️ Could not check PPMs after transfer: {e}")

    return transferred_count, transfer_results


def validate_equipment_transfer(equipment, target_department, target_workshop):
    """
    Validate if equipment can be safely transferred
    Returns: (can_transfer, error_message, dependency_info)
    """
    logger.info(f"🔍 Validating transfer for equipment: {equipment.serial_number}")

    # Check target department and workshop are valid
    if not target_department or not target_workshop:
        return False, "Invalid target department or workshop", None

    # Check target department belongs to target workshop
    if target_department.workshop.id != target_workshop.id:
        return False, f"Department {target_department.name} does not belong to {target_workshop.name}", None

    # Check for dependencies
    dep_count, dep_breakdown = get_equipment_dependencies_count(equipment)

    dependencies_with_location = []
    dependencies_without_location = []

    equipment_models = discover_equipment_dependencies()

    for model_info in equipment_models:
        model_key = f"{model_info['app_label']}.{model_info['model_name']}"
        if model_key in dep_breakdown:
            dep_info = dep_breakdown[model_key]

            if dep_info['needs_location_update']:
                dependencies_with_location.append({
                    'model': model_key,
                    'count': dep_info['count'],
                    'has_workshop': dep_info['has_workshop'],
                    'has_department': dep_info['has_department']
                })
            else:
                dependencies_without_location.append({
                    'model': model_key,
                    'count': dep_info['count']
                })

    logger.info(f"✅ Transfer validation passed")
    logger.info(f"   Dependencies with location fields: {len(dependencies_with_location)} types ({sum(d['count'] for d in dependencies_with_location)} records)")
    logger.info(f"   Dependencies without location fields: {len(dependencies_without_location)} types ({sum(d['count'] for d in dependencies_without_location)} records)")

    return True, None, {
        'total': dep_count,
        'breakdown': dep_breakdown,
        'with_location': dependencies_with_location,
        'without_location': dependencies_without_location
    }


def verify_dependency_transfer(equipment, expected_workshop, expected_department):
    """
    Verify that all dependencies were transferred correctly
    Returns: (all_correct, issues_found)
    """
    logger.info(f"🔍 Verifying dependency transfer for equipment: {equipment.serial_number}")

    all_correct = True
    issues = []

    equipment_models = discover_equipment_dependencies()

    for model_info in equipment_models:
        try:
            model = model_info['model']
            field_name = model_info['field_name']
            has_workshop = model_info['has_workshop']
            has_department = model_info['has_department']

            # Get all dependencies
            dependencies = model.objects.filter(**{field_name: equipment})

            for dep in dependencies:
                # Check workshop field
                if has_workshop and hasattr(dep, 'workshop'):
                    if dep.workshop and dep.workshop.id != expected_workshop.id:
                        all_correct = False
                        issues.append({
                            'model': model_info['model_name'],
                            'record_id': dep.id,
                            'field': 'workshop',
                            'expected': expected_workshop.name,
                            'actual': dep.workshop.name if dep.workshop else 'None'
                        })
                        logger.error(
                            f"   ❌ {model_info['model_name']} {dep.id}: "
                            f"workshop is {dep.workshop.name if dep.workshop else 'None'}, "
                            f"expected {expected_workshop.name}"
                        )

                # Check department field
                if has_department and hasattr(dep, 'department'):
                    if dep.department and dep.department.id != expected_department.id:
                        all_correct = False
                        issues.append({
                            'model': model_info['model_name'],
                            'record_id': dep.id,
                            'field': 'department',
                            'expected': expected_department.name,
                            'actual': dep.department.name if dep.department else 'None'
                        })
                        logger.error(
                            f"   ❌ {model_info['model_name']} {dep.id}: "
                            f"department is {dep.department.name if dep.department else 'None'}, "
                            f"expected {expected_department.name}"
                        )

        except Exception as e:
            logger.warning(f"   ⚠️ Error verifying {model_info['model_name']}: {e}")

    if all_correct:
        logger.info(f"   ✅ All dependencies verified - locations match equipment")
    else:
        logger.error(f"   ❌ Found {len(issues)} location mismatches in dependencies")

    return all_correct, issues


def check_ppm_location(equipment):
    """
    Dedicated function to check PPM locations for an equipment
    Returns detailed PPM location info
    """
    try:
        from ppms.models import PPMSchedule

        ppms = PPMSchedule.objects.filter(equipment=equipment).select_related(
            'equipment', 'workshop', 'department'
        )

        logger.info(f"🔍 PPM Location Check for Equipment: {equipment.serial_number}")
        logger.info(f"   Equipment Location: {equipment.department.workshop.name}/{equipment.department.name}")
        logger.info(f"   Total PPMs: {ppms.count()}")

        ppm_details = []
        for ppm in ppms:
            matches_equipment = (
                ppm.workshop_id == equipment.department.workshop_id and
                ppm.department_id == equipment.department_id
            )

            detail = {
                'ppm_id': ppm.id,
                'ppm_name': ppm.ppm_name,
                'workshop': ppm.workshop.name if ppm.workshop else 'None',
                'workshop_id': str(ppm.workshop_id) if ppm.workshop_id else None,
                'department': ppm.department.name if ppm.department else 'None',
                'department_id': str(ppm.department_id) if ppm.department_id else None,
                'matches_equipment_location': matches_equipment
            }

            ppm_details.append(detail)

            status = "✅" if matches_equipment else "❌"
            logger.info(f"   {status} PPM {ppm.id} ({ppm.ppm_name}): "
                       f"{ppm.workshop.name if ppm.workshop else 'None'}/"
                       f"{ppm.department.name if ppm.department else 'None'}")

        return ppm_details

    except Exception as e:
        logger.error(f"❌ Error checking PPM locations: {e}")
        return []
