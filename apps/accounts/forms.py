from django.contrib.admin.forms import AdminAuthenticationForm


class EmailAdminAuthenticationForm(AdminAuthenticationForm):
    """Admin login form, relabelled since accounts log in with their e-mail."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Adres e-mail"
        self.fields["username"].widget.attrs["autocomplete"] = "email"
