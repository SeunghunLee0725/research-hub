from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from hub.api import agent_api
from hub.config import Settings
from hub.db.session import make_sessionmaker
from hub.web import routes
from hub.web.auth import LoginLimiter


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="research-hub", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.sessionmaker = make_sessionmaker(settings.database_url)
    app.state.login_limiter = LoginLimiter()
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="hub_session",
                       max_age=settings.session_max_age_seconds, same_site="strict", https_only=False)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
    app.include_router(agent_api.router)
    app.include_router(routes.router)
    return app
