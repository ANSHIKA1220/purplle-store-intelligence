import cv2
import numpy as np


def point_in_polygon(point, polygon):

    polygon = np.array(polygon, dtype=np.int32)

    result = cv2.pointPolygonTest(
        polygon,
        point,
        False
    )

    return result >= 0