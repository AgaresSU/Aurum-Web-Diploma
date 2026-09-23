from django.contrib import admin

from .models import Service, Work


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('title', 'sort_order', 'is_published')
    list_editable = ('sort_order', 'is_published')
    prepopulated_fields = {'slug': ('title',)}


@admin.register(Work)
class WorkAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'is_published', 'created_at')
    list_filter = ('category', 'is_published')
    search_fields = ('title', 'short_description', 'description')
    prepopulated_fields = {'slug': ('title',)}

# Register your models here.
