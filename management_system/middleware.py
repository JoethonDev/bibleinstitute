from django.urls import reverse
from django.shortcuts import redirect
from logging import getLogger

logging = getLogger(__name__)

class SessionExpiryUpdate:
    def __init__(self, get_response):
        self.get_response = get_response
        # One-time configuration and initialization.

    def __call__(self, request):
        # Code to be executed for each request before
        # the view (and later middleware) are called.
        if request.user.is_authenticated:
            expiry_time = request.session.get_expiry_age()
            if expiry_time > 0:
                try:
                    request.session.set_expiry(request.session.get_session_cookie_age())
                    logging.info(f"{request.user} has refreshed his session")
                except Exception as e:
                    logging.error(f"Error: {e} has occurred with {request.user} in session refresh")

        response = self.get_response(request)

        # Code to be executed for each request/response after
        # the view is called.

        return response