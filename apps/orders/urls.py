from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("kup/<slug:slug>/", views.purchase, name="purchase"),
    path("zamowienie/<uuid:order_id>/", views.order_detail, name="detail"),
    # Demo checkout (virtual currency)
    path("kasa-demo/<uuid:order_id>/", views.demo_checkout, name="demo_checkout"),
    path("kasa-demo/<uuid:order_id>/zaplac/", views.demo_pay, name="demo_pay"),
    path("kasa-demo/<uuid:order_id>/odrzuc/", views.demo_decline, name="demo_decline"),
    path("kasa-demo/<uuid:order_id>/zaplac-pozniej/", views.demo_pay_delayed,
         name="demo_pay_delayed"),
    path("kasa-demo/<uuid:order_id>/porzuc/", views.demo_abandon, name="demo_abandon"),
    path("kasa-demo/dostarcz-webhook/", views.demo_deliver_webhook,
         name="demo_deliver_webhook"),
    # Server-to-server webhook endpoint
    path("webhooks/demo/", views.demo_webhook, name="demo_webhook"),
]
