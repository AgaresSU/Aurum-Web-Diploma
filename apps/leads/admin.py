from django.contrib import admin, messages

from apps.billing.services import create_invoice_from_lead

from .models import Lead, QuickConsultation


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "email", "service_type", "status", "source", "created_at")
    list_filter = ("status", "source", "service_type", "created_at")
    search_fields = ("name", "email", "subject", "task")
    readonly_fields = ("public_id", "created_at", "updated_at", "metadata")
    actions = ("mark_qualifying", "mark_proposal", "create_draft_invoice")

    @admin.action(description="Перевести на разбор")
    def mark_qualifying(self, request, queryset):
        queryset.update(status=Lead.Status.QUALIFYING)

    @admin.action(description="Готовится предложение")
    def mark_proposal(self, request, queryset):
        queryset.update(status=Lead.Status.PROPOSAL)

    @admin.action(description="Создать черновик счета и заказ")
    def create_draft_invoice(self, request, queryset):
        created = 0
        for lead in queryset:
            create_invoice_from_lead(lead)
            lead.status = Lead.Status.PROPOSAL
            lead.save(update_fields=("status", "updated_at"))
            created += 1
        messages.success(request, f"Создано черновиков счетов: {created}")


@admin.register(QuickConsultation)
class QuickConsultationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "display_name",
        "telegram_username",
        "client_email",
        "status",
        "quoted_amount",
        "invoice",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "client_name",
        "client_email",
        "telegram_username",
        "telegram_chat_id",
        "question",
        "manager_response",
    )
    readonly_fields = (
        "public_id",
        "telegram_chat_id",
        "telegram_user_id",
        "offer_accepted_at",
        "privacy_accepted_at",
        "created_at",
        "updated_at",
    )
