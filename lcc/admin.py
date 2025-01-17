from django.contrib import admin
from django.contrib.auth import get_user_model
from django.utils.safestring import mark_safe
from lcc import models


User = get_user_model()


class LegislationAdmin(admin.ModelAdmin):
    list_display = (
        '__str__', 'title', 'pk',
        'classifications_list', 'tags_list'
    )
    list_filter = ('law_type', 'country')
    search_fields = (
        'title', 'country__name',
        'classifications__name', 'tags__name'
    )
    actions = ['generate_pages']

    def classifications_list(self, obj):
        return '; '.join(
            obj.classifications.values_list('name', flat=True))

    def tags_list(self, obj):
        return '; '.join(
            obj.tags.values_list('name', flat=True))

    def generate_pages(self, request, queryset):
        generated = 0
        regenerated = 0
        no_pdf = 0
        for law in queryset:
            old_pages = law.pages.all()
            old_pages.delete()

            if not law.pdf_file:
                no_pdf += 1
                continue

            law.save_pdf_pages()

            if old_pages:
                regenerated += 1
            else:
                generated += 1
        self.message_user(
            request, (
                "Pages were generated for {} laws, regenerated for {} and {} "
                "were skipped due to absent PDF."
            ).format(generated, regenerated, no_pdf)
        )
    generate_pages.short_description = "(Re)generate text pages from PDF"

    classifications_list.short_description = "Classifications"
    tags_list.short_description = "Cross cutting categories"


class UserAdmin(admin.ModelAdmin):
    search_fields = ["username", "first_name", "last_name"]
    list_display = (
        "username", "first_name", "last_name", "email", "is_active",
        "get_approve_url"
    )
    list_filter = (
        "is_staff", "is_superuser", "is_active", "groups"
    )

    def get_approve_url(self, obj):
        url = obj.userprofile.approve_url
        link = ""
        if url:
            link = '<a href="%s">%s</a>' % (url, url)
        return mark_safe(link)
    get_approve_url.short_description = 'Approve URL'


# Register your models here.
admin.site.register(models.Legislation, LegislationAdmin)
admin.site.register(models.LegislationArticle)
admin.site.register(models.LegislationPage)
admin.site.register(models.UserProfile)
admin.site.register(models.Country)
admin.site.register(models.AssessmentProfile)
admin.site.register(models.TaxonomyTagGroup)
admin.site.register(models.TaxonomyTag)
admin.site.register(models.TaxonomyClassification)

admin.site.register(models.Gap)
admin.site.register(models.Question)
admin.site.register(models.Assessment)
admin.site.register(models.Answer)

admin.site.unregister(User)
admin.site.register(User, UserAdmin)
