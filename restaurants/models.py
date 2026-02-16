from django.db import models
from django.contrib.auth import get_user_model
import secrets


def generate_invite_code():
    return secrets.token_urlsafe(6)


class Restaurant(models.Model):
    name = models.CharField(max_length=255, unique=True)
    place_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    types = models.JSONField(default=list, blank=True)
    rating = models.FloatField(null=True, blank=True)
    user_ratings_total = models.PositiveIntegerField(null=True, blank=True)
    price_level = models.PositiveSmallIntegerField(null=True, blank=True)
    business_status = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class DiningSession(models.Model):
    name = models.CharField(max_length=255)
    proposed_time = models.DateTimeField()
    created_by = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    invite_code = models.CharField(max_length=32, unique=True, default=generate_invite_code)
    created_at = models.DateTimeField(auto_now_add=True)
    participants = models.ManyToManyField(
        get_user_model(),
        through="SessionParticipant",
        related_name="dining_sessions",
    )

    class Meta:
        ordering = ["-proposed_time", "-created_at"]

    def __str__(self):
        return f"{self.name} ({self.proposed_time:%Y-%m-%d %H:%M})"


class SessionParticipant(models.Model):
    session = models.ForeignKey(DiningSession, on_delete=models.CASCADE)
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "user"],
                name="unique_session_participant",
            ),
        ]

    def __str__(self):
        return f"{self.user} in {self.session}"


class SwipeDecision(models.Model):
    APPROVE = "approve"
    DISAPPROVE = "disapprove"
    DECISION_CHOICES = [
        (APPROVE, "Approve"),
        (DISAPPROVE, "Disapprove"),
    ]

    session = models.ForeignKey(DiningSession, on_delete=models.CASCADE)
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    decision = models.CharField(max_length=12, choices=DECISION_CHOICES)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "user", "restaurant"],
                name="unique_session_user_restaurant_swipe",
            ),
        ]

    def __str__(self):
        return f"{self.session}: {self.user} -> {self.restaurant} ({self.decision})"
