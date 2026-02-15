"""Cookie-based session authentication."""

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature
from functools import wraps

# Session cookie settings
COOKIE_NAME = "wb_admin_session"
COOKIE_MAX_AGE = 86400  # 24 hours
SECRET_KEY = "wayback-admin-secret-change-me"


def get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(SECRET_KEY)


def create_session_cookie(response: Response, username: str = "admin") -> None:
    """Set a signed session cookie."""
    s = get_serializer()
    token = s.dumps({"user": username})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def verify_session(request: Request) -> bool:
    """Check if the request has a valid session cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    s = get_serializer()
    try:
        s.loads(token, max_age=COOKIE_MAX_AGE)
        return True
    except BadSignature:
        return False


def clear_session_cookie(response: Response) -> None:
    """Delete the session cookie."""
    response.delete_cookie(COOKIE_NAME)
