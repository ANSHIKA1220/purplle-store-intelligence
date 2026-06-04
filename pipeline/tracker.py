import supervision as sv

tracker = sv.ByteTrack(
    track_activation_threshold=0.20,
    lost_track_buffer=90,
    minimum_matching_threshold=0.65,
    frame_rate=30
)