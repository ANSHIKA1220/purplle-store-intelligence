from fastapi import APIRouter
from fastapi import Depends

from sqlalchemy.orm import Session

from app.database import get_db

from app.services.heatmap_service import get_heatmap

router = APIRouter(
    tags=["Heatmap"]
)


@router.get(
    "/stores/{store_id}/heatmap"
)
def heatmap(
    store_id: str,
    db: Session = Depends(get_db)
):
    return get_heatmap(
        store_id,
        db
    )