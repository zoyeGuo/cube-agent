"""Digital Human Runtime — FastAPI application entry point."""
import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.chat import router as chat_router
from app.api.sessions import router as sessions_router
from app.api.setup import router as setup_router
from app.api.ws import router as ws_router
from app.api.clarify import router as clarify_router
from app.api.cancel import router as cancel_router
from app.services.scheduler import start as scheduler_start, stop as scheduler_stop
from app.knowledge import architecture_indexer

_log = logging.getLogger("runtime")
_file_handler: logging.FileHandler | None = None


def _setup_file_logging() -> None:
    global _file_handler
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    _file_handler = logging.FileHandler("runtime.log", encoding="utf-8")
    _file_handler.setFormatter(fmt)
    _file_handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(_file_handler)
    _log.info("=== runtime started, logging active ===")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_file_logging()
    from app.memory.manager import memory_manager
    removed = memory_manager.cleanup_episodes(normal_ttl_days=30)
    if removed:
        _log.info("startup cleanup: removed %d expired episodes", removed)
    try:
        architecture_indexer.ensure_index()
        _log.info("architecture index ready: %s", architecture_indexer.json_path)
    except Exception:
        _log.warning("architecture index build failed:\n%s", traceback.format_exc())
    scheduler_start()
    yield
    scheduler_stop()
    if _file_handler:
        _file_handler.flush()
        _file_handler.close()


app = FastAPI(title="Digital Human Runtime", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    _log.info("→ %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
        _log.info("← %s %s %d", request.method, request.url.path, response.status_code)
        return response
    except Exception:
        _log.error("Unhandled in %s %s:\n%s", request.method, request.url.path, traceback.format_exc())
        return JSONResponse(status_code=500, content={"detail": "internal error"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/v1")
app.include_router(sessions_router, prefix="/v1")
app.include_router(setup_router, prefix="/v1")
app.include_router(ws_router, prefix="/v1")
app.include_router(clarify_router, prefix="/v1")
app.include_router(cancel_router, prefix="/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
