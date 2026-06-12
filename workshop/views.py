from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from workshop.models import Workshop
from users.models import UserProfile
from django.db.models import Q, Count
from django.db import transaction, models
from django.http import JsonResponse
import json
from django.core.serializers.json import DjangoJSONEncoder
import logging
from django.apps import apps

# Set up logger
logger = logging.getLogger(__name__)

def is_hod(user):
    try:
        return user.userprofile.role == 'HOD'
    except UserProfile.DoesNotExist:
        return False

def discover_workshop_dependencies():
    """Automatically discover all models that have ForeignKey to Workshop"""
    workshop_models = []

    for app_config in apps.get_app_configs():
        for model in app_config.get_models():
            for field in model._meta.get_fields():
                if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                    if hasattr(field, 'related_model') and field.related_model == Workshop:
                        is_nullable = field.null if hasattr(field, 'null') else False

                        workshop_models.append({
                            'app_label': app_config.label,
                            'model_name': model.__name__,
                            'field_name': field.name,
                            'verbose_name': model._meta.verbose_name,
                            'model': model,
                            'is_nullable': is_nullable,
                            'on_delete': getattr(field.remote_field, 'on_delete', None).__name__ if hasattr(field.remote_field, 'on_delete') else 'UNKNOWN'
                        })
                        logger.debug(f"✅ Found FK: {app_config.label}.{model.__name__}.{field.name} (nullable={is_nullable})")

    logger.info(f"🔍 Total discovered models with Workshop FK/O2O: {len(workshop_models)}")
    return workshop_models

def get_workshop_dependencies_count(workshop):
    """Calculate total dependencies for a workshop using auto-discovery"""
    count = 0
    logger.info(f"🔍 Counting dependencies for workshop: {workshop.name} (ID: {workshop.id})")

    workshop_models = discover_workshop_dependencies()

    for model_info in workshop_models:
        try:
            model = model_info['model']
            field_name = model_info['field_name']
            field_count = model.objects.filter(**{field_name: workshop}).count()
            count += field_count

            if field_count > 0:
                logger.info(f"   📊 {model_info['app_label']}.{model_info['model_name']}.{field_name}: {field_count} records")

        except Exception as e:
            logger.warning(f"   ❌ Error checking {model_info['app_label']}.{model_info['model_name']}: {e}")
            continue

    logger.info(f"📈 Total dependencies: {count}")
    return count

def transfer_workshop_dependencies_auto(from_workshop, to_workshop):
    """Automatically discover and transfer all Workshop dependencies"""
    logger.info(f"🔄 AUTO TRANSFER START: {from_workshop.name} → {to_workshop.name if to_workshop else 'NULL'}")

    transferred_count = 0
    set_null_count = 0
    transfer_results = []
    updated_records = []

    workshop_models = discover_workshop_dependencies()
    logger.info(f"🔍 Discovered {len(workshop_models)} models with Workshop FK")

    for model_info in workshop_models:
        try:
            model = model_info['model']
            field_name = model_info['field_name']
            is_nullable = model_info['is_nullable']
            on_delete_behavior = model_info['on_delete']

            before_count = model.objects.filter(**{field_name: from_workshop}).count()

            if before_count > 0:
                logger.info(f"📊 {model_info['model_name']}.{field_name}: {before_count} records found")

                affected_records = list(model.objects.filter(**{field_name: from_workshop}))

                if is_nullable and on_delete_behavior == 'SET_NULL' and not to_workshop:
                    logger.info(f"   🔄 Setting {field_name} to NULL for {before_count} records")
                    updated = model.objects.filter(**{field_name: from_workshop}).update(
                        **{field_name: None, 'updated_at': timezone.now()}
                    )
                    set_null_count += updated
                    action = 'SET_NULL'
                else:
                    if to_workshop:
                        logger.info(f"   🔄 Transferring {before_count} records to {to_workshop.name}")
                        updated = model.objects.filter(**{field_name: from_workshop}).update(
                            **{field_name: to_workshop, 'updated_at': timezone.now()}
                        )
                        transferred_count += updated
                        action = 'TRANSFER'
                    else:
                        logger.error(f"   ❌ Cannot transfer non-nullable field without target workshop")
                        updated = 0
                        action = 'ERROR'

                if updated > 0:
                    for record in affected_records:
                        updated_records.append((model, record))

                after_count = model.objects.filter(**{field_name: from_workshop}).count()

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

@login_required
@user_passes_test(is_hod, login_url='/login/')
def transfer_workshop_dependencies(request, workshop_id):
    """NEW: Separate endpoint for transferring dependencies"""
    workshop = get_object_or_404(Workshop, id=workshop_id)

    if request.method == 'POST':
        transfer_workshop_id = request.POST.get('transfer_workshop_id')

        logger.info(f"🔄 TRANSFER REQUEST for: {workshop.name} (ID: {workshop.id})")
        logger.info(f"🎯 Transfer to: {transfer_workshop_id or 'NULL (for nullable fields)'}")

        # Count dependencies
        dependency_count = get_workshop_dependencies_count(workshop)
        logger.info(f"🧮 Dependencies found: {dependency_count}")

        if dependency_count == 0:
            return JsonResponse({
                'success': True,
                'message': 'No dependencies to transfer',
                'transferred_count': 0
            })

        workshop_models = discover_workshop_dependencies()
        nullable_fields_exist = any(m['is_nullable'] and m['on_delete'] == 'SET_NULL' for m in workshop_models)

        if dependency_count > 0 and not transfer_workshop_id and not nullable_fields_exist:
            return JsonResponse({
                'success': False,
                'error': f'Workshop has {dependency_count} dependencies. Please select a workshop to transfer them to.'
            }, status=400)

        try:
            transfer_start_time = timezone.now()

            with transaction.atomic():
                if transfer_workshop_id:
                    transfer_workshop = get_object_or_404(Workshop, id=transfer_workshop_id)
                    logger.info(f"🔄 STARTING AUTO-TRANSFER: {workshop.name} → {transfer_workshop.name}")
                    transferred_count, transfer_results = transfer_workshop_dependencies_auto(workshop, transfer_workshop)
                else:
                    transfer_workshop = None
                    logger.info(f"🔄 STARTING AUTO-CLEANUP: {workshop.name} → NULL")
                    transferred_count = 0
                    for model_info in workshop_models:
                        if model_info['is_nullable'] and model_info['on_delete'] == 'SET_NULL':
                            model = model_info['model']
                            field_name = model_info['field_name']
                            before_count = model.objects.filter(**{field_name: workshop}).count()

                            if before_count > 0:
                                affected_records = list(model.objects.filter(**{field_name: workshop}))
                                updated = model.objects.filter(**{field_name: workshop}).update(
                                    **{field_name: None, 'updated_at': timezone.now()}
                                )
                                transferred_count += updated

                                for record in affected_records:
                                    record.updated_at = timezone.now()
                                    record.save(update_fields=['updated_at'])

            # Verify transfer
            remaining_deps = get_workshop_dependencies_count(workshop)

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

        except Exception as e:
            logger.error(f"❌ Transfer failed: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Transfer failed: {str(e)}'
            }, status=500)

    return JsonResponse({'error': 'POST method required'}, status=400)

@login_required
@user_passes_test(is_hod, login_url='/login/')
def delete_workshop(request, workshop_id):
    """Delete workshop - expects dependencies already transferred and synced"""
    workshop = get_object_or_404(Workshop, id=workshop_id)

    if request.method == 'POST':
        logger.info(f"🗑️ DELETE REQUEST for: {workshop.name} (ID: {workshop.id})")

        # Verify no dependencies remain locally
        dependency_count = get_workshop_dependencies_count(workshop)

        if dependency_count > 0:
            error_msg = f'Workshop "{workshop.name}" still has {dependency_count} dependencies. Please transfer them first.'
            messages.error(request, error_msg)
            logger.error(f"❌ {error_msg}")
            return JsonResponse({
                'success': False,
                'error': error_msg
            }, status=400)

        try:
            deletion_timestamp = timezone.now()

            with transaction.atomic():
                workshop_name = workshop.name

                # Final safety check
                final_check = get_workshop_dependencies_count(workshop)
                if final_check > 0:
                    error_msg = f'Safety check failed! {final_check} dependencies still exist.'
                    messages.error(request, error_msg)
                    logger.error(f"❌ {error_msg}")
                    return JsonResponse({
                        'success': False,
                        'error': error_msg
                    }, status=400)

                # Mark for deletion
                workshop.refresh_from_db()
                workshop.pending_delete = True
                workshop.updated_at = deletion_timestamp
                workshop.save(update_fields=['pending_delete', 'updated_at'])

                logger.info(f"=" * 60)
                logger.info(f"🎉 SUCCESS: Workshop marked for deletion!")
                logger.info(f"=" * 60)
                logger.info(f"   📛 Workshop: {workshop_name}")
                logger.info(f"   🗑️ Deletion time: {deletion_timestamp}")
                logger.info(f"   ⏱️ Frontend ensured 45s+ gap from transfers")
                logger.info(f"=" * 60)

                messages.success(request, f'Workshop "{workshop_name}" marked for deletion successfully!')

                return JsonResponse({
                    'success': True,
                    'message': f'Workshop "{workshop_name}" marked for deletion'
                })

        except Exception as e:
            error_msg = f'Error marking workshop for deletion: {str(e)}'
            logger.error(f"❌ {error_msg}")
            import traceback
            logger.error(traceback.format_exc())
            messages.error(request, error_msg)
            return JsonResponse({
                'success': False,
                'error': error_msg
            }, status=500)

    return redirect('workshop:create_workshop')

@login_required
@user_passes_test(is_hod, login_url='/login/')
def create_workshop(request):
    if request.method == 'POST':
        name = request.POST.get('name').strip()
        category = request.POST.get('category')

        if name and category:
            if Workshop.objects.filter(name__iexact=name).exists():
                messages.error(request, f'A workshop named "{name}" already exists.')
            else:
                Workshop.objects.create(name=name, category=category)
                messages.success(request, 'Workshop created successfully!')
            return redirect('workshop:create_workshop')
        else:
            if not name:
                messages.error(request, 'Workshop name is required.')
            if not category:
                messages.error(request, 'Workshop category is required.')

    workshops = Workshop.objects.order_by('-created_at')
    workshops_json = json.dumps(
        list(workshops.values('id', 'name', 'category', 'pending_delete')),
        cls=DjangoJSONEncoder
    )

    return render(request, 'workshop/create_workshop.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'workshops': workshops,
        'workshops_json': workshops_json
    })

@login_required
@user_passes_test(is_hod, login_url='/login/')
def edit_workshop(request, workshop_id):
    workshop = get_object_or_404(Workshop, id=workshop_id)

    if request.method == 'POST':
        name = request.POST.get('name').strip()
        category = request.POST.get('category')

        if name and category:
            if Workshop.objects.filter(name__iexact=name).exclude(id=workshop_id).exists():
                messages.error(request, f'A workshop named "{name}" already exists.')
            else:
                workshop.name = name
                workshop.category = category
                workshop.save()
                messages.success(request, 'Workshop updated successfully!')
                return redirect('workshop:create_workshop')
        else:
            if not name:
                messages.error(request, 'Workshop name is required.')
            if not category:
                messages.error(request, 'Workshop category is required.')

    workshops = Workshop.objects.order_by('-created_at')
    workshops_json = json.dumps(
        list(workshops.values('id', 'name', 'category', 'pending_delete')),
        cls=DjangoJSONEncoder
    )

    return render(request, 'workshop/create_workshop.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'workshops': workshops,
        'workshops_json': workshops_json
    })

@login_required
@user_passes_test(is_hod, login_url='/login/')
def get_dependency_count(request, workshop_id):
    """API endpoint to get dependency count for a workshop"""
    try:
        workshop = get_object_or_404(Workshop, id=workshop_id)
        count = get_workshop_dependencies_count(workshop)

        return JsonResponse({
            'count': count,
            'workshop_name': workshop.name,
            'status': 'success'
        })
    except Exception as e:
        return JsonResponse({
            'count': 0,
            'status': 'error',
            'message': str(e)
        })
