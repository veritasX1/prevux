"""A signature from a photo, as vector strokes (like Preview's camera signatures).

Phone photo of a signature on paper → even out the lighting → separate ink from paper →
drop specks → trace the centre line of the ink (Zhang–Suen thinning) → smooth polylines.
The result has the same format as a signature drawn by hand: a list of strokes, each a list
of (x, y) points, scaled so that the usual pen width of 2.2 matches the ink on the photo.

Pure Python + Pillow, no extra libraries. Photos are scaled down first, so it stays fast.
"""

from PIL import Image, ImageFilter, ImageOps

PEN_WIDTH = 2.2          # width signatures are drawn with (markup.on_signature)
MAX_SIDE = 900           # working resolution
NEIGHBOURS = ((-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0))


class NoSignatureFound(ValueError):
    pass


def ink_mask(image):
    """Set of (x, y) ink pixels and the working size."""
    image = ImageOps.exif_transpose(image).convert("L")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    image = image.filter(ImageFilter.MedianFilter(3))
    width, height = image.size
    # Even out shadows and gradients: compare every pixel with its blurred surroundings.
    background = image.filter(ImageFilter.GaussianBlur(max(12, min(width, height) / 12)))
    pixels = image.load()
    bg = background.load()
    ratio = [[0] * width for _ in range(height)]
    histogram = [0] * 256
    for y in range(height):
        row = ratio[y]
        for x in range(width):
            value = min(255, int(pixels[x, y] * 255 / max(1, bg[x, y])))
            row[x] = value
            histogram[value] += 1
    threshold = min(otsu(histogram), 200)
    return {(x, y) for y in range(height) for x in range(width) if ratio[y][x] < threshold}, (width, height)


def otsu(histogram):
    total = sum(histogram)
    weighted = sum(i * h for i, h in enumerate(histogram))
    best, best_value = 0, -1.0
    back = back_sum = 0
    for i, count in enumerate(histogram):
        back += count
        if back == 0:
            continue
        front = total - back
        if front == 0:
            break
        back_sum += i * count
        mean_back = back_sum / back
        mean_front = (weighted - back_sum) / front
        between = back * front * (mean_back - mean_front) ** 2
        if between > best_value:
            best, best_value = i, between
    return best


def components(points):
    """Connected groups of pixels (8-neighbourhood)."""
    left = set(points)
    groups = []
    while left:
        start = left.pop()
        group, todo = [start], [start]
        while todo:
            x, y = todo.pop()
            for dx, dy in NEIGHBOURS:
                p = (x + dx, y + dy)
                if p in left:
                    left.remove(p)
                    group.append(p)
                    todo.append(p)
        groups.append(group)
    return groups


def clean(points):
    """Keep the signature, drop dust, paper texture and specks."""
    groups = components(points)
    if not groups:
        raise NoSignatureFound("keine Tinte gefunden")
    largest = max(len(g) for g in groups)
    # Dots (i, ä, a full stop) are small but real; paper dust is smaller still.
    keep = [g for g in groups if len(g) >= max(12, largest * 0.004)]
    return {p for g in keep for p in g}


def thin(points):
    """Zhang–Suen thinning: the one-pixel centre line of the ink."""
    skeleton = set(points)

    def ring(x, y):
        return [(x + dx, y + dy) in skeleton for dx, dy in ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))]

    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            remove = []
            for (x, y) in skeleton:
                p = ring(x, y)   # p2 … p9 clockwise from north
                count = sum(p)
                if not 2 <= count <= 6:
                    continue
                transitions = sum(1 for i in range(8) if not p[i] and p[(i + 1) % 8])
                if transitions != 1:
                    continue
                if step == 0 and (p[0] and p[2] and p[4] or p[2] and p[4] and p[6]):
                    continue
                if step == 1 and (p[0] and p[2] and p[6] or p[0] and p[4] and p[6]):
                    continue
                remove.append((x, y))
            if remove:
                skeleton.difference_update(remove)
                changed = True
    return skeleton


def trace(skeleton):
    """Turn the centre line into polylines (start at line ends, then remaining loops)."""
    def neighbours(p):
        return [(p[0] + dx, p[1] + dy) for dx, dy in NEIGHBOURS if (p[0] + dx, p[1] + dy) in skeleton]

    visited_edges = set()
    lines = []

    def walk(start, nxt):
        line = [start]
        previous, current = start, nxt
        while True:
            visited_edges.add(frozenset((previous, current)))
            line.append(current)
            options = [n for n in neighbours(current) if frozenset((current, n)) not in visited_edges and n != previous]
            if not options:
                return line
            # Go on as straight as possible.
            dx, dy = current[0] - previous[0], current[1] - previous[1]
            options.sort(key=lambda n: -((n[0] - current[0]) * dx + (n[1] - current[1]) * dy))
            previous, current = current, options[0]

    # Single points (a dot thinned down to one pixel) become a tiny stroke.
    for p in skeleton:
        if not neighbours(p):
            lines.append([p, (p[0] + 0.4, p[1])])

    ends = [p for p in skeleton if len(neighbours(p)) == 1]
    junctions = [p for p in skeleton if len(neighbours(p)) >= 3]
    for start in ends + junctions + list(skeleton):
        for n in neighbours(start):
            if frozenset((start, n)) not in visited_edges:
                lines.append(walk(start, n))
    return [line for line in lines if len(line) >= 4 or (len(line) == 2 and not neighbours(line[0]))]


def simplify(points, tolerance=0.9):
    """Ramer–Douglas–Peucker."""
    if len(points) < 3:
        return points
    (ax, ay), (bx, by) = points[0], points[-1]
    length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 or 1.0
    index, distance = 0, 0.0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / length
        if d > distance:
            index, distance = i, d
    if distance <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[:index + 1], tolerance)[:-1] + simplify(points[index:], tolerance)


def smooth(points):
    """One pass of Chaikin smoothing – soft curves like a pen, without changing the shape."""
    if len(points) < 3:
        return points
    result = [points[0]]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        result.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
        result.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
    result.append(points[-1])
    return result


def signature_from_image(path):
    """Strokes of the signature on a photo (raises NoSignatureFound)."""
    with Image.open(path) as image:
        ink, _size = ink_mask(image)
    if len(ink) < 30:
        raise NoSignatureFound("keine Tinte gefunden")
    ink = clean(ink)
    skeleton = thin(ink)
    # Small round blobs (i-dots) can thin away completely: keep their centre as a dot.
    for group in components(ink):
        if not any(p in skeleton for p in group):
            cx = round(sum(x for x, _y in group) / len(group))
            cy = round(sum(y for _x, y in group) / len(group))
            skeleton.add((cx, cy))
    if not skeleton:
        raise NoSignatureFound("keine Linien gefunden")
    width = max(1.0, len(ink) / len(skeleton))          # average ink width in pixels
    scale = PEN_WIDTH / width
    x0 = min(x for x, _y in ink)
    y0 = min(y for _x, y in ink)
    strokes = []
    for line in trace(skeleton):
        points = smooth(simplify([(float(x), float(y)) for x, y in line]))
        strokes.append([(round((x - x0) * scale, 2), round((y - y0) * scale, 2)) for x, y in points])
    if not strokes:
        raise NoSignatureFound("keine Linien gefunden")
    return strokes
