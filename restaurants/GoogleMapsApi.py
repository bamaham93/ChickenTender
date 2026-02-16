import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from .models import DiningSession, Restaurant, SessionParticipant, SwipeDecision

GOOGLE_PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_PLACES_TEXT_SEARCH_LEGACY_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
GOOGLE_PLACE_DETAILS_LEGACY_URL = "https://maps.googleapis.com/maps/api/place/details/json"
GOOGLE_PLACES_FIELD_MASK = "places.displayName,places.formattedAddress"
GOOGLE_PLACE_DETAILS_FIELDS = ",".join(
    [
        "name",
        "formatted_address",
        "place_id",
        "geometry",
        "rating",
        "user_ratings_total",
        "types",
        "formatted_phone_number",
        "international_phone_number",
        "website",
        "url",
        "opening_hours",
        "business_status",
        "price_level",
    ]
)
HTTP_HEADERS = {"User-Agent": "ChickenTender/1.0 (Django App)"}
LOCATION_SESSION_KEY = "location_restaurant_ids"
LOCATION_LABEL_SESSION_KEY = "location_label"
LOCATION_ADDRESS_MAP_SESSION_KEY = "location_restaurant_addresses"
EXTERNAL_API_RATE_LIMIT_SECONDS = 2


def _fetch_json(url, *, data=None, headers=None):
    all_headers = dict(HTTP_HEADERS)
    if headers:
        all_headers.update(headers)
    request = Request(url, data=data, headers=all_headers)
    try:
        with urlopen(request, timeout=20) as response:
            raw_body = response.read()
    except (HTTPError, URLError):
        raise
    except Exception as exc:
        raise URLError(f"Unexpected network error: {exc}") from exc

    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise URLError("Google Maps API returned a non-JSON response.") from exc


def _rate_limit_retry_after(user_id, scope, interval_seconds=EXTERNAL_API_RATE_LIMIT_SECONDS):
    now = time.time()
    key = f"rate_limit:{scope}:user:{user_id}"
    blocked_until = cache.get(key)
    if blocked_until and blocked_until > now:
        return max(1, math.ceil(blocked_until - now))
    cache.set(key, now + interval_seconds, timeout=interval_seconds)
    return 0


def _search_google_places_restaurants(query_text, *, latitude=None, longitude=None, limit=60):
    # Places API (New) path intentionally disabled for now because SearchText is
    # blocked for the current project key. Keeping this code for later re-enable:
    #
    # request_body = {
    #     "textQuery": query_text,
    #     "maxResultCount": limit,
    # }
    # if latitude is not None and longitude is not None:
    #     request_body["locationBias"] = {
    #         "circle": {
    #             "center": {
    #                 "latitude": latitude,
    #                 "longitude": longitude,
    #             },
    #             "radius": 8000.0,
    #         }
    #     }
    #
    # payload = _fetch_json(
    #     GOOGLE_PLACES_TEXT_SEARCH_URL,
    #     data=json.dumps(request_body).encode("utf-8"),
    #     headers={
    #         "Content-Type": "application/json",
    #         "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
    #         "X-Goog-FieldMask": GOOGLE_PLACES_FIELD_MASK,
    #     },
    # )
    #
    # seen = set()
    # places = []
    # for place in payload.get("places", []):
    #     display_name = place.get("displayName") or {}
    #     if isinstance(display_name, dict):
    #         name = (display_name.get("text") or "").strip()
    #     else:
    #         name = str(display_name).strip()
    #     formatted_address = (place.get("formattedAddress") or "").strip()
    #     if not name:
    #         continue
    #     lowered = name.casefold()
    #     if lowered in seen:
    #         continue
    #     seen.add(lowered)
    #     places.append(
    #         {
    #             "name": name,
    #             "formatted_address": formatted_address,
    #         }
    #     )
    # return places

    return _search_google_places_restaurants_legacy(
        query_text,
        latitude=latitude,
        longitude=longitude,
        limit=limit,
    )


def _search_google_places_restaurants_legacy(query_text, *, latitude=None, longitude=None, limit=60):
    params = {
        "query": query_text,
        "key": settings.GOOGLE_MAPS_API_KEY,
    }
    if latitude is not None and longitude is not None:
        params["location"] = f"{latitude},{longitude}"
        params["radius"] = "8000"

    payload = _fetch_json(f"{GOOGLE_PLACES_TEXT_SEARCH_LEGACY_URL}?{urlencode(params)}")
    status = (payload.get("status") or "").strip()
    if status not in {"OK", "ZERO_RESULTS"}:
        error_message = (payload.get("error_message") or status or "Unknown legacy Places error").strip()
        raise URLError(error_message)

    seen = set()
    places = []
    for place in payload.get("results", []):
        name = (place.get("name") or "").strip()
        formatted_address = (place.get("formatted_address") or "").strip()
        place_id = (place.get("place_id") or "").strip()
        types = place.get("types") or []
        if not isinstance(types, list):
            types = []
        rating = place.get("rating")
        user_ratings_total = place.get("user_ratings_total")
        price_level = place.get("price_level")
        business_status = (place.get("business_status") or "").strip()

        try:
            rating = float(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating = None

        try:
            user_ratings_total = int(user_ratings_total) if user_ratings_total is not None else None
        except (TypeError, ValueError):
            user_ratings_total = None

        try:
            price_level = int(price_level) if price_level is not None else None
        except (TypeError, ValueError):
            price_level = None

        if not name:
            continue
        lowered = name.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        places.append(
            {
                "name": name,
                "formatted_address": formatted_address,
                "place_id": place_id,
                "types": types,
                "rating": rating,
                "user_ratings_total": user_ratings_total,
                "price_level": price_level,
                "business_status": business_status,
            }
        )
        if len(places) >= limit:
            break

    return places


def _fetch_google_place_details_legacy(place_id):
    params = {
        "place_id": place_id,
        "fields": GOOGLE_PLACE_DETAILS_FIELDS,
        "key": settings.GOOGLE_MAPS_API_KEY,
    }
    payload = _fetch_json(f"{GOOGLE_PLACE_DETAILS_LEGACY_URL}?{urlencode(params)}")
    status = (payload.get("status") or "").strip()
    if status not in {"OK", "ZERO_RESULTS"}:
        error_message = (payload.get("error_message") or status or "Unknown place details error").strip()
        raise URLError(error_message)

    result = payload.get("result") or {}
    location = ((result.get("geometry") or {}).get("location") or {})
    lat = location.get("lat")
    lng = location.get("lng")
    try:
        lat = float(lat) if lat is not None else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        lng = None

    weekday_hours = ((result.get("opening_hours") or {}).get("weekday_text") or [])
    if not isinstance(weekday_hours, list):
        weekday_hours = []
    types = result.get("types") or []
    if not isinstance(types, list):
        types = []

    return {
        "name": (result.get("name") or "").strip(),
        "place_id": (result.get("place_id") or place_id).strip(),
        "formatted_address": (result.get("formatted_address") or "").strip(),
        "rating": result.get("rating"),
        "user_ratings_total": result.get("user_ratings_total"),
        "price_level": result.get("price_level"),
        "business_status": (result.get("business_status") or "").strip(),
        "types": types,
        "formatted_phone_number": (result.get("formatted_phone_number") or "").strip(),
        "international_phone_number": (result.get("international_phone_number") or "").strip(),
        "website": (result.get("website") or "").strip(),
        "maps_url": (result.get("url") or "").strip(),
        "weekday_hours": weekday_hours,
        "latitude": lat,
        "longitude": lng,
    }


def _extract_google_error_message(error):
    if isinstance(error, HTTPError):
        try:
            body = error.read().decode("utf-8")
            payload = json.loads(body)
            upstream = payload.get("error", {})
            message = (upstream.get("message") or "").strip()
            status = (upstream.get("status") or "").strip()
            if message and status:
                return f"{status}: {message}"
            if message:
                return message
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        return f"HTTP {error.code}"

    reason = getattr(error, "reason", "")
    if reason:
        return str(reason)
    return str(error)


def _save_restaurants_to_db(places):
    entries = []
    for place in places:
        name = place["name"]
        place_id = (place.get("place_id") or "").strip()
        restaurant = None

        if place_id:
            restaurant = Restaurant.objects.filter(place_id=place_id).first()
            if not restaurant:
                restaurant = Restaurant.objects.filter(name=name).first()

        if not restaurant:
            restaurant, _ = Restaurant.objects.get_or_create(name=name)

        if restaurant.name != name:
            name_taken = Restaurant.objects.filter(name=name).exclude(id=restaurant.id).exists()
            if not name_taken:
                restaurant.name = name

        if place_id:
            restaurant.place_id = place_id
        restaurant.types = place.get("types") or []
        restaurant.rating = place.get("rating")
        restaurant.user_ratings_total = place.get("user_ratings_total")
        restaurant.price_level = place.get("price_level")
        restaurant.business_status = place.get("business_status") or ""
        restaurant.save()

        entries.append(
            {
                "id": restaurant.id,
                "name": restaurant.name,
                "formatted_address": place.get("formatted_address", ""),
                "rating": restaurant.rating,
                "price_level": restaurant.price_level,
                "user_ratings_total": restaurant.user_ratings_total,
            }
        )
    return entries
