import logging
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_GET
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.core.paginator import Paginator
from django.urls import reverse
from django.http import JsonResponse
from decimal import Decimal, InvalidOperation
from django.utils import timezone
from django.contrib.auth import get_user_model

from CalSoft.models import CalibrationProcedure, CalibrationParameter, Standard, Parameter, SubParameter, SetValue, CalibrationSession
from CalSoft.forms import CalibrationProcedureForm, ParameterFormSet, ProcedureSearchForm

User = get_user_model()
logger = logging.getLogger(__name__)


@login_required
def procedure_list(request):
    procedures = CalibrationProcedure.objects.filter(
        active_status=True).order_by('-created_at')

    search_query = request.GET.get('search', '')
    created_by = request.GET.get('created_by', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if search_query:
        procedures = procedures.filter(
            Q(name__icontains=search_query) | Q(description__icontains=search_query)
        )
    if created_by:
        procedures = procedures.filter(created_by_id=created_by)
    if date_from:
        procedures = procedures.filter(created_at__gte=date_from)
    if date_to:
        procedures = procedures.filter(created_at__lte=date_to)

    paginator = Paginator(procedures, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # AJAX response - return only the procedures table HTML
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        html = render(request, 'Calibrition/partials/procedures_table.html', {
            'page_obj': page_obj,
        }).content.decode('utf-8')
        return JsonResponse({'html': html})

    context = {
        'page_obj': page_obj,
        'total_procedures': CalibrationProcedure.objects.filter(active_status=True).count(),
        'active_procedures': CalibrationProcedure.objects.filter(active_status=True).count(),
        'recent_procedures': CalibrationProcedure.objects.filter(
            active_status=True, created_at__gte=timezone.now().replace(day=1)
        ).count(),
        'sessions_count': CalibrationSession.objects.count(),
        'users': User.objects.all(),
        'current_filters': {
            'search': search_query,
            'created_by': created_by,
            'date_from': date_from,
            'date_to': date_to,
        },
        'show_sidebar': True,
    }
    return render(request, 'Calibrition/procedures-list.html', context)


@login_required
@require_http_methods(["GET", "POST"])
def procedure_create(request):
    if request.method == 'POST':
        form = CalibrationProcedureForm(request.POST)
        parameter_formset = ParameterFormSet(request.POST, prefix='parameters')

        if form.is_valid() and parameter_formset.is_valid():
            try:
                with transaction.atomic():
                    procedure = form.save(commit=False)
                    procedure.created_by = request.user
                    procedure.save()

                    for param_form in parameter_formset:
                        if param_form.cleaned_data and not param_form.cleaned_data.get('DELETE', False):
                            parameter = param_form.save(commit=False)
                            parameter.procedure = procedure
                            standard_id = param_form.cleaned_data.get('standard_reference')
                            if standard_id:
                                try:
                                    standard = Standard.objects.get(id=standard_id)
                                    parameter.standard_reference = standard.serial_number
                                except Standard.DoesNotExist:
                                    logger.warning(f"Standard with ID {standard_id} not found")
                            parameter.save()

                            param_prefix = param_form.prefix
                            param_index = param_prefix.split('-')[-1] if param_prefix else '0'
                            has_sub_parameters = request.POST.get(
                                f'parameters-{param_index}-has_sub_parameters') == 'on'

                            sub_parameters_created = []
                            if has_sub_parameters:
                                sub_param_prefix = f'parameters-{param_index}-sub_parameters'
                                total_sub_forms = request.POST.get(f'{sub_param_prefix}-TOTAL_FORMS', '0')
                                try:
                                    total_sub_forms = int(total_sub_forms)
                                    for sub_idx in range(total_sub_forms):
                                        if request.POST.get(f'{sub_param_prefix}-{sub_idx}-DELETE') != 'on':
                                            sub_name = request.POST.get(
                                                f'{sub_param_prefix}-{sub_idx}-name', '').strip()
                                            sub_tolerance = request.POST.get(
                                                f'{sub_param_prefix}-{sub_idx}-tolerance', '1.0')
                                            sub_order = request.POST.get(
                                                f'{sub_param_prefix}-{sub_idx}-order', '0')
                                            if sub_name:
                                                sub_parameter = SubParameter.objects.create(
                                                    parameter=parameter,
                                                    name=sub_name,
                                                    tolerance=Decimal(str(sub_tolerance)),
                                                    order=int(sub_order)
                                                )
                                                sub_parameters_created.append((sub_idx, sub_parameter))
                                except ValueError as e:
                                    logger.error(f"Error processing sub-parameters: {str(e)}")

                            set_value_prefix = f'parameters-{param_index}-set_values'
                            total_set_forms = request.POST.get(f'{set_value_prefix}-TOTAL_FORMS', '0')
                            set_values_created = 0
                            try:
                                total_set_forms = int(total_set_forms)
                                for set_idx in range(total_set_forms):
                                    if request.POST.get(f'{set_value_prefix}-{set_idx}-DELETE') != 'on':
                                        set_value = request.POST.get(
                                            f'{set_value_prefix}-{set_idx}-value', '').strip()
                                        set_order = request.POST.get(
                                            f'{set_value_prefix}-{set_idx}-order', '0')
                                        sub_param_idx = request.POST.get(
                                            f'{set_value_prefix}-{set_idx}-sub_parameter', '')
                                        if set_value:
                                            try:
                                                associated_sub_param = None
                                                if sub_param_idx and sub_param_idx.isdigit():
                                                    sub_param_idx = int(sub_param_idx)
                                                    for sub_idx, sub_param in sub_parameters_created:
                                                        if sub_idx == sub_param_idx:
                                                            associated_sub_param = sub_param
                                                            break
                                                SetValue.objects.create(
                                                    parameter=parameter,
                                                    sub_parameter=associated_sub_param,
                                                    value=Decimal(str(set_value)),
                                                    order=int(set_order)
                                                )
                                                set_values_created += 1
                                            except (ValueError, InvalidOperation) as e:
                                                logger.error(
                                                    f"Invalid set value '{set_value}' for parameter {parameter.name}: {str(e)}")
                                                messages.error(
                                                    request, f"Invalid set value '{set_value}' for parameter {parameter.name}")
                                                return render(request, 'Calibrition/procedure_form.html', {
                                                    'form': form,
                                                    'parameter_formset': parameter_formset,
                                                    'show_sidebar': True,
                                                })
                            except ValueError as e:
                                logger.error(f"Error processing set values: {str(e)}")

                            if set_values_created == 0:
                                logger.error(
                                    f"No set value data found for parameter {parameter.name}")
                                messages.error(
                                    request, f"At least one set value is required for parameter {parameter.name}")
                                return render(request, 'Calibrition/procedure_form.html', {
                                    'form': form,
                                    'parameter_formset': parameter_formset,
                                    'show_sidebar': True,
                                })

                    messages.success(
                        request, f'Procedure "{procedure.name}" created successfully.')
                    return redirect('calibration:procedure_list')
            except Exception as e:
                logger.error(f"Error creating procedure: {str(e)}", exc_info=True)
                messages.error(request, f'Error creating procedure: {str(e)}')
        else:
            logger.error(f"Form errors: {form.errors}")
            logger.error(f"ParameterFormSet errors: {parameter_formset.errors}")

    else:
        form = CalibrationProcedureForm()
        parameter_formset = ParameterFormSet(prefix='parameters')

    context = {
        'form': form,
        'parameter_formset': parameter_formset,
        'show_sidebar': True,
    }
    return render(request, 'Calibrition/procedure_form.html', context)


def procedure_detail(request, pk):
    try:
        procedure = get_object_or_404(CalibrationProcedure, pk=pk)
        recent_sessions = getattr(procedure, 'sessions', getattr(
            procedure, 'calibrationsession_set', None))
        if recent_sessions:
            recent_sessions = recent_sessions.all().order_by('-timestamp')[:10]
        else:
            recent_sessions = []

        parameters = []
        for param in procedure.parameters.all().prefetch_related('set_values', 'sub_parameters__set_values'):
            main_set_values = param.set_values.filter(sub_parameter=None)
            param_data = {
                'id': param.id,
                'name': param.name,
                'unit': param.unit,
                'num_readings': param.num_readings,
                'standard_reference': param.standard_reference,
                'reference_uncertainty': param.reference_uncertainty,
                'coverage_factor': param.coverage_factor,
                'tolerance': param.tolerance,
                'main_set_values': main_set_values,
                'sub_parameters': []
            }
            for sub_param in param.sub_parameters.all():
                sub_param_data = {
                    'id': sub_param.id,
                    'name': sub_param.name,
                    'tolerance': sub_param.tolerance,
                    'order': sub_param.order,
                    'set_values': sub_param.set_values.all()
                }
                param_data['sub_parameters'].append(sub_param_data)
            parameters.append(param_data)

        context = {
            'procedure': procedure,
            'recent_sessions': recent_sessions,
            'parameters': parameters,
            'show_sidebar': True,
        }
        return render(request, 'Calibrition/procedure_detail.html', context)

    except Exception as e:
        logger.error(f"Error in procedure_detail: {str(e)}", exc_info=True)
        return render(request, 'Calibrition/procedure_detail.html', {'error': str(e), 'show_sidebar': True}, status=500)


@login_required
@require_http_methods(["GET", "POST"])
def procedure_edit(request, pk):
    """Edit an existing calibration procedure."""
    procedure = get_object_or_404(CalibrationProcedure, pk=pk, active_status=True)

    if request.method == 'POST':
        form = CalibrationProcedureForm(request.POST, instance=procedure)
        parameter_formset = ParameterFormSet(
            request.POST, prefix='parameters', instance=procedure)

        if form.is_valid() and parameter_formset.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    parameters = parameter_formset.save(commit=False)

                    for param in parameters:
                        param.procedure = procedure
                        param.save()

                    for param_to_delete in parameter_formset.deleted_objects:
                        param_to_delete.delete()

                    messages.success(
                        request, f'Procedure "{procedure.name}" updated successfully.')
                    return redirect('calibration:procedure_detail', pk=procedure.pk)
            except Exception as e:
                messages.error(request, f'Error updating procedure: {str(e)}')
        else:
            messages.error(request, 'Please correct the errors in the form.')
    else:
        form = CalibrationProcedureForm(instance=procedure)
        parameter_formset = ParameterFormSet(
            prefix='parameters', instance=procedure)

        for param_form in parameter_formset:
            param_instance = param_form.instance
            if param_instance.pk:
                param_form.sub_parameters = SubParameter.objects.filter(
                    parameter=param_instance)
                param_form.set_values = SetValue.objects.filter(
                    Q(parameter=param_instance) & Q(sub_parameter=None)
                )

    context = {
        'form': form,
        'parameter_formset': parameter_formset,
        'procedure': procedure,
        'show_sidebar': True,
    }

    return render(request, 'Calibrition/procedure_edit.html', context)


@login_required
@require_http_methods(["POST"])
def procedure_delete(request, pk):
    """Delete a calibration procedure with pending delete logic"""
    try:
        procedure = get_object_or_404(CalibrationProcedure, pk=pk)
        procedure_name = procedure.name

        if hasattr(procedure, 'pending_delete'):
            procedure.pending_delete = True
            procedure.updated_at = timezone.now()
            procedure.save(update_fields=['pending_delete', 'updated_at'])

            CalibrationAuditLog.objects.create(
                user=request.user,
                action='procedure_pending_delete',
                description=f"Procedure '{procedure_name}' marked for deletion (pending sync to HQ) by {request.user.username}"
            )
            messages.success(
                request,
                f"Procedure '{procedure_name}' marked for deletion. It will be removed after syncing with HQ.",
                extra_tags="procedure delete pending success"
            )
        else:
            procedure.is_active = False
            procedure.modified_date = timezone.now()
            procedure.save(update_fields=['is_active', 'modified_date'])

            CalibrationAuditLog.objects.create(
                user=request.user,
                action='procedure_soft_delete',
                description=f"Procedure '{procedure_name}' soft-deleted (marked inactive) by {request.user.username}"
            )
            messages.success(
                request,
                f"Procedure '{procedure_name}' deleted successfully.",
                extra_tags="procedure delete success",
            )

        return redirect('calibration:procedure_list')

    except Exception as e:
        logger.error(f"Error deleting procedure {pk} for user {request.user.username}: {e}")
        messages.error(
            request,
            f"Error deleting procedure: {str(e)}",
            extra_tags="procedure delete error",
        )
        return redirect('calibration:procedure_list')