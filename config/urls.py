from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "logowanie/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("wyloguj/", auth_views.LogoutView.as_view(next_page="/"), name="logout"),
    path("", include("apps.checkin.urls")),
    path("", include("apps.reports.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.events.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
