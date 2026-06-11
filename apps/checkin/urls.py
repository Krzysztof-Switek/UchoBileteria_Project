from django.urls import path

from . import views

app_name = "checkin"

urlpatterns = [
    path("wejscie/", views.event_select, name="event_select"),
    path("wejscie/weryfikuj", views.verify, name="verify"),
    path("wejscie/<int:event_id>/skaner/", views.scanner, name="scanner"),
    path("wejscie/<int:event_id>/skan/", views.scan, name="scan"),
    path("wejscie/<int:event_id>/szukaj/", views.search, name="search"),
    path("wejscie/<int:event_id>/odpraw/", views.check_in_code, name="check_in_code"),
    path(
        "wejscie/<int:event_id>/lista-awaryjna.csv",
        views.emergency_list,
        name="emergency_list",
    ),
]
