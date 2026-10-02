from django.contrib import admin

from .models import AuditEvent, ConsentAcceptance, OperationalAlert


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "object_label", "channel", "ip_address")
    list_filter = ("action", "channel", "object_type", "created_at")
    search_fields = ("object_label", "object_type", "object_id", "actor__username", "actor__email")
    readonly_fields = tuple(field.name for field in AuditEvent._meta.fields)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConsentAcceptance)
class ConsentAcceptanceAdmin(admin.ModelAdmin):
    list_display = (
        "accepted_at",
        "document_type",
        "document_scope",
        "document_version",
        "channel",
        "subject_type",
        "subject_id",
    )
    list_filter = ("document_type", "document_scope", "channel", "document_version")
    search_fields = ("subject_type", "subject_id", "user__username", "user__email", "document_checksum")
    readonly_fields = tuple(field.name for field in ConsentAcceptance._meta.fields)
    date_hierarchy = "accepted_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OperationalAlert)
class OperationalAlertAdmin(admin.ModelAdmin):
    list_display = ("last_detected_at", "severity", "title", "is_active", "last_notified_at", "resolved_at")
    list_filter = ("is_active", "severity", "last_detected_at")
    search_fields = ("code", "title", "message")
    readonly_fields = tuple(field.name for field in OperationalAlert._meta.fields)
    date_hierarchy = "last_detected_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False
