from django.contrib import admin

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 1
    fields = ("author_role", "author", "body", "is_internal", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "client_email", "status", "lead", "updated_at")
    list_filter = ("status", "created_at", "updated_at")
    search_fields = ("title", "client_email", "lead__email", "messages__body")
    readonly_fields = ("public_token", "created_at", "updated_at")
    inlines = (MessageInline,)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "author_role", "is_internal", "created_at")
    list_filter = ("author_role", "is_internal", "created_at")
    search_fields = ("body", "conversation__title", "conversation__client_email")
