from fastapi import FastAPI
from app.models.session import SessionDB
from app.models.pos_transaction import POSTransaction
from app.database import Base
from app.database import engine
from app.api.ingest import router as ingest_router
from app.models.db_event import EventDB
from app.api.metrics import router as metrics_router
from app.api.debug import router as debug_router
from app.api.funnel import router as funnel_router
from app.api.heatmap import router as heatmap_router
from app.api.anomalies import router as anomalies_router
from app.models.cv_event import CVEvent
from app.api.cv import router as cv_router
app = FastAPI(
    title="Store Intelligence API"
)
app.include_router(ingest_router)
app.include_router(metrics_router)
app.include_router(debug_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(cv_router)
Base.metadata.create_all(bind=engine)


@app.get("/")
def root():
    return {
        "message": "Store Intelligence API Running"
    }