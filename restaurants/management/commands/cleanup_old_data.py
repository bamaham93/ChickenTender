from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from restaurants.models import DiningSession, Restaurant, SwipeDecision


class Command(BaseCommand):
    help = "Delete sessions, restaurants, and swipe decisions older than N days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=60,
            help="Delete records older than this many days (default: 60).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without making any changes.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        old_decisions = SwipeDecision.objects.filter(updated_at__lt=cutoff)
        old_sessions = DiningSession.objects.filter(created_at__lt=cutoff)
        old_restaurants = Restaurant.objects.filter(created_at__lt=cutoff)

        decision_count = old_decisions.count()
        session_count = old_sessions.count()
        restaurant_count = old_restaurants.count()

        self.stdout.write(
            f"Cutoff: {cutoff.isoformat()} | decisions={decision_count}, "
            f"sessions={session_count}, restaurants={restaurant_count}"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run only. No records were deleted."))
            return

        deleted_decisions, _ = old_decisions.delete()
        deleted_sessions, _ = old_sessions.delete()
        deleted_restaurants, _ = old_restaurants.delete()

        self.stdout.write(
            self.style.SUCCESS(
                "Deleted records | "
                f"decisions={deleted_decisions}, sessions={deleted_sessions}, "
                f"restaurants={deleted_restaurants}"
            )
        )
