from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.funnel_service import get_funnel

router = APIRouter(
    tags=["Funnel"]
)

@router.get("/stores/{store_id}/funnel")
def funnel(
    store_id: str,
    db: Session = Depends(get_db)
):
    return get_funnel(store_id, db)