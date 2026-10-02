from django.contrib import admin

from .models import Project, ProjectEvent, ProjectFile, ProjectStage


class ProjectStageInline(admin.TabularInline):
    model = ProjectStage
    extra = 0


class ProjectFileInline(admin.TabularInline):
    model = ProjectFile
    extra = 0
    readonly_fields = ("public_id", "original_name", "size", "created_at")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "client_email", "status", "manager", "due_at", "updated_at")
    list_filter = ("status", "service_type", "updated_at")
    search_fields = ("title", "client_name", "client_email", "summary")
    autocomplete_fields = ("user", "manager")
    readonly_fields = ("public_id", "created_at", "updated_at", "completed_at")
    inlines = (ProjectStageInline, ProjectFileInline)


@admin.register(ProjectEvent)
class ProjectEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "project", "kind", "title", "client_visible")
    list_filter = ("kind", "client_visible", "created_at")
    search_fields = ("project__title", "title", "description")
    readonly_fields = ("created_at",)
