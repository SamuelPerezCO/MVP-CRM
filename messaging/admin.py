"""Admin registrations -- a raw window onto conversations while the Inbox UI
is the primary surface. Handy for flipping status/assignment during dev."""

from django.contrib import admin

from .models import Conversation, ConversationTag, Message, Tag


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    # ``fields`` picks the columns of each inline row (the Message rows shown
    # inside a Conversation's admin page). ``template`` renders as a
    # <select> over every plantilla -- the default widget for a ForeignKey --
    # and ``billed_amount`` as a number input (``<input type="number"
    # step="0.000001">``) Django validates as a Decimal.
    # Neither is in ``readonly_fields``, so they can be corrected by hand
    # here; nothing in the app edits them once ``send_template`` has
    # returned.
    fields = ("direction", "body", "status", "timestamp", "provider_message_id",
              "template", "billed_amount")
    readonly_fields = ("timestamp",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("contact", "channel", "status", "assigned_to",
                    "unread_count", "last_message_at")
    list_filter = ("channel", "status")
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    # The billing columns are here because this is the only place that lists a
    # month's template spend as one sortable, filterable table -- the app
    # shows a per-message amount on the bubble and a monthly total, but never
    # the rows side by side.
    #
    # ``list_display`` is the column list of the changelist (the table at
    # /admin/messaging/message/). For the ``template`` column Django prints
    # ``str(message.template)`` -- MessageTemplate.__str__ returns the
    # plantilla's name -- under the header "Plantilla" (the field's
    # verbose_name), and "-" when the FK is NULL. Because ``conversation``
    # and ``template`` are ForeignKeys, the changelist adds
    # ``select_related`` for them on its own, so a page of rows is one JOIN
    # query rather than one extra query per row. ``billed_amount`` is a real
    # column, so clicking its header sorts by it; rows that were never
    # billed show "-" there too.
    list_display = ("conversation", "direction", "body", "status", "timestamp",
                    "template", "billed_amount")
    # Each entry becomes a filter box in the right-hand sidebar.
    # ``billed_category`` has no ``choices``, so Django uses its
    # AllValuesFieldListFilter: the box lists every distinct value stored in
    # the column (marketing/utility/authentication, plus a blank entry for
    # the unbilled rows) and narrows the table to one of them.
    list_filter = ("direction", "status", "billed_category")
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
