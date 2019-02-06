import ast

from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import DetailView, UpdateView, DeleteView

import lcc.models as models
import lcc.forms as forms

class CountryMetadataFiltering:

    BOOLEAN_FIELDS = [
        "cw",
        "small_cw",
        "un",
        "ldc",
        "lldc",
        "sid",
    ]

    LIST_FIELDS = [
        "region",
        "sub_region",
        "legal_system",
    ]

    RANGE_FIELDS = {
        "population": models.POP_RANGES,
        "hdi2015": models.HDI_RANGES,
        "gdp_capita": models.GDP_RANGES,
        "ghg_no_lucf": models.GHG_NO_LUCF,
        "ghg_lucf": models.GHG_LUCF,
    }

    def __init__(self, *args, **kwargs):
        self.data = {}
        self.countries = models.Country.objects.all()
        return super(CountryMetadataFiltering, self).__init__(*args, **kwargs)

    def filter_boolean_fields(self, request, field):
        if request.GET.get(field):
            self.data[field] = ast.literal_eval(request.GET.get(field))

    def filter_list_fields(self, request, field):
        if request.GET.getlist(field):
            field_name = "{}__name__in".format(field)
            self.data[field_name] = request.GET.getlist(field)

    def filter_range_fields(self, request, field):
        if request.GET.getlist(field):
            min_value = self.RANGE_FIELDS[field][int(request.GET.get(field))][0]
            max_value = self.RANGE_FIELDS[field][int(request.GET.get(field))][1]
            min_field_name = "{}__gte".format(field)
            max_field_name = "{}__lte".format(field)
            self.data[min_field_name] = min_value
            self.data[max_field_name] = max_value

    def filter_countries(self, request, filteres=None):
        for field in self.BOOLEAN_FIELDS:
            self.filter_boolean_fields(request, field)

        for field in self.LIST_FIELDS:
            self.filter_list_fields(request, field)

        for field in self.RANGE_FIELDS:
            self.filter_range_fields(request,field)

        return self.countries.filter(**self.data)


class Metadata:
    labels = dict(
        population_range='Population range',
        hdi2015_range='HDI Range',
        gdp_capita_range='GDP Range',
        ghg_no_lucf_range=(
            'Total GHG Emissions excluding LUCF MtCO2e 2014 ranges'
        ),
        ghg_lucf_range='Total GHG Emissions including LUCF MtCO2e 2014 ranges'
    )

    def __init__(self, original, customised):
        self.form = forms.CountryBase()
        self.original = original
        self.customised = customised

    def __iter__(self):
        for name in self.form.fields:
            yield getattr(self, name)
            range = f'{name}_range'
            if hasattr(self.original, range):
                yield getattr(self, range)

    @staticmethod
    def _get_value(target, name):
        value = getattr(target, name)
        return (
            [v.name for v in value.all()]
            if hasattr(value, 'all')
            else value
        )

    def _get_label(self, name):
        return (
            self.labels[name]
            if name in self.labels
            else self.form[name].label
        )

    def _get_type(self, name):
        field = self.form.fields.get(name, None)

        if not field:
            return None

        if getattr(field.widget, 'allow_multiple_selected', None):
            return 'multiple'

        return field.widget.input_type

    def __getattr__(self, name):
        val_orig = self._get_value(self.original, name)
        val_custom = (
            self._get_value(self.customised, name)
            if self.is_customised()
            else val_orig
        )

        return dict(
            value=val_custom,
            orig=val_orig,
            name=name,
            label=self._get_label(name),
            type=self._get_type(name),
            modified=val_custom != val_orig
        )

    def is_customised(self):
        return self.customised is not None


class Details(DetailView):
    template_name = 'country/view.html'
    model = models.Country
    pk_url_kwarg = 'iso'

    def get_object(self):
        iso = self.kwargs.get(self.pk_url_kwarg)
        return get_object_or_404(
            self.model,
            iso=iso
        )

    def get_context_data(self, **kwargs):
        countries = models.Country.objects.all()
        context = super().get_context_data(**kwargs)
        context['countries'] = countries
        context['country'] = self.object
        try:
            metadata_user = models.AssessmentProfile.objects.get(
                country__iso=self.object.iso,
                user=self.request.user_profile
            )
        except models.AssessmentProfile.DoesNotExist:
            metadata_user = None

        context['meta'] = Metadata(self.object, metadata_user)

        return context


def _get_user_metadata(iso, user_profile):
    try:
        meta = models.AssessmentProfile.objects.get(
            country__iso=iso, user=user_profile
        )
    except models.AssessmentProfile.DoesNotExist:
        original = models.Country.objects.get(iso=iso)
        meta = original.clone_to_profile(user_profile)
    return meta


class Customise(UpdateView):
    template_name = 'country/customise.html'
    model = models.AssessmentProfile
    form_class = forms.CustomiseCountry
    pk_url_kwarg = 'iso'


    @transaction.atomic
    def get_object(self):
        iso = self.kwargs.get(self.pk_url_kwarg)
        return _get_user_metadata(iso, self.request.user_profile)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['country'] = self.object.country
        try:
            metadata_user = self.model.objects.get(
                country__iso=self.object.country.iso,
                user=self.request.user_profile
            )
        except self.model.DoesNotExist:
            metadata_user = None
        iso = self.kwargs.get(self.pk_url_kwarg)
        
        origin = get_object_or_404(
            models.Country,
            iso=iso,
        )
        context['meta'] = Metadata(origin, metadata_user)
        return context

    @transaction.atomic
    def form_valid(self, form):
        if self.request.POST.get('save'):
            form.save()

        elif self.request.POST.get('discard'):
            form.delete()

        return HttpResponseRedirect(self.get_success_url())


class DeleteCustomisedProfile(DeleteView):
    model = models.AssessmentProfile
    pk_url_kwarg = 'iso'

    def get_success_url(self, **kwargs):
        iso = self.kwargs.get(self.pk_url_kwarg)
        return reverse('lcc:country:view', kwargs={
            'iso': iso
        })

    def get_object(self):
        iso = self.kwargs.get(self.pk_url_kwarg)
        return models.AssessmentProfile.objects.get(
            user=self.request.user_profile,
            country__iso=iso
        )

    def get(self, *args, **kwargs):
        return self.post(*args, **kwargs)
