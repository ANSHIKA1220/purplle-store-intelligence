from app.models.session import SessionDB


def process_event(event, db):

    # ENTRY EVENT
    if event.event_type.upper() == "ENTRY":

        existing_session = (
            db.query(SessionDB)
            .filter(
                SessionDB.visitor_id == event.visitor_id
            )
            .first()
        )

        if not existing_session:

            session = SessionDB(
                visitor_id=event.visitor_id,
                store_id=event.store_id,
                entry_time=event.timestamp,
                is_staff=event.is_staff,
                converted=False
            )

            db.add(session)

    # EXIT EVENT
    elif event.event_type.upper() == "EXIT":

        session = (
            db.query(SessionDB)
            .filter(
                SessionDB.visitor_id == event.visitor_id
            )
            .first()
        )

        if session:
            session.exit_time = event.timestamp

            dwell = (
                    event.timestamp -
                    session.entry_time
            ).total_seconds()

            session.dwell_seconds = int(dwell)