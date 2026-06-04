import time
import uuid
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.database import Base, engine
from app.models.session import SessionDB          # noqa: F401 – ensure table created
from app.models.pos_transaction import POSTransaction  # noqa: F401
from app.models.db_event import EventDB           # noqa: F401
from app.models.cv_event import CVEvent           # noqa: F401

from app.api.ingest import router as ingest_router
from app.api.ingest_raw import router as ingest_raw_router
from app.api.metrics import router as metrics_router
from app.api.funnel import router as funnel_router
from app.api.heatmap import router as heatmap_router
from app.api.anomalies import router as anomalies_router
from app.api.health import router as health_router
from app.api.debug import router as debug_router
from app.api.cv import router as cv_router
from app.api.stores import router as stores_router

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)
logger = logging.getLogger("store_intelligence")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Store Intelligence API",
    version="1.0.0",
    description="Retail analytics API — from raw CCTV to live store metrics",
)

# ---------------------------------------------------------------------------
# Structured request logging middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    start = time.time()

    try:
        response: Response = await call_next(request)
    except Exception as exc:
        latency_ms = int((time.time() - start) * 1000)
        logger.error(
            "",
            extra={
                "trace_id": trace_id,
                "store_id": request.path_params.get("store_id", "-"),
                "endpoint": request.url.path,
                "method": request.method,
                "latency_ms": latency_ms,
                "status_code": 500,
                "error": str(exc),
            },
        )
        raise

    latency_ms = int((time.time() - start) * 1000)
    logger.info(
        "",
        extra={
            "trace_id": trace_id,
            "store_id": request.path_params.get("store_id", "-"),
            "endpoint": request.url.path,
            "method": request.method,
            "latency_ms": latency_ms,
            "status_code": response.status_code,
        },
    )
    response.headers["X-Trace-Id"] = trace_id
    return response


# ---------------------------------------------------------------------------
# Database-unavailable → HTTP 503 (graceful degradation)
# ---------------------------------------------------------------------------
@app.exception_handler(OperationalError)
async def db_unavailable_handler(request: Request, exc: OperationalError):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    logger.error("Database unavailable: %s", exc)
    return JSONResponse(
        status_code=503,
        content={
            "error": "SERVICE_UNAVAILABLE",
            "message": "Database is temporarily unavailable. Please retry shortly.",
            "trace_id": trace_id,
        },
    )


# ---------------------------------------------------------------------------
# Generic 500 handler — no raw stack traces in responses
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred.",
            "trace_id": trace_id,
        },
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(ingest_router)
app.include_router(ingest_raw_router)
app.include_router(stores_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(health_router)
app.include_router(debug_router)
app.include_router(cv_router)

# ---------------------------------------------------------------------------
# Create all tables on startup
# ---------------------------------------------------------------------------
Base.metadata.create_all(bind=engine)


@app.get("/", tags=["default"])
def root():
    return {"message": "Store Intelligence API Running", "version": "1.0.0"}
