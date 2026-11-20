import hmac
from django.conf import settings
from rest_framework.permissions import BasePermission


class HasApiKey(BasePermission):
    # Requests must send the configured key in the X-API-Key header.
    # If API_KEY is empty (local testing only) every request is allowed.
    def has_permission(self, request, view):
        if not settings.API_KEY:
            return True
        supplied_key = request.headers.get("X-API-Key", "")
        return hmac.compare_digest(supplied_key, settings.API_KEY)
