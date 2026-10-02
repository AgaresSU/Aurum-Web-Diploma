from django.contrib import admin

from .models import IntegrationEvent, OutboundTask, QuickConsultationBotSettings, RobokassaSettings, TelegramBotSettings


@admin.register(IntegrationEvent)
class IntegrationEventAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "status", "title", "created_at")
    list_filter = ("provider", "status", "created_at")
    search_fields = ("title",)
    readonly_fields = ("provider", "status", "title", "payload", "created_at")


@admin.register(OutboundTask)
class OutboundTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "task_type", "status", "attempts", "available_at", "created_at")
    list_filter = ("task_type", "status", "created_at")
    search_fields = ("deduplication_key", "last_error")
    readonly_fields = (
        "task_type",
        "payload",
        "deduplication_key",
        "status",
        "attempts",
        "available_at",
        "locked_at",
        "completed_at",
        "last_error",
        "created_at",
        "updated_at",
    )


@admin.register(TelegramBotSettings)
class TelegramBotSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "is_enabled", "token_saved", "admin_chat_id", "webhook_url", "last_update_id", "updated_at")
    readonly_fields = ("token_saved", "webhook_secret_saved", "created_at", "updated_at")
    fields = (
        "is_enabled",
        "token_saved",
        "admin_chat_id",
        "webhook_secret_saved",
        "webhook_url",
        "last_update_id",
        "created_at",
        "updated_at",
    )

    @admin.display(boolean=True, description="Токен сохранен")
    def token_saved(self, obj):
        return obj.has_bot_token

    @admin.display(boolean=True, description="Webhook secret сохранен")
    def webhook_secret_saved(self, obj):
        return obj.has_webhook_secret


@admin.register(QuickConsultationBotSettings)
class QuickConsultationBotSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "is_enabled", "token_saved", "admin_chat_id", "webhook_url", "last_update_id", "updated_at")
    readonly_fields = ("token_saved", "webhook_secret_saved", "created_at", "updated_at")
    fields = (
        "is_enabled",
        "token_saved",
        "admin_chat_id",
        "webhook_secret_saved",
        "webhook_url",
        "last_update_id",
        "created_at",
        "updated_at",
    )

    @admin.display(boolean=True, description="Токен сохранен")
    def token_saved(self, obj):
        return obj.has_bot_token

    @admin.display(boolean=True, description="Webhook secret сохранен")
    def webhook_secret_saved(self, obj):
        return obj.has_webhook_secret


@admin.register(RobokassaSettings)
class RobokassaSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "is_enabled",
        "merchant_login",
        "password1_saved",
        "password2_saved",
        "test_mode",
        "receipt_enabled",
        "updated_at",
    )
    readonly_fields = ("password1_saved", "password2_saved", "created_at", "updated_at")
    fields = (
        "is_enabled",
        "merchant_login",
        "password1_saved",
        "password2_saved",
        "test_mode",
        "hash_algorithm",
        "payment_url",
        "receipt_enabled",
        "receipt_sno",
        "receipt_tax",
        "receipt_payment_method",
        "receipt_payment_object",
        "created_at",
        "updated_at",
    )

    @admin.display(boolean=True, description="Password #1 сохранен")
    def password1_saved(self, obj):
        return obj.has_password1

    @admin.display(boolean=True, description="Password #2 сохранен")
    def password2_saved(self, obj):
        return obj.has_password2
