from django.contrib import admin

from .models import DiningSession, Restaurant, SessionParticipant, SwipeDecision


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "place_id", "rating", "created_at")
    search_fields = ("name", "place_id")
    ordering = ("name",)


@admin.register(SwipeDecision)
class SwipeDecisionAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "user", "restaurant", "decision", "updated_at")
    list_filter = ("decision", "session", "updated_at")
    search_fields = ("session__name", "user__username", "restaurant__name")
    ordering = ("-updated_at",)


@admin.register(DiningSession)
class DiningSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "proposed_time", "created_by", "invite_code")
    list_filter = ("proposed_time",)
    search_fields = ("name", "invite_code", "created_by__username")
    ordering = ("-proposed_time",)


@admin.register(SessionParticipant)
class SessionParticipantAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "user", "joined_at")
    list_filter = ("joined_at",)
    search_fields = ("session__name", "user__username")
    ordering = ("-joined_at",)
