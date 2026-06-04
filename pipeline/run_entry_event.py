import cv2
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

video_path = r"pipeline/inputs/Store 2/entry 2.mp4"

cap = cv2.VideoCapture(video_path)

# -----------------------
# Entry Line
# -----------------------

LINE_Y = 650

track_history = {}

while True:

    success, frame = cap.read()

    if not success:
        break

    result = model(frame, verbose=False)[0]

    detections = sv.Detections.from_ultralytics(result)

    # Keep only persons
    person_mask = detections.class_id == 0
    detections = detections[person_mask]

    detections = tracker.update_with_detections(detections)

    # Draw entry line
    cv2.line(
        frame,
        (0, LINE_Y),
        (frame.shape[1], LINE_Y),
        (0, 0, 255),
        3
    )

    if detections.tracker_id is not None:

        for bbox, track_id in zip(
            detections.xyxy,
            detections.tracker_id
        ):

            x1, y1, x2, y2 = map(int, bbox)

            center_x = int((x1 + x2) / 2)
            center_y = int((y1 + y2) / 2)

            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"ID {track_id}",
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2
            )

            cv2.circle(
                frame,
                (center_x, center_y),
                5,
                (255, 0, 0),
                -1
            )

            # -----------------------
            # Line Crossing Logic
            # -----------------------

            if track_id not in track_history:
                track_history[track_id] = center_y
                continue

            previous_y = track_history[track_id]

            if previous_y < LINE_Y and center_y >= LINE_Y:

                save_event(
                    "ENTRY",
                    int(track_id)
                )

                print(
                    f"ENTRY {track_id}"
                )

            elif previous_y > LINE_Y and center_y <= LINE_Y:

                save_event(
                    "EXIT",
                    int(track_id)
                )

                print(
                    f"EXIT {track_id}"
                )

            track_history[track_id] = center_y

    cv2.imshow("Entry Events", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()