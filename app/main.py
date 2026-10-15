from fastapi import FastAPI

from .routers import accounts, knowledge, trips
from .services.auth_rate_limit import create_auth_rate_limiter


def create_app() -> FastAPI:
    application = FastAPI(
        title="Travel Planning Agent API",
        version="0.7.0",
    )
    application.state.auth_rate_limiter = create_auth_rate_limiter()
    application.include_router(accounts.router)
    application.include_router(trips.router)
    application.include_router(knowledge.router)

    @application.get("/health", tags=["Health"])
    def health_check():
        return {
            "status": "ok",
            "version": "0.7.0",
        }

    return application


app = create_app()
