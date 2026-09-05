from django.apps import AppConfig

# UX-05: panel sections ordered by day-to-day workflow (events → orders →
# tickets → audit trail), not alphabetically. Apps not listed here (e.g.
# django.contrib.auth, visible only to real superusers) sort after these.
STAFF_APP_ORDER = ["events", "orders", "tickets", "auditlog", "accounts"]


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "Konta i role"

    def ready(self):
        from django.contrib import admin
        from django.contrib.auth.signals import user_logged_in

        from .forms import EmailAdminAuthenticationForm
        from .ratelimit import handle_successful_login

        admin.site.login_form = EmailAdminAuthenticationForm
        admin.site.site_header = "Bileteria UCHO"
        admin.site.site_title = "Bileteria UCHO — panel obsługi"
        admin.site.index_title = "Panel obsługi"

        original_get_app_list = admin.site.get_app_list
        order = {label: i for i, label in enumerate(STAFF_APP_ORDER)}

        def get_app_list(request, app_label=None):
            app_list = original_get_app_list(request, app_label=app_label)
            return sorted(app_list, key=lambda app: order.get(app["app_label"], len(order)))

        admin.site.get_app_list = get_app_list

        # Module-level receiver + dispatch_uid: a local closure here would only
        # be weakly referenced by the signal and get garbage-collected right
        # after ready() returns, silently breaking the connection.
        user_logged_in.connect(
            handle_successful_login, dispatch_uid="accounts_clear_login_failures"
        )
