"""Staff log in with their e-mail address; brute-force attempts are throttled.

Replaces the default ModelBackend (see AUTHENTICATION_BACKENDS in settings) so
every login form in the project — /admin/login/ and the door-staff /logowanie/
— gets the same behaviour for free.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

from .ratelimit import is_login_rate_limited, register_login_failure


class EmailOrUsernameBackend(ModelBackend):
    """Authenticate by e-mail (preferred) or username, rate-limited per IP+identifier."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        if request is not None and is_login_rate_limited(request, username):
            return None

        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(
                Q(email__iexact=username) | Q(username__iexact=username)
            )
        except (UserModel.DoesNotExist, UserModel.MultipleObjectsReturned):
            # Hash a dummy password anyway so a nonexistent account doesn't
            # respond faster than a real one (timing side-channel).
            UserModel().set_password(password)
            if request is not None:
                register_login_failure(request, username)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user

        if request is not None:
            register_login_failure(request, username)
        return None
