import datetime
from django.views.decorators.http import require_http_methods
from django.shortcuts import redirect
from django.db import transaction, connection

from .common import (add_generic_form_error,
                     get_nested_item, get_deserialized_gleaned_data)
from .. import forms
from ..models import SubmittedPriceList
from ..decorators import handle_cancel
from frontend import ajaxform
from frontend.steps import Steps
from ..schedules import registry
from ..templatetags.analyze_contract import (
    Vocabulary,
    describe,
)

steps = Steps(
    template_format='data_capture/analyze_price_list/step_{}.html',
)


class AnalyzeStep1Form(forms.Step1Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields['contract_number']


@steps.step(label='Basic information')
@require_http_methods(["GET", "POST"])
def analyze_step_1(request, step):
    if request.method == 'GET':
        form = AnalyzeStep1Form(data=get_nested_item(
            request.session,
            ('data_capture:analyze_price_list', 'step_1_POST')
        ))
    else:
        form = AnalyzeStep1Form(request.POST)
        if form.is_valid():
            request.session['data_capture:analyze_price_list'] = {
                'step_1_POST': request.POST,
            }

            return redirect('data_capture:analyze_step_2')
        else:
            add_generic_form_error(request, form)

    return step.render(request, {
        'form': form,
    })


@steps.step(label='Upload price list')
@require_http_methods(["GET", "POST"])
@handle_cancel
def analyze_step_2(request, step):
    step_1_post_data = get_nested_item(request.session, (
        'data_capture:analyze_price_list',
        'step_1_POST'
    ))
    if step_1_post_data is None:
        return redirect('data_capture:analyze_step_1')
    step_1_form = AnalyzeStep1Form(step_1_post_data)
    if not step_1_form.is_valid():
        raise AssertionError('invalid step 1 data in session')
    schedule_class = step_1_form.cleaned_data['schedule_class']
    form_kwargs = dict(
        schedule=step_1_form.cleaned_data['schedule'],
    )

    if request.method == 'GET':
        form = forms.PriceListUploadForm(**form_kwargs)
    else:
        form = forms.PriceListUploadForm(
            request.POST,
            request.FILES,
            **form_kwargs
        )
        if form.is_valid():
            if 'gleaned_data' in form.cleaned_data:
                gleaned_data = form.cleaned_data['gleaned_data']

                session_pl = request.session['data_capture:analyze_price_list']
                session_pl['gleaned_data'] = registry.serialize(gleaned_data)
                session_pl['filename'] = form.cleaned_data['file'].name
                request.session.modified = True

            return ajaxform.redirect(request, 'data_capture:analyze_step_3')
        else:
            add_generic_form_error(request, form)

    return ajaxform.render(
        request,
        context=step.context({
            'form': form,
            'upload_example': schedule_class.render_upload_example(request)
        }),
        template_name=step.template_name,
        ajax_template_name='data_capture/analyze_price_list/upload_form.html',
    )


@steps.step(label='Analysis')
@require_http_methods(["GET"])
def analyze_step_3(request, step):
    gleaned_data = get_deserialized_gleaned_data(
        request,
        'data_capture:analyze_price_list'
    )
    if gleaned_data is None:
        return redirect('data_capture:analyze_step_2')

    valid_rows = []
    if gleaned_data.valid_rows:
        cursor = connection.cursor()
        vocab = Vocabulary.from_db(cursor)
        with transaction.atomic():
            sid = transaction.savepoint()
            price_list = SubmittedPriceList(
                is_small_business=False,
                submitter_id=0,
                escalation_rate=0,
                status_changed_at=datetime.datetime.now(),
                status_changed_by_id=0,
            )
            price_list.save()
            gleaned_data.add_to_price_list(price_list)
            for row in price_list.rows.all():
                analysis = describe(
                    cursor,
                    vocab,
                    row.labor_category,
                    row.min_years_experience,
                    row.education_level,
                    float(row.base_year_rate),
                )
                valid_rows.append({
                    'analysis': analysis,
                    'sin': row.sin,
                    'labor_category': row.labor_category,
                    'education_level': row.education_level,
                    'min_years_experience': row.min_years_experience,
                    'price': float(row.base_year_rate),
                })
            transaction.savepoint_rollback(sid)

    return step.render(request, {
        'gleaned_data': gleaned_data,
        'valid_rows': valid_rows,
    })
