from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("kup/<slug:slug>/", views.purchase, name="purchase"),
    path("zamowienie/<uuid:order_id>/", views.order_detail, name="detail"),
]
