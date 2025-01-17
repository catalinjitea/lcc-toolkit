import json
import operator

from functools import reduce

from django import views
from django.conf import settings
from django.contrib.auth import mixins
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q as DjQ
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.generic import (
    ListView, CreateView, DetailView, UpdateView, DeleteView
)
from elasticsearch_dsl import Q

from lcc import models, constants, forms
from lcc.constants import LEGISLATION_YEAR_RANGE
from lcc.documents import LegislationDocument
from lcc.views.base import TagGroupRender, TaxonomyFormMixin
from lcc.views.country import (
    CountryMetadataFiltering,
    POP_RANGES,
    HDI_RANGES,
    GDP_RANGES,
    GHG_LUCF,
    GHG_NO_LUCF,
)


CONN = settings.TAXONOMY_CONNECTOR


class HighlightedLaws:
    """
    This class wraps a Search instance and is compatible with Django's
    pagination API.
    """

    def __init__(self, search, sort=None):
        self.search = search
        self.sort =  sort

    def __getitem__(self, key):
        hits = self.search[key]
        if self.sort:
            return hits.sort(self.sort).to_queryset()
        laws = []
        matched_article_tags = []
        matched_article_classifications = []
        for hit, law in zip(hits, hits.to_queryset()):
            if hasattr(hit.meta, 'highlight'):
                highlights = hit.meta.highlight.to_dict()
                if 'abstract' in highlights:
                    law._highlighted_abstract = mark_safe(
                        ' […] '.join(highlights['abstract'])
                    )
                if 'pdf_text' in highlights:
                    law._highlighted_pdf_text = mark_safe(
                        ' […] '.join(
                            highlights['pdf_text']
                        ).replace('<pre>', '').replace('</pre>', '')
                    )
                if 'title' in highlights:
                    law._highlighted_title = mark_safe(highlights['title'][0])
                if 'classifications' in highlights:
                    law._highlighted_classifications = [
                        mark_safe(classification)
                        for classification in (
                            highlights['classifications'][0].split(CONN))
                    ]
                if 'article_classifications' in highlights:
                    matched_article_classifications += [
                        tag[4:-5] for tag in (
                            highlights['article_classifications'][0].split(CONN))
                        if '<em>' in tag
                    ]
                if 'tags' in highlights:
                    law._highlighted_tags = [
                        mark_safe(tag)
                        for tag in highlights['tags'][0].split(CONN)
                    ]
                if 'article_tags' in highlights:
                    matched_article_tags += [
                        tag[4:-5] for tag in (
                            highlights['article_tags'][0].split(CONN))
                        if '<em>' in tag
                    ]

            if hasattr(hit.meta, 'inner_hits'):
                law._highlighted_articles = []
                if hit.meta.inner_hits.articles:
                    for article in hit.meta.inner_hits.articles.hits:
                        article_dict = {
                            'pk': article.pk,
                            'code': article.code
                        }
                        if not hasattr(article.meta, 'highlight'):
                            continue
                        highlights = article.meta.highlight.to_dict()
                        matched_text = highlights.get('articles.text')
                        if matched_text:
                            article_dict['text'] = mark_safe(
                                ' […] '.join(matched_text)
                            )
                        matched_classifications = (
                            highlights.get(
                                'articles.classifications_text')
                        )
                        if matched_classifications:
                            article_dict['classifications'] = [
                                mark_safe(classification)
                                for classification in (
                                    matched_classifications[0].split(CONN))
                            ]
                        matched_tags = highlights.get(
                            'articles.tags_text')
                        if matched_tags:
                            article_dict['tags'] = [
                                mark_safe(tag)
                                for tag in (
                                    matched_tags[0].split(CONN))
                            ]
                        law._highlighted_articles.append(article_dict)
                elif matched_article_classifications or matched_article_tags:
                    # NOTE: This is a hack. ElasticSearch won't return
                    # highlighted article tags in some cases so this workaround
                    # is necessary. Please fix if you know how. Try searching
                    # for a keyword that is in the title of a law, and filtering
                    # by a tag that is assigned to an article of that law, but
                    # not the law itself. The query will work (it will only
                    # return the law that has such an article, and not others),
                    # but the inner_hits will be empty.
                    law._highlighted_articles = []
                    articles = law.articles.filter(
                        DjQ(tags__name__in=matched_article_tags) |
                        DjQ(
                            classifications__name__in=(
                                matched_article_classifications)
                        )
                    ).prefetch_related('tags')
                    for article in articles:
                        article_dict = {
                            'pk': article.pk,
                            'code': article.code,
                            'classifications': [
                                mark_safe('<em>{}</em>'.format(cl.name))
                                if cl.name in matched_article_classifications
                                else cl.name
                                for cl in article.classifications.all()
                            ],
                            'tags': [
                                mark_safe('<em>{}</em>'.format(tag.name))
                                if tag.name in matched_article_tags
                                else tag.name
                                for tag in article.tags.all()
                            ]
                        }
                        law._highlighted_articles.append(article_dict)
            laws.append(law)
        return laws

    def count(self):
        return self.search.count()


class LegislationExplorer(CountryMetadataFiltering, ListView):
    template_name = "legislation/explorer.html"
    model = models.Legislation

    def get_sort(self):
        promulgation_sort = self.request.GET.get("promulgation_sort")
        country_sort = self.request.GET.get("country_sort")
        if promulgation_sort:
            if promulgation_sort == '1':
                return 'year'
            else:
                return '-year'
        if country_sort:
            if country_sort == '1':
                return 'country_name'
            else:
                return '-country_name'

    def get_queryset(self):
        """
        Perform filtering using ElasticSearch instead of Postgres.
        Note that this DOES NOT return a QuerySet object, it returms a Page
        object instead. This is necessary because by transforming an
        elasticsearch-dsl Search object into a QuerySet a lot of functionality
        is lost, so we need to make things a bit more custom.
        """

        law_queries = []
        article_queries = []
        article_highlights = {}

        # jQuery's ajax function ads `[]` to duplicated querystring parameters
        # or parameters whose values are objects, so we have to take that into
        # account when looking for our values in the querystring. More into at:
        #   - http://api.jquery.com/jQuery.param/

        # List of strings representing TaxonomyClassification ids
        classification_ids = [
            int(pk) for pk in self.request.GET.getlist('classifications[]')]

        if classification_ids:

            classification_names = models.TaxonomyClassification.objects.filter(
                pk__in=classification_ids).values_list('name', flat=True)

            # Search root document for any of the classifications received
            law_queries.append(
                reduce(
                    operator.or_,
                    [
                        Q('match_phrase', classifications=name)
                        for name in classification_names
                    ]
                ) | reduce(
                    operator.or_,
                    [
                        Q('match_phrase', article_classifications=name)
                        for name in classification_names
                    ]
                )
            )

            # Search inside articles for any classifications
            article_queries.append(
                reduce(
                    operator.or_,
                    [
                        Q(
                            'match_phrase',
                            articles__classifications_text=name
                        ) for name in classification_names
                    ]
                ) | reduce(
                    operator.or_,
                    [
                        Q(
                            'match_phrase',
                            articles__parent_classifications=name
                        ) for name in classification_names
                    ]
                )
            )
            article_highlights['articles.classifications_text'] = {
                'number_of_fragments': 0
            }

        # List of strings representing TaxonomyTag ids
        tag_ids = [int(pk) for pk in self.request.GET.getlist('tags[]')]
        if tag_ids:
            tag_names = models.TaxonomyTag.objects.filter(
                pk__in=tag_ids).values_list('name', flat=True)

            # Search root document
            law_queries.append(
                reduce(
                    operator.or_,
                    [
                        Q('match_phrase', tags=name)
                        for name in tag_names
                    ]
                ) | reduce(
                    operator.or_,
                    [
                        Q('match_phrase', article_tags=name)
                        for name in tag_names
                    ]
                )
            )

            # Search inside articles
            article_queries.append(
                reduce(
                    operator.or_,
                    [
                        Q('match_phrase', articles__tags_text=name)
                        for name in tag_names
                    ]
                ) | reduce(
                    operator.or_,
                    [
                        Q('match_phrase', articles__parent_tags=name)
                        for name in tag_names
                    ]
                )
            )
            article_highlights['articles.tags_text'] = {
                'number_of_fragments': 0
            }

        # String to be searched in all text fields (full-text search using
        # elasticsearch's default best_fields strategy)
        q = self.request.GET.get('q')
        law_q_query = []
        article_q_query = []
        if q:
            # Compose root document search
            law_q_query = [
                Q(
                    'multi_match', query=q, fields=[
                        'title', 'abstract', 'pdf_text', 'classifications',
                        'tags'
                    ]
                )
            ]
            # Compose nested document search inside articles
            article_q_query = [
                Q('multi_match', query=q, fields=['articles.text']) |
                Q(
                    'constant_score', boost=50, filter={
                        "match_phrase": {
                            "articles.text": q
                        }
                    }
                )
            ]
            article_q_highlights = {'articles.text': {}}

        search = LegislationDocument.search()
        sort = self.get_sort()

        if not sort:
            if q:
                q_in_law = Q(
                    'bool', must=law_queries + law_q_query + ([
                        Q(
                            'nested',
                            score_mode='max',
                            # boost=10,
                            path='articles',
                            query=Q(
                                reduce(
                                    operator.and_,
                                    article_queries
                                )
                            ),
                            inner_hits={
                                'highlight': {'fields': article_highlights}
                            }
                        )
                    ] if article_queries else [])
                )
                q_in_article = Q(
                    'bool', must=law_queries + ([
                        Q(
                            'nested',
                            score_mode='max',
                            # boost=10,
                            path='articles',
                            query=Q(
                                reduce(
                                    operator.and_,
                                    article_queries + article_q_query
                                )
                            ),
                            inner_hits={
                                'highlight': {
                                    'fields': {
                                        **article_highlights,
                                        **article_q_highlights
                                    }
                                }
                            }
                        )
                    ] if article_queries or article_q_query else [])
                )
                search = search.query(q_in_law | q_in_article).highlight(
                    'abstract', 'pdf_text'
                )
            else:
                root_query = [Q(
                    reduce(
                        operator.and_,
                        law_queries
                    )
                )] if law_queries else []
                nested_query = [Q(
                    'nested',
                    score_mode='max',
                    # boost=10,
                    path='articles',
                    query=Q(
                        reduce(
                            operator.and_,
                            article_queries
                        )
                    ),
                    inner_hits={
                        'highlight': {'fields': article_highlights}
                    }
                )] if article_queries else []
                final_query = []
                if root_query:
                    final_query += root_query
                    if nested_query:
                        # Necessary for highlights
                        final_query += root_query and nested_query
                if final_query:
                    search = search.query(
                        'bool', should=final_query,
                        minimum_should_match=1
                    )

            # String representing country iso code
            countries = self.request.GET.getlist('countries[]')
            selected_countries = False
            if countries:
                selected_countries = True
            filtering_countries = self.filter_countries(self.request, selected_countries=selected_countries)
            if countries or filtering_countries.count() != models.Country.objects.all().count():
                countries.extend([country.iso for country in filtering_countries])
                search = search.query('terms', country=countries)

            # String representing law_type
            law_types = self.request.GET.getlist('law_types[]')
            if law_types:
                search = search.query('terms', law_type=law_types)

            # String representing the minimum year allowed in the results
            from_year = self.request.GET.get('from_year')
            # String representing the maximum year allowed in the results
            to_year = self.request.GET.get('to_year')

            if all([from_year, to_year]):
                search = search.query(
                    Q('range', year={'gte': int(from_year), 'lte': int(to_year)}) |
                    Q('range', year_amendment={
                        'gte': int(from_year), 'lte': int(to_year)}) |
                    Q('range', year_mentions={
                        'gte': int(from_year), 'lte': int(to_year)})
                )

            search = search.highlight(
                'title', 'classifications', 'article_classifications', 'tags',
                'article_tags', number_of_fragments=0
            )

            if not any([classification_ids, tag_ids, q]):
                # If there is no score to sort by, sort by id
                search = search.sort('id')

            # import json; print(json.dumps(search.to_dict(), indent=2))

        all_laws = HighlightedLaws(search, sort)

        paginator = Paginator(all_laws, settings.LAWS_PER_PAGE)

        page = self.request.GET.get('page', 1)

        try:
            laws = paginator.page(page)
        except PageNotAnInteger:
            # If page is not an integer, deliver first page.
            laws = paginator.page(1)
        except EmptyPage:
            # If page is out of range (e.g. 9999), deliver last page of results.
            laws = paginator.page(paginator.num_pages)
        return laws

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group_tags = models.TaxonomyTagGroup.objects.all()
        top_classifications = models.TaxonomyClassification.objects.filter(
            level=0).order_by('code')
        countries = models.Country.objects.all().order_by('name')
        regions = models.Region.objects.all().order_by('name')
        sub_regions = models.SubRegion.objects.all().order_by('name')
        legal_systems = models.LegalSystem.objects.all().order_by('name')

        laws = self.object_list

        legislation_year = (
            LEGISLATION_YEAR_RANGE[0],
            LEGISLATION_YEAR_RANGE[len(LEGISLATION_YEAR_RANGE) - 1]
        )
        filters_dict = dict(self.request.GET)
        context.update({
            'laws': laws,
            'group_tags': group_tags,
            'top_classifications': top_classifications,
            'countries': countries,
            'regions': regions,
            'sub_regions': sub_regions,
            'legal_systems': legal_systems,
            'population': POP_RANGES,
            'hdi2015': HDI_RANGES,
            'gdp_capita': GDP_RANGES,
            'ghg_no_lucf': GHG_NO_LUCF,
            'ghg_lucf': GHG_LUCF,
            'legislation_type': constants.LEGISLATION_TYPE,
            'legislation_year': legislation_year,
            'min_year': settings.MIN_YEAR,
            'max_year': settings.MAX_YEAR,
            'from_year': filters_dict.pop('from_year', [settings.MIN_YEAR])[0],
            'to_year': filters_dict.pop('to_year', [settings.MAX_YEAR])[0],
            'filters': json.dumps(filters_dict)
        })
        return context


class LegislationAdd(mixins.LoginRequiredMixin, TaxonomyFormMixin,
                     CreateView):
    template_name = "legislation/add.html"
    form_class = forms.LegislationForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        countries = sorted(models.Country.objects.all(), key=lambda c: c.name)
        context.update({
            "countries": countries,
            "legislation_type": constants.LEGISLATION_TYPE,
            "tag_groups": [
                TagGroupRender(tag_group)
                for tag_group in models.TaxonomyTagGroup.objects.all()
            ],
            "available_languages": constants.ALL_LANGUAGES,
            "source_types": constants.SOURCE_TYPE,
            "geo_coverage": constants.GEOGRAPHICAL_COVERAGE,
            "adoption_years": LEGISLATION_YEAR_RANGE,
            "classifications": models.TaxonomyClassification.objects.filter(
                level=0).order_by('code')
        })
        return context

    def form_valid(self, form):
        legislation = form.save()
        legislation.save_pdf_pages()

        if "save-and-continue-btn" in self.request.POST:
            return HttpResponseRedirect(
                reverse('lcc:legislation:articles:add',
                        kwargs={'legislation_pk': legislation.pk})
            )
        if "save-btn" in self.request.POST:
            return HttpResponseRedirect(reverse("lcc:legislation:explorer"))


class LegislationView(DetailView):
    template_name = "legislation/detail.html"
    pk_url_kwarg = 'legislation_pk'
    model = models.Legislation
    context_object_name = 'law'


class LegislationPagesView(views.View):

    def get(self, request, *args, **kwargs):
        law = get_object_or_404(models.Legislation,
                                pk=kwargs['legislation_pk'])
        pages = law.pages.all()
        content = {}
        for page in pages:
            content[page.page_number] = page.page_text

        return JsonResponse(content)


class LegislationEditView(mixins.LoginRequiredMixin, TaxonomyFormMixin,
                          UpdateView):
    template_name = "legislation/edit.html"
    model = models.Legislation
    form_class = forms.LegislationForm
    pk_url_kwarg = 'legislation_pk'
    context_object_name = 'law'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        countries = sorted(models.Country.objects.all(), key=lambda c: c.name)
        context.update({
            "countries": countries,
            "available_languages": constants.ALL_LANGUAGES,
            "legislation_type": constants.LEGISLATION_TYPE,
            "tag_groups": [
                TagGroupRender(tag_group)
                for tag_group in models.TaxonomyTagGroup.objects.all()
            ],
            "classifications": models.TaxonomyClassification.objects.filter(
                level=0).order_by('code'),
            "adoption_years": LEGISLATION_YEAR_RANGE,
            "source_types": constants.SOURCE_TYPE,
            "geo_coverage": constants.GEOGRAPHICAL_COVERAGE,
        })
        return context

    def form_valid(self, form):
        legislation = form.save()
        if 'pdf_file' in self.request.FILES:
            models.LegislationPage.objects.filter(
                legislation=legislation).delete()
            legislation.save_pdf_pages()

        return HttpResponseRedirect(
            reverse('lcc:legislation:details',
                    kwargs={'legislation_pk': legislation.pk})
        )


class LegislationDeleteView(mixins.LoginRequiredMixin, DeleteView):
    model = models.Legislation
    pk_url_kwarg = 'legislation_pk'

    def get_success_url(self, **kwargs):
        return reverse("lcc:legislation:explorer")

    def get(self, *args, **kwargs):
        return self.post(*args, **kwargs)
