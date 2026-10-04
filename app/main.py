from fastapi import FastAPI

from .routers import knowledge, trips


def create_app() -> FastAPI:
    application = FastAPI(
        title="Travel Planning Agent API",
        version="0.5.0",
    )
    application.include_router(trips.router)
    application.include_router(knowledge.router)

    @application.get("/health", tags=["Health"])
    def health_check():
        return {
            "status": "ok",
            "version": "0.5.0",
        }

    return application


app = create_app()