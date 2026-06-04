from app.models.db_event import EventDB


def get_funnel(store_id, db):

    entries = (
        db.query(EventDB)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type == "ENTRY"
        )
        .count()
    )

    zone_visits = (
        db.query(EventDB)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type == "ZONE_ENTER"
        )
        .count()
    )

    queue_events = (
        db.query(EventDB)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type.in_(
                [
                    "QUEUE_COMPLETED",
                    "QUEUE_ABANDONED"
                ]
            )
        )
        .count()
    )

    return {
        "store_id": store_id,
        "entry": entries,
        "zone_visit": zone_visits,
        "queue": queue_events
    }