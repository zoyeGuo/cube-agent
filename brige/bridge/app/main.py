from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.chat import router as chat_router
from app.routers.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Desktop Digital Human Bridge",
        version="0.1.0",
        description="A minimal bridge service between the desktop frontend and Hermes.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(chat_router)
    return app


app = create_app()
