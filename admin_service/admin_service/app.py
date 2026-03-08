"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_admin_config
from .redis_client import get_redis, close_redis
from .auth import verify_session
from .routes import auth as auth_routes
from .routes import dashboard as dashboard_routes
from .routes import config as config_routes
from .routes import cache as cache_routes
from .routes import crawler as crawler_routes
from .routes import warc as warc_routes

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    admin_config = get_admin_config()
    app.state.admin_config = admin_config
    app.state.redis = await get_redis(admin_config.redis_url)
    yield
    await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(title="Wayback Proxy Admin", lifespan=lifespan)

    # Static files
    app.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static",
    )

    # Templates
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    app.state.templates = templates

    # Auth middleware — redirect unauthenticated requests to /login
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        public_paths = {"/login", "/static"}
        path = request.url.path

        if any(path.startswith(p) for p in public_paths):
            return await call_next(request)

        # If no password configured, skip auth
        admin_config = getattr(request.app.state, "admin_config", None)
        if admin_config and not admin_config.admin_password:
            return await call_next(request)

        if not verify_session(request):
            return RedirectResponse("/login", status_code=303)

        return await call_next(request)

    # Include routers
    app.include_router(auth_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(config_routes.router)
    app.include_router(cache_routes.router)
    app.include_router(crawler_routes.router)
    app.include_router(warc_routes.router)

    return app
