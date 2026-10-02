from django.contrib import admin

from .models import CaseStudy, FAQItem, Service, TemplateProduct, Testimonial


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "service_type",
        "price_prefix",
        "price_amount",
        "price_unit",
        "price_note",
        "is_featured",
        "is_published",
        "sort_order",
        "updated_at",
    )
    list_filter = ("service_type", "is_featured", "is_published")
    search_fields = ("title", "short_description", "full_description", "business_value")
    prepopulated_fields = {"slug": ("title",)}
    list_editable = (
        "price_prefix",
        "price_amount",
        "price_unit",
        "price_note",
        "is_featured",
        "is_published",
        "sort_order",
    )
    readonly_fields = ("price_display",)
    fieldsets = (
        (
            "Услуга",
            {
                "fields": (
                    "title",
                    "slug",
                    "service_type",
                    "short_description",
                    "full_description",
                    "business_value",
                    "deliverables",
                )
            },
        ),
        (
            "Цена для сайта и модерации Robokassa",
            {
                "fields": (
                    "price_display",
                    "price_prefix",
                    "price_amount",
                    "price_unit",
                    "price_note",
                ),
                "description": "Эти значения автоматически выводятся на главной странице, странице услуг и карточке услуги.",
            },
        ),
        ("Публикация", {"fields": ("is_featured", "is_published", "sort_order")}),
    )


@admin.register(CaseStudy)
class CaseStudyAdmin(admin.ModelAdmin):
    list_display = ("title", "industry", "is_featured", "is_published", "sort_order", "updated_at")
    list_filter = ("industry", "is_featured", "is_published")
    search_fields = ("title", "challenge", "solution", "result", "stack")
    prepopulated_fields = {"slug": ("title",)}
    list_editable = ("is_featured", "is_published", "sort_order")


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = (
        "client_name",
        "submitted_by",
        "client_details",
        "project_title",
        "is_featured",
        "is_published",
        "sort_order",
        "updated_at",
    )
    list_filter = ("is_featured", "is_published")
    search_fields = ("client_name", "client_details", "project_title", "text")
    list_editable = ("is_featured", "is_published", "sort_order")
    readonly_fields = ("submitted_by", "publication_consent_at", "created_at", "updated_at")


@admin.register(TemplateProduct)
class TemplateProductAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "template_type",
        "category",
        "industry",
        "is_paid_ready",
        "is_published",
        "sort_order",
        "updated_at",
    )
    list_filter = ("template_type", "category", "industry", "is_paid_ready", "is_published")
    search_fields = ("title", "short_description", "conversion_focus", "contents", "implementation_notes")
    prepopulated_fields = {"slug": ("title",)}
    list_editable = ("is_paid_ready", "is_published", "sort_order")


@admin.register(FAQItem)
class FAQItemAdmin(admin.ModelAdmin):
    list_display = ("question", "is_published", "sort_order", "updated_at")
    list_filter = ("is_published",)
    search_fields = ("question", "answer")
    list_editable = ("is_published", "sort_order")
