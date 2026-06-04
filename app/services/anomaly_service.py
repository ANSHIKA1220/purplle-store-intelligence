from app.models.db_event import EventDB


def get_anomalies(store_id, db):

    anomalies = []

    queue_completed = (
        db.query(EventDB)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type == "QUEUE_COMPLETED"
        )
        .count()
    )

    queue_abandoned = (
        db.query(EventDB)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type == "QUEUE_ABANDONED"
        )
        .count()
    )

    total_queue = queue_completed + queue_abandoned

    if total_queue > 0:

        abandonment_rate = (
            queue_abandoned / total_queue
        ) * 100

        if abandonment_rate > 20:

            anomalies.append({
                "type": "QUEUE_ABANDONMENT",
                "severity": "HIGH",
                "value": round(abandonment_rate, 2),
                "message":
                "Queue abandonment rate exceeded threshold"
            })

    return {
        "store_id": store_id,
        "anomalies": anomalies
    }