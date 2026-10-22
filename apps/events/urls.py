from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.event_list, name="list"),
    path("wydarzenia/<slug:slug>/", views.event_detail, name="detail"),
    path("wydarzenia/<slug:slug>/miejsca.json", views.seat_states, name="seat_states"),
    path(
        "wydarzenia/<slug:slug>/przypomnienie.ics",
        views.sales_start_reminder,
        name="sales_start_reminder",
    ),
]
