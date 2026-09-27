from django.contrib.auth.models import UserManager

from .utils.validators import normalize_username


class CanonicalUserManager(UserManager):
    """Apply canonical username handling to Django's natural-key lookups."""

    use_in_migrations = True

    def get_by_natural_key(self, username):
        return super().get_by_natural_key(normalize_username(username))
