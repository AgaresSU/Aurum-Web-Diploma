from django.contrib import admin

from .models import ClientRequest


@admin.register(ClientRequest)
class ClientRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "client", "service_type", "status", "created_at")
    list_filter = ("status", "service_type")
    search_fields = ("title", "client__username", "client__email", "summary")
