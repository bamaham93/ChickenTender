from datetime import timedelta
from io import BytesIO
import os
import unittest
from urllib.error import HTTPError
from urllib.error import URLError
from unittest.mock import patch

import django
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.test import override_settings
from django.test.utils import setup_test_environment
from django.urls import reverse
from django.utils import timezone

# Allow VS Code unittest discovery to import Django tests directly.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chickentender.settings")
django.setup()
if "testserver" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]
try:
    setup_test_environment()
except RuntimeError:
    # Django's test runner already initialized the test environment.
    pass

from .models import DiningSession, Restaurant, SessionParticipant, SwipeDecision


class SessionVisibilityTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user(
            username="alice", password="test-pass-123"
        )
        self.bob = user_model.objects.create_user(
            username="bob", password="test-pass-123"
        )

        base_time = timezone.now() + timedelta(days=1)

        self.alice_created_session = DiningSession.objects.create(
            name="Alice Created",
            proposed_time=base_time,
            created_by=self.alice,
        )
        SessionParticipant.objects.create(
            session=self.alice_created_session,
            user=self.alice,
        )

        self.invited_session = DiningSession.objects.create(
            name="Invited Session",
            proposed_time=base_time + timedelta(hours=1),
            created_by=self.bob,
        )
        SessionParticipant.objects.create(session=self.invited_session, user=self.bob)
        SessionParticipant.objects.create(session=self.invited_session, user=self.alice)

        self.hidden_session = DiningSession.objects.create(
            name="Hidden Session",
            proposed_time=base_time + timedelta(hours=2),
            created_by=self.bob,
        )
        SessionParticipant.objects.create(session=self.hidden_session, user=self.bob)

        self.restaurant = Restaurant.objects.create(name="Test Chicken Place")

    def test_index_shows_only_created_or_joined_sessions(self):
        self.client.login(username="alice", password="test-pass-123")

        response = self.client.get(reverse("restaurants:index"))

        self.assertEqual(response.status_code, 200)
        visible_ids = {row["id"] for row in response.context["sessions"]}
        created_ids = {row["id"] for row in response.context["created_sessions"]}

        self.assertIn(self.alice_created_session.id, visible_ids)
        self.assertIn(self.invited_session.id, visible_ids)
        self.assertNotIn(self.hidden_session.id, visible_ids)

        self.assertIn(self.alice_created_session.id, created_ids)
        self.assertNotIn(self.invited_session.id, created_ids)
        self.assertNotIn(self.hidden_session.id, created_ids)

    def test_non_participant_cannot_access_session_results(self):
        self.client.login(username="alice", password="test-pass-123")

        response = self.client.get(
            reverse("restaurants:session_results", args=[self.hidden_session.id])
        )

        self.assertRedirects(response, reverse("restaurants:index"))

    def test_non_participant_cannot_access_restaurant_detail(self):
        self.client.login(username="alice", password="test-pass-123")

        response = self.client.get(
            reverse(
                "restaurants:restaurant_detail",
                args=[self.hidden_session.id, self.restaurant.id],
            )
        )

        self.assertRedirects(response, reverse("restaurants:index"))

    def test_index_requires_authentication(self):
        response = self.client.get(reverse("restaurants:index"))
        self.assertRedirects(response, "/accounts/login/?next=/")

    def test_session_results_requires_authentication(self):
        response = self.client.get(
            reverse("restaurants:session_results", args=[self.alice_created_session.id])
        )
        self.assertRedirects(
            response,
            f"/accounts/login/?next=/sessions/{self.alice_created_session.id}/results/",
        )

    def test_restaurant_detail_requires_authentication(self):
        response = self.client.get(
            reverse(
                "restaurants:restaurant_detail",
                args=[self.alice_created_session.id, self.restaurant.id],
            )
        )
        self.assertRedirects(
            response,
            f"/accounts/login/?next=/sessions/{self.alice_created_session.id}/restaurants/{self.restaurant.id}/",
        )

    def test_create_session_requires_authentication(self):
        response = self.client.post(
            reverse("restaurants:create_session"),
            data={"name": "Blocked"},
        )
        self.assertRedirects(response, "/accounts/login/?next=/")

    def test_join_session_requires_authentication(self):
        response = self.client.post(
            reverse("restaurants:join_session"),
            data={"invite_code": self.alice_created_session.invite_code},
        )
        self.assertRedirects(response, "/accounts/login/?next=/")

    def test_swipe_restaurant_requires_authentication(self):
        response = self.client.post(
            reverse(
                "restaurants:swipe_restaurant",
                args=[self.alice_created_session.id, self.restaurant.id],
            ),
            data={"decision": "approve"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "Authentication required.")


@override_settings(GOOGLE_MAPS_API_KEY="test-google-api-key")
class LocationRestaurantSearchTests(TestCase):
    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="location-user", password="test-pass-123"
        )
        self.client.login(username="location-user", password="test-pass-123")

    def test_index_clear_location_query_param_resets_location_session_state(self):
        session = self.client.session
        session["location_restaurant_ids"] = [1, 2, 3]
        session["location_label"] = "60614"
        session["location_restaurant_addresses"] = {"1": "123 Main St"}
        session.save()

        response = self.client.get(reverse("restaurants:index"), {"clear_location": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["location_label"], "")
        updated = self.client.session
        self.assertNotIn("location_restaurant_ids", updated)
        self.assertNotIn("location_label", updated)
        self.assertNotIn("location_restaurant_addresses", updated)

    @patch("restaurants.views._search_google_places_restaurants")
    def test_text_location_search_returns_restaurants(self, places_mock):
        places_mock.return_value = [
            {
                "name": "Chicken Planet",
                "formatted_address": "100 Main St, Chicago, IL",
                "place_id": "abc123",
                "types": ["restaurant", "food", "point_of_interest"],
                "rating": 4.6,
                "user_ratings_total": 812,
                "price_level": 2,
                "business_status": "OPERATIONAL",
            },
            {
                "name": "Wing City",
                "formatted_address": "200 State St, Chicago, IL",
                "place_id": "xyz789",
                "types": ["restaurant", "meal_takeaway"],
                "rating": 4.2,
                "user_ratings_total": 103,
                "price_level": 1,
                "business_status": "OPERATIONAL",
            },
        ]

        response = self.client.post(
            reverse("restaurants:search_restaurants_by_location"),
            data={
                "mode": "query",
                "query": "Chicago",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["location_label"], "Chicago")
        self.assertEqual(len(payload["restaurants"]), 2)
        self.assertEqual(
            sorted([row["name"] for row in payload["restaurants"]]),
            ["Chicken Planet", "Wing City"],
        )
        self.assertIn("formatted_address", payload["restaurants"][0])
        saved = Restaurant.objects.get(name="Chicken Planet")
        self.assertEqual(saved.place_id, "abc123")
        self.assertEqual(saved.types, ["restaurant", "food", "point_of_interest"])
        self.assertEqual(saved.rating, 4.6)
        self.assertEqual(saved.user_ratings_total, 812)
        self.assertEqual(saved.price_level, 2)
        self.assertEqual(saved.business_status, "OPERATIONAL")
        places_mock.assert_called_once_with(
            "restaurants in Chicago",
            latitude=None,
            longitude=None,
        )

    @patch("restaurants.views._search_google_places_restaurants")
    def test_device_location_search_with_invalid_coordinates_fails(self, places_mock):
        response = self.client.post(
            reverse("restaurants:search_restaurants_by_location"),
            data={
                "mode": "device",
                "latitude": 200,
                "longitude": -87.62,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("out of range", response.json()["error"])
        places_mock.assert_not_called()

    def test_location_search_requires_valid_mode(self):
        response = self.client.post(
            reverse("restaurants:search_restaurants_by_location"),
            data={"mode": "something-else"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Mode must be query or device.")

    @patch("restaurants.views._search_google_places_restaurants")
    def test_location_search_surfaces_upstream_error_reason(self, places_mock):
        places_mock.side_effect = URLError("403 Forbidden")

        with self.assertLogs("restaurants.views", level="ERROR") as captured:
            response = self.client.post(
                reverse("restaurants:search_restaurants_by_location"),
                data={
                    "mode": "query",
                    "query": "Chicago",
                },
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("403 Forbidden", response.json()["error"])
        self.assertTrue(
            any("Location search upstream failure." in line for line in captured.output)
        )

    @patch("restaurants.views._search_google_places_restaurants")
    def test_location_search_with_no_results_clears_persisted_location_state(self, places_mock):
        places_mock.return_value = []

        session = self.client.session
        session["location_restaurant_ids"] = [9]
        session["location_label"] = "Old ZIP"
        session["location_restaurant_addresses"] = {"9": "Old Address"}
        session.save()

        response = self.client.post(
            reverse("restaurants:search_restaurants_by_location"),
            data={
                "mode": "query",
                "query": "Nowhere",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["restaurants"], [])

        updated = self.client.session
        self.assertNotIn("location_restaurant_ids", updated)
        self.assertNotIn("location_label", updated)
        self.assertNotIn("location_restaurant_addresses", updated)

    def test_location_search_requires_authentication(self):
        self.client.logout()
        response = self.client.post(
            reverse("restaurants:search_restaurants_by_location"),
            data={"mode": "query", "query": "Chicago"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "Authentication required.")

    @patch("restaurants.views._search_google_places_restaurants")
    def test_location_search_rate_limited_per_user(self, places_mock):
        places_mock.return_value = []

        first = self.client.post(
            reverse("restaurants:search_restaurants_by_location"),
            data={"mode": "query", "query": "Chicago"},
            content_type="application/json",
        )
        second = self.client.post(
            reverse("restaurants:search_restaurants_by_location"),
            data={"mode": "query", "query": "Chicago"},
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn("retry_after", second.json())

    @override_settings(GOOGLE_MAPS_API_KEY="")
    def test_location_search_logs_warning_when_google_api_key_missing(self):
        with patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": ""}):
            with self.assertLogs("restaurants.views", level="WARNING") as captured:
                response = self.client.post(
                    reverse("restaurants:search_restaurants_by_location"),
                    data={
                        "mode": "query",
                        "query": "Chicago",
                    },
                    content_type="application/json",
                )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"],
            "Google Places API key is not configured.",
        )
        self.assertTrue(
            any(
                "Location search blocked: GOOGLE_MAPS_API_KEY is missing." in line
                for line in captured.output
            )
        )

    @patch("restaurants.views._fetch_json")
    @unittest.skip("Places API (New) path is intentionally disabled for now.")
    def test_google_places_new_permission_denied_falls_back_to_legacy(self, fetch_json_mock):
        from restaurants.views import _search_google_places_restaurants

        denied_payload = (
            b'{"error":{"status":"PERMISSION_DENIED",'
            b'"message":"Requests to this API places.googleapis.com method '
            b'google.maps.places.v1.Places.SearchText are blocked."}}'
        )
        fetch_json_mock.side_effect = [
            HTTPError(
                url="https://places.googleapis.com/v1/places:searchText",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=BytesIO(denied_payload),
            ),
            {
                "status": "OK",
                "results": [
                    {
                        "name": "Fallback Chicken",
                        "formatted_address": "123 Legacy Ln",
                    }
                ],
            },
        ]

        places = _search_google_places_restaurants("restaurants in Chicago")
        self.assertEqual(
            places,
            [{"name": "Fallback Chicken", "formatted_address": "123 Legacy Ln"}],
        )


@override_settings(GOOGLE_MAPS_API_KEY="test-google-api-key")
class RestaurantPlaceDetailsTests(TestCase):
    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="detail-user", password="test-pass-123"
        )
        self.session = DiningSession.objects.create(
            name="Detail Session",
            proposed_time=timezone.now() + timedelta(days=1),
            created_by=self.user,
        )
        SessionParticipant.objects.create(session=self.session, user=self.user)
        self.restaurant = Restaurant.objects.create(
            name="Place Detail Chicken",
            place_id="abc-place-id-123",
        )

    @patch("restaurants.views._fetch_google_place_details_legacy")
    def test_restaurant_detail_fetches_place_details_when_place_id_exists(self, details_mock):
        details_mock.return_value = {
            "name": "Place Detail Chicken",
            "place_id": "abc-place-id-123",
            "formatted_address": "123 Main St",
            "rating": 4.8,
            "user_ratings_total": 90,
            "price_level": 2,
            "business_status": "OPERATIONAL",
            "types": ["restaurant"],
            "formatted_phone_number": "(555) 111-2222",
            "international_phone_number": "+1 555-111-2222",
            "website": "https://example.com",
            "maps_url": "https://maps.google.com/?cid=123",
            "weekday_hours": ["Monday: 9:00 AM - 9:00 PM"],
            "latitude": 41.88,
            "longitude": -87.63,
        }

        self.client.login(username="detail-user", password="test-pass-123")
        response = self.client.get(
            reverse(
                "restaurants:restaurant_detail",
                args=[self.session.id, self.restaurant.id],
            )
        )

        self.assertEqual(response.status_code, 200)
        details_mock.assert_called_once_with("abc-place-id-123")
        self.assertEqual(response.context["place_details"]["place_id"], "abc-place-id-123")
        self.assertEqual(response.context["place_details_error"], "")

    @patch("restaurants.views._fetch_google_place_details_legacy")
    def test_restaurant_detail_shows_nonfatal_error_when_place_details_lookup_fails(self, details_mock):
        details_mock.side_effect = URLError("upstream timeout")

        self.client.login(username="detail-user", password="test-pass-123")
        response = self.client.get(
            reverse(
                "restaurants:restaurant_detail",
                args=[self.session.id, self.restaurant.id],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["place_details"]["place_id"], "abc-place-id-123")
        self.assertIn("upstream timeout", response.context["place_details_error"])

    @patch("restaurants.views._fetch_google_place_details_legacy")
    def test_restaurant_api_details_returns_live_details(self, details_mock):
        details_mock.return_value = {
            "name": "Place Detail Chicken",
            "place_id": "abc-place-id-123",
            "formatted_address": "123 Main St",
            "rating": 4.8,
            "user_ratings_total": 90,
            "price_level": 2,
            "business_status": "OPERATIONAL",
            "types": ["restaurant"],
            "formatted_phone_number": "(555) 111-2222",
            "international_phone_number": "+1 555-111-2222",
            "website": "https://example.com",
            "maps_url": "https://maps.google.com/?cid=123",
            "weekday_hours": ["Monday: 9:00 AM - 9:00 PM"],
            "latitude": 41.88,
            "longitude": -87.63,
        }

        self.client.login(username="detail-user", password="test-pass-123")
        response = self.client.get(
            reverse("restaurants:restaurant_api_details", args=[self.restaurant.id])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["details"]["place_id"], "abc-place-id-123")
        self.assertEqual(payload["restaurant"]["rating"], 4.8)
        self.assertEqual(payload["warning"], "")
        details_mock.assert_called_once_with("abc-place-id-123")

    @patch("restaurants.views._fetch_google_place_details_legacy")
    def test_restaurant_api_details_returns_warning_on_lookup_failure(self, details_mock):
        details_mock.side_effect = URLError("upstream timeout")

        self.client.login(username="detail-user", password="test-pass-123")
        response = self.client.get(
            reverse("restaurants:restaurant_api_details", args=[self.restaurant.id])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["details"]["place_id"], "abc-place-id-123")
        self.assertIn("upstream timeout", payload["warning"])

    def test_restaurant_api_details_requires_authentication(self):
        response = self.client.get(
            reverse("restaurants:restaurant_api_details", args=[self.restaurant.id])
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "Authentication required.")

    @patch("restaurants.views._fetch_google_place_details_legacy")
    def test_restaurant_api_details_rate_limited_per_user(self, details_mock):
        details_mock.return_value = {
            "name": "Place Detail Chicken",
            "place_id": "abc-place-id-123",
            "formatted_address": "123 Main St",
            "rating": 4.8,
            "user_ratings_total": 90,
            "price_level": 2,
            "business_status": "OPERATIONAL",
            "types": ["restaurant"],
            "formatted_phone_number": "(555) 111-2222",
            "international_phone_number": "+1 555-111-2222",
            "website": "https://example.com",
            "maps_url": "https://maps.google.com/?cid=123",
            "weekday_hours": ["Monday: 9:00 AM - 9:00 PM"],
            "latitude": 41.88,
            "longitude": -87.63,
        }
        self.client.login(username="detail-user", password="test-pass-123")

        first = self.client.get(
            reverse("restaurants:restaurant_api_details", args=[self.restaurant.id])
        )
        second = self.client.get(
            reverse("restaurants:restaurant_api_details", args=[self.restaurant.id])
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn("retry_after", second.json())


class CleanupOldDataCommandTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="cleanup-user", password="test-pass-123"
        )
        now = timezone.now()
        old_time = now - timedelta(days=90)
        recent_time = now - timedelta(days=10)

        self.old_session = DiningSession.objects.create(
            name="Old Session",
            proposed_time=old_time,
            created_by=self.user,
        )
        self.recent_session = DiningSession.objects.create(
            name="Recent Session",
            proposed_time=recent_time,
            created_by=self.user,
        )
        DiningSession.objects.filter(id=self.old_session.id).update(created_at=old_time)
        DiningSession.objects.filter(id=self.recent_session.id).update(created_at=recent_time)
        self.old_session.refresh_from_db()
        self.recent_session.refresh_from_db()

        self.old_restaurant = Restaurant.objects.create(name="Old Restaurant")
        self.recent_restaurant = Restaurant.objects.create(name="Recent Restaurant")
        Restaurant.objects.filter(id=self.old_restaurant.id).update(created_at=old_time)
        Restaurant.objects.filter(id=self.recent_restaurant.id).update(created_at=recent_time)
        self.old_restaurant.refresh_from_db()
        self.recent_restaurant.refresh_from_db()

        self.old_decision = SwipeDecision.objects.create(
            session=self.old_session,
            user=self.user,
            restaurant=self.recent_restaurant,
            decision=SwipeDecision.APPROVE,
        )
        self.recent_decision = SwipeDecision.objects.create(
            session=self.recent_session,
            user=self.user,
            restaurant=self.recent_restaurant,
            decision=SwipeDecision.DISAPPROVE,
        )
        SwipeDecision.objects.filter(id=self.old_decision.id).update(updated_at=old_time)
        SwipeDecision.objects.filter(id=self.recent_decision.id).update(updated_at=recent_time)

    def test_cleanup_old_data_dry_run_does_not_delete_records(self):
        call_command("cleanup_old_data", "--days", "60", "--dry-run")

        self.assertTrue(DiningSession.objects.filter(id=self.old_session.id).exists())
        self.assertTrue(Restaurant.objects.filter(id=self.old_restaurant.id).exists())
        self.assertTrue(SwipeDecision.objects.filter(id=self.old_decision.id).exists())

    def test_cleanup_old_data_deletes_only_records_older_than_cutoff(self):
        call_command("cleanup_old_data", "--days", "60")

        self.assertFalse(DiningSession.objects.filter(id=self.old_session.id).exists())
        self.assertFalse(Restaurant.objects.filter(id=self.old_restaurant.id).exists())
        self.assertFalse(SwipeDecision.objects.filter(id=self.old_decision.id).exists())

        self.assertTrue(DiningSession.objects.filter(id=self.recent_session.id).exists())
        self.assertTrue(Restaurant.objects.filter(id=self.recent_restaurant.id).exists())
        self.assertTrue(SwipeDecision.objects.filter(id=self.recent_decision.id).exists())
