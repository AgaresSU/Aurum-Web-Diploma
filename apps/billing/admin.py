from django.contrib import admin

from .models import Invoice, InvoiceItem, ManagedSite, Order, Payment
from .services import mark_payment_succeeded


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    fields = ("name", "note", "quantity", "unit_price")
    extra = 1


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = (
        "provider",
        "status",
        "amount",
        "signature_valid",
        "provider_state_code",
        "last_reconciled_at",
        "created_at",
        "paid_at",
    )
    readonly_fields = ("provider_state_code", "last_reconciled_at", "created_at", "paid_at")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "client_email", "amount", "status", "created_at")
    list_filter = ("status", "created_at", "due_date")
    search_fields = ("title", "client_name", "client_email", "description")
    readonly_fields = ("public_token", "client_requisites", "created_at", "updated_at")
    inlines = (InvoiceItemInline, PaymentInline)
    actions = ("mark_issued", "mark_paid")

    @admin.action(description="Выставить выбранные счета")
    def mark_issued(self, request, queryset):
        queryset.update(status=Invoice.Status.ISSUED)

    @admin.action(description="Отметить как оплаченные вручную")
    def mark_paid(self, request, queryset):
        for invoice in queryset:
            payment = Payment.objects.create(
                invoice=invoice,
                provider=Payment.Provider.MANUAL,
                status=Payment.Status.PENDING,
                amount=invoice.amount,
            )
            mark_payment_succeeded(payment, {"source": "django_admin_manual"}, signature_valid=True)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "client_email", "status", "invoice", "created_at")
    list_filter = ("status", "created_at", "due_at")
    search_fields = ("title", "client_name", "client_email", "scope", "internal_notes")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("lead", "user", "invoice")
    actions = ("mark_in_progress", "mark_waiting_payment", "mark_completed")

    @admin.action(description="Перевести в работу")
    def mark_in_progress(self, request, queryset):
        queryset.update(status=Order.Status.IN_PROGRESS)

    @admin.action(description="Ожидает оплату")
    def mark_waiting_payment(self, request, queryset):
        queryset.update(status=Order.Status.WAITING_PAYMENT)

    @admin.action(description="Завершить")
    def mark_completed(self, request, queryset):
        queryset.update(status=Order.Status.COMPLETED)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "invoice",
        "provider",
        "status",
        "amount",
        "provider_state_code",
        "last_reconciled_at",
        "created_at",
        "paid_at",
    )
    list_filter = ("provider", "status", "signature_valid", "created_at")
    search_fields = ("invoice__title", "invoice__client_email")
    readonly_fields = (
        "provider_operation_id",
        "provider_state_code",
        "last_reconciled_at",
        "reconciliation_note",
        "created_at",
        "paid_at",
        "provider_payload",
    )


@admin.register(ManagedSite)
class ManagedSiteAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "domain_name",
        "user",
        "status",
        "support_plan",
        "domain_expires_at",
        "hosting_expires_at",
        "ssl_expires_at",
    )
    list_filter = ("status", "support_plan", "ssl_auto_renew", "analytics_connected", "seo_baseline_ready")
    search_fields = ("title", "domain_name", "url", "user__username", "user__email", "notes")
    autocomplete_fields = ("user", "lead", "order")
    readonly_fields = ("created_at", "updated_at")
