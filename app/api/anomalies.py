from fastapi import APIRouter
from fastapi import Depends

from sqlalchemy.orm import Session

from app.database import get_db

from app.services.anomaly_service import (
    get_anomalies
)

router = APIRouter(
    tags=["Anomalies"]
)


@router.get(
    "/stores/{store_id}/anomalies"
)
def anomalies(
    store_id: str,
    db: Session = Depends(get_db)
):

    return get_anomalies(
        store_id,
        db
    )