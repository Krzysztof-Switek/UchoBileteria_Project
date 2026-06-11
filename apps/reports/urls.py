from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("raporty/", views.dashboard, name="dashboard"),
    path("raporty/sprzedaz.csv", views.sales_csv, name="sales_csv"),
    path("raporty/przeplywy.csv", views.cash_flow_csv, name="cash_flow_csv"),
]
