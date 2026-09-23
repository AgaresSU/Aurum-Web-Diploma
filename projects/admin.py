from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'service', 'status', 'due_at', 'updated_at')
    list_filter = ('status', 'service')
    search_fields = ('title', 'owner__username', 'summary')

# Register your models here.
