"""Admin registrations -- a raw window onto conversations while the Inbox UI
is the primary surface. Handy for flipping status/assignment during dev."""

from django.contrib import admin

from .models import Conversation, ConversationTag, Message, Tag


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("direction", "body", "status", "timestamp", "provider_message_id")
    readonly_fields = ("timestamp",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("contact", "channel", "status", "assigned_to",
                    "unread_count", "last_message_at")
    list_filter = ("channel", "status")
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "direction", "body", "status", "timestamp")
    list_filter = ("direction", "status")
    search_fields = ("body", "provider_message_id")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "is_archived", "created_by", "created_at")
    list_filter = ("is_archived", "color")
    search_fields = ("name",)


@admin.register(ConversationTag)
class ConversationTagAdmin(admin.ModelAdmin):
    list_display = ("conversation", "tag", "tagged_by", "tagged_at")
    list_filter = ("tag",)
