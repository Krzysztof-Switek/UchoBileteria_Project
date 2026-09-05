from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("szukaj/", views.global_search, name="global_search"),
    path("raporty/", views.dashboard, name="dashboard"),
    path("raporty/wydarzenie/<int:event_id>/", views.event_report, name="event_report"),
    path("raporty/sprzedaz.csv", views.sales_csv, name="sales_csv"),
    path("raporty/sprzedaz.xlsx", views.sales_xlsx, name="sales_xlsx"),
    path("raporty/pule.csv", views.pool_sales_csv, name="pool_sales_csv"),
    path("raporty/pule.xlsx", views.pool_sales_xlsx, name="pool_sales_xlsx"),
    path("raporty/przeplywy.csv", views.cash_flow_csv, name="cash_flow_csv"),
    path("raporty/przeplywy.xlsx", views.cash_flow_xlsx, name="cash_flow_xlsx"),
]
