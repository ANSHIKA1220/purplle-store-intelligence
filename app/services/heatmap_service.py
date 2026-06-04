from sqlalchemy import func

from app.models.db_event import EventDB


def get_heatmap(store_id, db):

    results = (
        db.query(
            EventDB.zone_name,
            func.count().label("visits")
        )
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type == "ZONE_ENTER"
        )
        .group_by(
            EventDB.zone_name
        )
        .all()
    )

    return {
        "store_id": store_id,
        "zones": [
            {
                "zone": row.zone_name,
                "visits": row.visits
            }
            for row in results
        ]
    }