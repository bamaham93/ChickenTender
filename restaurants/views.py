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
from .GoogleMapsApi import _fetch_json, _rate_limit_retry_after, _search_google_places_restaurants, _fetch_google_place_details_legacy, _extract_google_error_message, _save_restaurants_to_db


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


# def _fetch_json(url, *, data=None, headers=None):
#     all_headers = dict(HTTP_HEADERS)
#     if headers:
#         all_headers.update(headers)
#     request = Request(url, data=data, headers=all_headers)
#     with urlopen(request, timeout=20) as response:
#         return json.loads(response.read().decode("utf-8"))


# def _rate_limit_retry_after(user_id, scope, interval_seconds=EXTERNAL_API_RATE_LIMIT_SECONDS):
#     now = time.time()
#     key = f"rate_limit:{scope}:user:{user_id}"
#     blocked_until = cache.get(key)
#     if blocked_until and blocked_until > now:
#         return max(1, math.ceil(blocked_until - now))
#     cache.set(key, now + interval_seconds, timeout=interval_seconds)
#     return 0


# def _search_google_places_restaurants(query_text, *, latitude=None, longitude=None, limit=60):
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

#     return _search_google_places_restaurants_legacy(
#         query_text,
#         latitude=latitude,
#         longitude=longitude,
#         limit=limit,
#     )


# def _search_google_places_restaurants_legacy(query_text, *, latitude=None, longitude=None, limit=60):
#     params = {
#         "query": query_text,
#         "key": settings.GOOGLE_MAPS_API_KEY,
#     }
#     if latitude is not None and longitude is not None:
#         params["location"] = f"{latitude},{longitude}"
#         params["radius"] = "8000"

#     payload = _fetch_json(f"{GOOGLE_PLACES_TEXT_SEARCH_LEGACY_URL}?{urlencode(params)}")
#     status = (payload.get("status") or "").strip()
#     if status not in {"OK", "ZERO_RESULTS"}:
#         error_message = (payload.get("error_message") or status or "Unknown legacy Places error").strip()
#         raise URLError(error_message)

#     seen = set()
#     places = []
#     for place in payload.get("results", []):
#         name = (place.get("name") or "").strip()
#         formatted_address = (place.get("formatted_address") or "").strip()
#         place_id = (place.get("place_id") or "").strip()
#         types = place.get("types") or []
#         if not isinstance(types, list):
#             types = []
#         rating = place.get("rating")
#         user_ratings_total = place.get("user_ratings_total")
#         price_level = place.get("price_level")
#         business_status = (place.get("business_status") or "").strip()

#         try:
#             rating = float(rating) if rating is not None else None
#         except (TypeError, ValueError):
#             rating = None

#         try:
#             user_ratings_total = int(user_ratings_total) if user_ratings_total is not None else None
#         except (TypeError, ValueError):
#             user_ratings_total = None

#         try:
#             price_level = int(price_level) if price_level is not None else None
#         except (TypeError, ValueError):
#             price_level = None

#         if not name:
#             continue
#         lowered = name.casefold()
#         if lowered in seen:
#             continue
#         seen.add(lowered)
#         places.append(
#             {
#                 "name": name,
#                 "formatted_address": formatted_address,
#                 "place_id": place_id,
#                 "types": types,
#                 "rating": rating,
#                 "user_ratings_total": user_ratings_total,
#                 "price_level": price_level,
#                 "business_status": business_status,
#             }
#         )
#         if len(places) >= limit:
#             break

#     return places


# def _fetch_google_place_details_legacy(place_id):
#     params = {
#         "place_id": place_id,
#         "fields": GOOGLE_PLACE_DETAILS_FIELDS,
#         "key": settings.GOOGLE_MAPS_API_KEY,
#     }
#     payload = _fetch_json(f"{GOOGLE_PLACE_DETAILS_LEGACY_URL}?{urlencode(params)}")
#     status = (payload.get("status") or "").strip()
#     if status not in {"OK", "ZERO_RESULTS"}:
#         error_message = (payload.get("error_message") or status or "Unknown place details error").strip()
#         raise URLError(error_message)

#     result = payload.get("result") or {}
#     location = ((result.get("geometry") or {}).get("location") or {})
#     lat = location.get("lat")
#     lng = location.get("lng")
#     try:
#         lat = float(lat) if lat is not None else None
#     except (TypeError, ValueError):
#         lat = None
#     try:
#         lng = float(lng) if lng is not None else None
#     except (TypeError, ValueError):
#         lng = None

#     weekday_hours = ((result.get("opening_hours") or {}).get("weekday_text") or [])
#     if not isinstance(weekday_hours, list):
#         weekday_hours = []
#     types = result.get("types") or []
#     if not isinstance(types, list):
#         types = []

#     return {
#         "name": (result.get("name") or "").strip(),
#         "place_id": (result.get("place_id") or place_id).strip(),
#         "formatted_address": (result.get("formatted_address") or "").strip(),
#         "rating": result.get("rating"),
#         "user_ratings_total": result.get("user_ratings_total"),
#         "price_level": result.get("price_level"),
#         "business_status": (result.get("business_status") or "").strip(),
#         "types": types,
#         "formatted_phone_number": (result.get("formatted_phone_number") or "").strip(),
#         "international_phone_number": (result.get("international_phone_number") or "").strip(),
#         "website": (result.get("website") or "").strip(),
#         "maps_url": (result.get("url") or "").strip(),
#         "weekday_hours": weekday_hours,
#         "latitude": lat,
#         "longitude": lng,
#     }


# def _extract_google_error_message(error):
#     if isinstance(error, HTTPError):
#         try:
#             body = error.read().decode("utf-8")
#             payload = json.loads(body)
#             upstream = payload.get("error", {})
#             message = (upstream.get("message") or "").strip()
#             status = (upstream.get("status") or "").strip()
#             if message and status:
#                 return f"{status}: {message}"
#             if message:
#                 return message
#         except (UnicodeDecodeError, json.JSONDecodeError):
#             pass
#         return f"HTTP {error.code}"

#     reason = getattr(error, "reason", "")
#     if reason:
#         return str(reason)
#     return str(error)


# def _save_restaurants_to_db(places):
#     entries = []
#     for place in places:
#         name = place["name"]
#         place_id = (place.get("place_id") or "").strip()
#         restaurant = None

#         if place_id:
#             restaurant = Restaurant.objects.filter(place_id=place_id).first()
#             if not restaurant:
#                 restaurant = Restaurant.objects.filter(name=name).first()

#         if not restaurant:
#             restaurant, _ = Restaurant.objects.get_or_create(name=name)

#         if restaurant.name != name:
#             name_taken = Restaurant.objects.filter(name=name).exclude(id=restaurant.id).exists()
#             if not name_taken:
#                 restaurant.name = name

#         if place_id:
#             restaurant.place_id = place_id
#         restaurant.types = place.get("types") or []
#         restaurant.rating = place.get("rating")
#         restaurant.user_ratings_total = place.get("user_ratings_total")
#         restaurant.price_level = place.get("price_level")
#         restaurant.business_status = place.get("business_status") or ""
#         restaurant.save()

#         entries.append(
#             {
#                 "id": restaurant.id,
#                 "name": restaurant.name,
#                 "formatted_address": place.get("formatted_address", ""),
#                 "rating": restaurant.rating,
#                 "price_level": restaurant.price_level,
#                 "user_ratings_total": restaurant.user_ratings_total,
#             }
#         )
#     return entries


def index(request):
    if not request.user.is_authenticated:
        next_url = quote(request.get_full_path(), safe="/?=&")
        return redirect(f"/accounts/login/?next={next_url}")

    starter_restaurants = ["Chick-fil-A", "Raising Cane's", "Popeyes"]
    for name in starter_restaurants:
        Restaurant.objects.get_or_create(name=name)

    if request.GET.get("clear_location") == "1":
        request.session.pop(LOCATION_SESSION_KEY, None)
        request.session.pop(LOCATION_LABEL_SESSION_KEY, None)
        request.session.pop(LOCATION_ADDRESS_MAP_SESSION_KEY, None)

    selected_session = None
    sessions = []
    created_sessions = []
    location_restaurant_ids = request.session.get(LOCATION_SESSION_KEY) or []
    location_address_map = request.session.get(LOCATION_ADDRESS_MAP_SESSION_KEY) or {}
    location_label = request.session.get(LOCATION_LABEL_SESSION_KEY) or ""
    if location_restaurant_ids:
        restaurants_query = Restaurant.objects.filter(id__in=location_restaurant_ids)
    else:
        restaurants_query = Restaurant.objects.filter(name__in=starter_restaurants)
    invite_code = request.GET.get("invite", "").strip()

    invited_session = None
    if invite_code:
        invited_session = DiningSession.objects.filter(invite_code=invite_code).first()
        if invited_session:
            SessionParticipant.objects.get_or_create(
                session=invited_session,
                user=request.user,
            )

    user_sessions = DiningSession.objects.filter(participants=request.user).distinct()
    sessions = list(
        user_sessions.values("id", "name", "proposed_time", "invite_code")
    )
    created_sessions = list(
        DiningSession.objects.filter(created_by=request.user).values(
            "id", "name", "proposed_time", "invite_code"
        )
    )
    for session in created_sessions:
        session["share_url"] = request.build_absolute_uri(
            f"/?invite={session['invite_code']}"
        )

    session_id = request.GET.get("session")
    if session_id:
        selected_session = user_sessions.filter(id=session_id).first()
    if not selected_session and invited_session:
        selected_session = user_sessions.filter(id=invited_session.id).first()
    if not selected_session:
        selected_session = user_sessions.first()

    if selected_session:
        swiped_restaurant_ids = SwipeDecision.objects.filter(
            session=selected_session,
            user=request.user,
        ).values_list("restaurant_id", flat=True)
        restaurants_query = restaurants_query.exclude(id__in=swiped_restaurant_ids)

    restaurants = list(
        restaurants_query.values(
            "id",
            "name",
            "rating",
            "price_level",
            "user_ratings_total",
        )
    )
    for restaurant in restaurants:
        restaurant["formatted_address"] = location_address_map.get(str(restaurant["id"]), "")
    return render(
        request,
        "restaurants/index.html",
        {
            "restaurants": restaurants,
            "sessions": sessions,
            "created_sessions": created_sessions,
            "selected_session": selected_session,
            "location_label": location_label,
        },
    )



def signup(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('restaurants:index')
    else:
        form = UserCreationForm()

    return render(request, 'registration/signup.html', {'form': form})


def session_results(request, session_id):
    if not request.user.is_authenticated:
        next_url = quote(request.get_full_path(), safe="/?=&")
        return redirect(f"/accounts/login/?next={next_url}")

    session = get_object_or_404(DiningSession, id=session_id)
    is_participant = SessionParticipant.objects.filter(
        session=session, user=request.user
    ).exists()
    if not is_participant:
        return redirect("restaurants:index")

    participants = session.participants.order_by("username")
    participant_count = participants.count()

    restaurants = (
        Restaurant.objects.filter(
            swipedecision__session=session,
            swipedecision__decision=SwipeDecision.APPROVE,
        )
        .annotate(
            approver_count=Count(
                "swipedecision__user",
                filter=Q(
                    swipedecision__session=session,
                    swipedecision__decision=SwipeDecision.APPROVE,
                ),
                distinct=True,
            )
        )
        .order_by("-approver_count", "name")
    )

    return render(
        request,
        "restaurants/session_results.html",
        {
            "session": session,
            "participants": participants,
            "restaurants": restaurants,
        },
    )


def restaurant_detail(request, session_id, restaurant_id):
    if not request.user.is_authenticated:
        next_url = quote(request.get_full_path(), safe="/?=&")
        return redirect(f"/accounts/login/?next={next_url}")

    session = get_object_or_404(DiningSession, id=session_id)
    is_participant = SessionParticipant.objects.filter(
        session=session, user=request.user
    ).exists()
    if not is_participant:
        return redirect("restaurants:index")

    restaurant = get_object_or_404(Restaurant, id=restaurant_id)
    place_details = {
        "name": restaurant.name,
        "place_id": restaurant.place_id or "",
        "formatted_address": "",
        "rating": restaurant.rating,
        "user_ratings_total": restaurant.user_ratings_total,
        "price_level": restaurant.price_level,
        "business_status": restaurant.business_status or "",
        "types": restaurant.types or [],
        "formatted_phone_number": "",
        "international_phone_number": "",
        "website": "",
        "maps_url": "",
        "weekday_hours": [],
        "latitude": None,
        "longitude": None,
    }
    place_details_error = ""
    if restaurant.place_id and settings.GOOGLE_MAPS_API_KEY:
        retry_after = _rate_limit_retry_after(request.user.id, "restaurant_detail_page")
        if retry_after:
            place_details_error = f"Live details are rate-limited. Try again in {retry_after}s."
        else:
            try:
                live_details = _fetch_google_place_details_legacy(restaurant.place_id)
                place_details.update(live_details)
            except URLError as exc:
                place_details_error = _extract_google_error_message(exc)

    decisions = SwipeDecision.objects.filter(
        session=session,
        restaurant=restaurant,
    ).select_related("user")
    decision_by_user_id = {decision.user_id: decision.decision for decision in decisions}

    participants = session.participants.order_by("username")
    participant_rows = []
    approve_count = 0
    disapprove_count = 0

    for participant in participants:
        decision = decision_by_user_id.get(participant.id, "pending")
        if decision == SwipeDecision.APPROVE:
            approve_count += 1
        elif decision == SwipeDecision.DISAPPROVE:
            disapprove_count += 1
        participant_rows.append(
            {
                "username": participant.username,
                "decision": decision,
            }
        )

    participant_count = participants.count()
    all_approved = participant_count > 0 and approve_count == participant_count

    return render(
        request,
        "restaurants/restaurant_detail.html",
        {
            "session": session,
            "restaurant": restaurant,
            "place_details": place_details,
            "place_details_error": place_details_error,
            "participant_rows": participant_rows,
            "approve_count": approve_count,
            "disapprove_count": disapprove_count,
            "pending_count": max(participant_count - approve_count - disapprove_count, 0),
            "all_approved": all_approved,
        },
    )


@require_GET
def restaurant_api_details(request, restaurant_id):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    retry_after = _rate_limit_retry_after(request.user.id, "restaurant_details_api")
    if retry_after:
        return JsonResponse(
            {
                "error": "Too many requests. Please wait and try again.",
                "retry_after": retry_after,
            },
            status=429,
        )

    restaurant = get_object_or_404(Restaurant, id=restaurant_id)
    details = {
        "name": restaurant.name,
        "place_id": restaurant.place_id or "",
        "formatted_address": "",
        "rating": restaurant.rating,
        "user_ratings_total": restaurant.user_ratings_total,
        "price_level": restaurant.price_level,
        "business_status": restaurant.business_status,
        "types": restaurant.types or [],
        "formatted_phone_number": "",
        "international_phone_number": "",
        "website": "",
        "maps_url": "",
        "weekday_hours": [],
        "latitude": None,
        "longitude": None,
    }
    warning = ""

    if restaurant.place_id and settings.GOOGLE_MAPS_API_KEY:
        try:
            live_details = _fetch_google_place_details_legacy(restaurant.place_id)
            details.update(live_details)
            restaurant.rating = live_details.get("rating")
            restaurant.user_ratings_total = live_details.get("user_ratings_total")
            restaurant.price_level = live_details.get("price_level")
            restaurant.business_status = live_details.get("business_status") or ""
            restaurant.types = live_details.get("types") or []
            restaurant.save(
                update_fields=[
                    "rating",
                    "user_ratings_total",
                    "price_level",
                    "business_status",
                    "types",
                ]
            )
        except URLError as exc:
            warning = _extract_google_error_message(exc)

    return JsonResponse(
        {
            "ok": True,
            "restaurant": {
                "id": restaurant.id,
                "name": restaurant.name,
                "rating": restaurant.rating,
                "price_level": restaurant.price_level,
                "user_ratings_total": restaurant.user_ratings_total,
            },
            "details": details,
            "warning": warning,
        }
    )


@require_POST
def create_session(request):
    if not request.user.is_authenticated:
        return redirect("/accounts/login/?next=/")

    name = request.POST.get("name", "").strip()
    proposed_time_raw = request.POST.get("proposed_time", "").strip()
    if not name:
        name = f"{request.user.username}'s meal"

    proposed_time = parse_datetime(proposed_time_raw) if proposed_time_raw else None
    if proposed_time is None:
        proposed_time = timezone.now()
    if timezone.is_naive(proposed_time):
        proposed_time = timezone.make_aware(
            proposed_time, timezone.get_current_timezone()
        )

    session = DiningSession.objects.create(
        name=name,
        proposed_time=proposed_time,
        created_by=request.user,
    )
    SessionParticipant.objects.get_or_create(session=session, user=request.user)

    return redirect(f"/?session={session.id}")


@require_POST
def join_session(request):
    if not request.user.is_authenticated:
        return redirect("/accounts/login/?next=/")

    invite_code = request.POST.get("invite_code", "").strip()
    if not invite_code:
        return redirect("restaurants:index")

    session = DiningSession.objects.filter(invite_code=invite_code).first()
    if not session:
        return redirect("restaurants:index")

    SessionParticipant.objects.get_or_create(session=session, user=request.user)
    return redirect(f"/?session={session.id}")


@require_POST
def swipe_restaurant(request, session_id, restaurant_id):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    session = get_object_or_404(DiningSession, id=session_id)
    is_participant = SessionParticipant.objects.filter(
        session=session, user=request.user
    ).exists()
    if not is_participant:
        return JsonResponse({"error": "You are not in this session."}, status=403)

    restaurant = get_object_or_404(Restaurant, id=restaurant_id)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    decision = payload.get("decision")
    allowed = {choice for choice, _ in SwipeDecision.DECISION_CHOICES}
    if decision not in allowed:
        return JsonResponse({"error": "Decision must be approve or disapprove."}, status=400)

    SwipeDecision.objects.update_or_create(
        session=session,
        user=request.user,
        restaurant=restaurant,
        defaults={"decision": decision},
    )

    return JsonResponse(
        {
            "ok": True,
            "session_id": session.id,
            "restaurant_id": restaurant.id,
            "decision": decision,
        }
    )


@require_POST
def search_restaurants_by_location(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    retry_after = _rate_limit_retry_after(request.user.id, "location_search")
    if retry_after:
        return JsonResponse(
            {
                "error": "Too many requests. Please wait and try again.",
                "retry_after": retry_after,
            },
            status=429,
        )

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    mode = (payload.get("mode") or "").strip()
    location_label = ""
    query_text = ""
    latitude = None
    longitude = None
    if not settings.GOOGLE_MAPS_API_KEY:
        return JsonResponse(
            {"error": "Google Places API key is not configured."},
            status=503,
        )

    if mode == "query":
        query = (payload.get("query") or "").strip()
        if not query:
            return JsonResponse({"error": "Location query is required."}, status=400)
        location_label = query
        query_text = f"restaurants in {query}"
    elif mode == "device":
        try:
            latitude = float(payload.get("latitude"))
            longitude = float(payload.get("longitude"))
        except (TypeError, ValueError):
            return JsonResponse({"error": "Valid latitude and longitude are required."}, status=400)

        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return JsonResponse({"error": "Latitude or longitude is out of range."}, status=400)
        location_label = f"Current location ({latitude:.4f}, {longitude:.4f})"
        query_text = "restaurants"
    else:
        return JsonResponse({"error": "Mode must be query or device."}, status=400)

    try:
        nearby_places = _search_google_places_restaurants(
            query_text,
            latitude=latitude,
            longitude=longitude,
        )
    except URLError as exc:
        message = _extract_google_error_message(exc)
        return JsonResponse(
            {"error": f"Restaurant lookup failed: {message}"},
            status=502,
        )

    if not nearby_places:
        request.session.pop(LOCATION_SESSION_KEY, None)
        request.session.pop(LOCATION_LABEL_SESSION_KEY, None)
        request.session.pop(LOCATION_ADDRESS_MAP_SESSION_KEY, None)
        return JsonResponse(
            {
                "ok": True,
                "location_label": location_label,
                "restaurants": [],
            }
        )

    restaurant_entries = _save_restaurants_to_db(nearby_places)
    restaurant_ids = [entry["id"] for entry in restaurant_entries]
    address_map = {
        str(entry["id"]): entry["formatted_address"] for entry in restaurant_entries
    }
    request.session[LOCATION_SESSION_KEY] = restaurant_ids
    request.session[LOCATION_LABEL_SESSION_KEY] = location_label
    request.session[LOCATION_ADDRESS_MAP_SESSION_KEY] = address_map
    restaurants = restaurant_entries

    return JsonResponse(
        {
            "ok": True,
            "location_label": location_label,
            "restaurants": restaurants,
        }
    )
