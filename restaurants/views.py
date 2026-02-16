import json
from urllib.parse import quote

from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from .models import DiningSession, Restaurant, SessionParticipant, SwipeDecision


def index(request):
    starter_restaurants = ["Chick-fil-A", "Raising Cane's", "Popeyes"]
    for name in starter_restaurants:
        Restaurant.objects.get_or_create(name=name)

    selected_session = None
    sessions = []
    created_sessions = []
    restaurants_query = Restaurant.objects.all()
    invite_code = request.GET.get("invite", "").strip()

    if invite_code and not request.user.is_authenticated:
        next_url = quote(request.get_full_path(), safe="/?=&")
        return redirect(f"/accounts/login/?next={next_url}")

    if request.user.is_authenticated:
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

    restaurants = list(restaurants_query.values("id", "name"))
    return render(
        request,
        "restaurants/index.html",
        {
            "restaurants": restaurants,
            "sessions": sessions,
            "created_sessions": created_sessions,
            "selected_session": selected_session,
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
        .filter(approver_count=participant_count)
        .order_by("name")
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
            "participant_rows": participant_rows,
            "approve_count": approve_count,
            "disapprove_count": disapprove_count,
            "pending_count": max(participant_count - approve_count - disapprove_count, 0),
            "all_approved": all_approved,
        },
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
