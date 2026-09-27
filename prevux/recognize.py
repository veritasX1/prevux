"""Turn rough sketches into clean shapes, like Preview's Sketch tool."""

import math


def recognize(points):
    """Return ("line", start, end), ("oval"|"rect", bounds) or None."""
    if len(points) < 5:
        return None

    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    width, height = x1 - x0, y1 - y0
    diagonal = math.hypot(width, height)
    if diagonal < 8:
        return None

    length = sum(math.hypot(bx - ax, by - ay) for (ax, ay), (bx, by) in zip(points, points[1:]))
    start, end = points[0], points[-1]
    chord = math.hypot(end[0] - start[0], end[1] - start[1])

    # A nearly straight stroke becomes a line.
    if chord > 0 and length / chord < 1.12:
        deviation = max(distance_to_line(point, start, end) for point in points)
        if deviation < chord * 0.07:
            return ("line", start, end)

    # Closed strokes may be ovals or rectangles.
    if chord > diagonal * 0.25 or min(width, height) < diagonal * 0.15:
        return None

    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    a, b = width / 2, height / 2
    radii = [math.hypot((x - cx) / a, (y - cy) / b) for x, y in points]
    mean = sum(radii) / len(radii)
    spread = math.sqrt(sum((radius - mean) ** 2 for radius in radii) / len(radii))

    margin = min(width, height) * 0.14
    near_edge = sum(
        1 for x, y in points
        if min(x - x0, x1 - x, y - y0, y1 - y) < margin
    ) / len(points)

    # Rectangles hug the bounding box and have corners far from the center.
    corner_reach = max(radii)
    if near_edge > 0.88 and corner_reach > 1.22:
        return ("rect", (x0, y0, x1, y1))
    if spread < 0.12:
        return ("oval", (x0, y0, x1, y1))
    return None


def distance_to_line(point, start, end):
    (px, py), (ax, ay), (bx, by) = point, start, end
    length = math.hypot(bx - ax, by - ay)
    if length == 0:
        return math.hypot(px - ax, py - ay)
    return abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / length
