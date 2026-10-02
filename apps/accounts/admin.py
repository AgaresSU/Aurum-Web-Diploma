from django.contrib import admin

from .models import BackupCode, EmailVerificationCode, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "role",
        "client_type",
        "payer_name",
        "inn",
        "phone",
        "totp_enabled",
        "requisites_complete",
        "created_at",
    )
    list_filter = ("role", "client_type", "totp_enabled", "personal_data_consent", "created_at")
    search_fields = ("user__username", "user__email", "company", "legal_name", "inn", "phone", "telegram_username")


@admin.register(BackupCode)
class BackupCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "used_at", "created_at")
    list_filter = ("used_at", "created_at")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("code_hash", "created_at", "used_at")


@admin.register(EmailVerificationCode)
class EmailVerificationCodeAdmin(admin.ModelAdmin):
    list_display = ("email", "user", "purpose", "attempts", "expires_at", "used_at", "created_at")
    list_filter = ("purpose", "used_at", "created_at", "expires_at")
    search_fields = ("email", "user__username", "user__email")
    readonly_fields = ("code_hash", "attempts", "expires_at", "used_at", "created_at")
