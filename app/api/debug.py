from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import SessionDB

router = APIRouter(
    tags=["Debug"]
)


@router.get("/debug/sessions")
def get_sessions(
        db: Session = Depends(get_db)
):

    sessions = db.query(SessionDB).all()

    return [
        {
            "visitor_id": s.visitor_id,
            "store_id": s.store_id,
            "entry_time": s.entry_time,
            "exit_time": s.exit_time
        }
        for s in sessions
    ]