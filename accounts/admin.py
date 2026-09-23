from django.contrib import admin

from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'display_name', 'company', 'phone')
    search_fields = ('user__username', 'display_name', 'company', 'phone')

# Register your models here.
