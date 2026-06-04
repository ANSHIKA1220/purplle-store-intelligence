from fastapi import APIRouter
from fastapi import Depends

from sqlalchemy.orm import Session

from app.database import get_db

from app.services.metrics_service import (
    get_store_metrics
)

router = APIRouter(
    tags=["Metrics"]
)


@router.get(
    "/stores/{store_id}/metrics"
)
def metrics(
        store_id: str,
        db: Session = Depends(get_db)
):

    return get_store_metrics(
        store_id,
        db
    )