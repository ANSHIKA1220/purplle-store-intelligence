import cv2
import numpy as np
import supervision as sv
from ultralytics import YOLO
from pipeline.event_writer import save_event
# -----------------------
# Model + Tracker
# -----------------------

model = YOLO("yolov8n.pt")

tracker = sv.ByteTrack(
    track_activation_threshold=0.20,
    lost_track_buffer=90,
    minimum_matching_threshold=0.65,
    frame_rate=30
)

# -----------------------
# Video
# -----------------------

video_path = r"pipeline/inputs/Store 2/zone.mp4"

cap = cv2.VideoCapture(video_path)

track_zones = {}

# -----------------------
# Zones
# -----------------------

LEFT_SHELF = [
    (0, 0),
    (300, 0),
    (300, 1080),
    (0, 1080)
]

CENTER_AISLE = [
    (300, 0),
    (660, 0),
    (660, 1080),
    (300, 1080)
]

RIGHT_SHELF = [
    (660, 0),
    (960, 0),
    (960, 1080),
    (660, 1080)
]


def get_zone(cx, cy):

    if cv2.pointPolygonTest(
        np.array(LEFT_SHELF, dtype=np.int32),
        (cx, cy),
        False
    ) >= 0:
        return "LEFT_SHELF"

    if cv2.pointPolygonTest(
        np.array(CENTER_AISLE, dtype=np.int32),
        (cx, cy),
        False
    ) >= 0:
        return "CENTER_AISLE"

    if cv2.pointPolygonTest(
        np.array(RIGHT_SHELF, dtype=np.int32),
        (cx, cy),
        False
    ) >= 0:
        return "RIGHT_SHELF"

    return None


while True:

    success, frame = cap.read()

    if not success:
        break

    result = model(frame, verbose=False)[0]

    detections = sv.Detections.from_ultralytics(result)

    # persons only
    person_mask = detections.class_id == 0
    detections = detections[person_mask]

    detections = tracker.update_with_detections(detections)

    if detections.tracker_id is not None:

        for bbox, track_id in zip(
            detections.xyxy,
            detections.tracker_id
        ):

            x1, y1, x2, y2 = map(int, bbox)

            cx = int((x1 + x2) / 2)
            cy = int((y1 + y2) / 2)

            current_zone = get_zone(cx, cy)

            previous_zone = track_zones.get(track_id)

            if previous_zone is None:

                track_zones[track_id] = current_zone

            elif previous_zone != current_zone:

                save_event(
                    "ZONE_CHANGE",
                    int(track_id),
                    current_zone
                )

                print(
                    f"{track_id}: {previous_zone} -> {current_zone}"
                )

                track_zones[track_id] = current_zone

            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                (0, 255, 0),
                2
            )

            cv2.circle(
                frame,
                (cx, cy),
                5,
                (0, 0, 255),
                -1
            )

            cv2.putText(
                frame,
                f"ID {track_id} | {current_zone}",
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2
            )

    # Zone boundaries

    cv2.line(frame, (300, 0), (300, 1080), (255, 0, 0), 2)
    cv2.line(frame, (660, 0), (660, 1080), (255, 0, 0), 2)

    cv2.imshow("Zone Analytics", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()