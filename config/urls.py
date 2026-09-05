from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "logowanie/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("wyloguj/", auth_views.LogoutView.as_view(next_page="/"), name="logout"),
    # KUP-04: linked from the public footer on every page.
    path(
        "regulamin/",
        TemplateView.as_view(template_name="legal/regulamin.html"),
        name="regulamin",
    ),
    path(
        "polityka-prywatnosci/",
        TemplateView.as_view(template_name="legal/polityka_prywatnosci.html"),
        name="polityka_prywatnosci",
    ),
    path("", include("apps.checkin.urls")),
    path("", include("apps.reports.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.events.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
