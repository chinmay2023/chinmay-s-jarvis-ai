# jarvis_web/assistance/middleware.py
import time
from importlib import import_module
from django.conf import settings
from django.utils.http import http_date
from django.utils.cache import patch_vary_headers
from django.contrib.sessions.middleware import SessionMiddleware


class DualSessionMiddleware(SessionMiddleware):
    """
    Inherits from SessionMiddleware to satisfy admin.E410 system checks.
    Uses standard __call__ pipeline to separate admin cookies (/admin/) 
    from HUD user cookies (/).
    """

    def __init__(self, get_response):
        super().__init__(get_response)
        engine = import_module(settings.SESSION_ENGINE)
        self.SessionStore = engine.SessionStore

    def __call__(self, request):
        is_admin = request.path.startswith('/admin')
        cookie_name = 'admin_sessionid' if is_admin else 'sessionid'
        cookie_path = '/admin/' if is_admin else '/'

        # 1. Attach the scoped session store to the request
        session_key = request.COOKIES.get(cookie_name)
        request.session = self.SessionStore(session_key)

        # 2. Process the request down the Django pipeline
        response = self.get_response(request)

        # 3. Process the session and cookie for the response
        try:
            accessed = request.session.accessed
            modified = request.session.modified
            empty = request.session.is_empty()
        except AttributeError:
            return response

        session_key = request.COOKIES.get(cookie_name)

        if empty:
            if session_key:
                response.delete_cookie(
                    cookie_name,
                    path=cookie_path,
                    domain=settings.SESSION_COOKIE_DOMAIN,
                    samesite='Lax',
                )
            return response

        if accessed:
            patch_vary_headers(response, ('Cookie',))

        if modified and not empty:
            if request.session.get_expire_at_browser_close():
                max_age = None
                expires = None
            else:
                max_age = request.session.get_expiry_age()
                expires_time = time.time() + max_age
                expires = http_date(expires_time)

            response.set_cookie(
                cookie_name,
                request.session.session_key,
                max_age=max_age,
                expires=expires,
                domain=settings.SESSION_COOKIE_DOMAIN,
                path=cookie_path,
                secure=settings.SESSION_COOKIE_SECURE or False,
                httponly=True,
                samesite='Lax',
            )

        return response