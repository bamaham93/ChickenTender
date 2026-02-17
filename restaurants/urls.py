from django.urls import path

from . import views

app_name = "restaurants"

urlpatterns = [
    path("", views.index, name="index"),
    path("sessions/create/", views.create_session, name="create_session"),
    path("sessions/join/", views.join_session, name="join_session"),
    path(
        "sessions/<int:session_id>/results/",
        views.session_results,
        name="session_results",
    ),
    path(
        "sessions/<int:session_id>/restaurants/<int:restaurant_id>/",
        views.restaurant_detail,
        name="restaurant_detail",
    ),
    path(
        "api/sessions/<int:session_id>/restaurants/<int:restaurant_id>/swipe/",
        views.swipe_restaurant,
        name="swipe_restaurant",
    ),
    path(
        "api/restaurants/by-location/",
        views.search_restaurants_by_location,
        name="search_restaurants_by_location",
    ),
    path(
        "api/restaurants/<int:restaurant_id>/details/",
        views.restaurant_api_details,
        name="restaurant_api_details",
    ),
]
