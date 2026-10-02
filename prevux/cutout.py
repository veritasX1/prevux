"""Remove a background, cut out an object (Preview: Remove Background, Smart Lasso).

Classic image processing, no AI model, nothing downloaded: the background colour is taken
from the edge (of the image, or of the lasso), everything connected to the edge in that
colour becomes transparent, the edge of the object is softened slightly. Works best with
plain backgrounds (product photos, logos, screenshots, scans).
"""

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

WORK_SIDE = 800       # masks are computed at this size, then scaled up


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else 0


def _background_colour(image, points):
    pixels = image.load()
    width, height = image.size
    samples = [pixels[min(width - 1, max(0, int(x))), min(height - 1, max(0, int(y)))] for x, y in points]
    return tuple(_median([s[c] for s in samples]) for c in range(3))


def _threshold(histogram, low=14, high=70):
    """Otsu between background (small difference) and object (large difference)."""
    total = sum(histogram)
    weighted = sum(i * h for i, h in enumerate(histogram))
    best, best_value, back, back_sum = low, -1.0, 0, 0
    for i, count in enumerate(histogram):
        back += count
        if not back:
            continue
        front = total - back
        if not front:
            break
        back_sum += i * count
        between = back * front * (back_sum / back - (weighted - back_sum) / front) ** 2
        if between > best_value:
            best, best_value = i, between
    return max(low, min(high, best))


def _difference(small, colour):
    """Per pixel: how far it is from the background colour (largest channel difference)."""
    r, g, b = ImageChops.difference(small, Image.new("RGB", small.size, colour)).split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b).filter(ImageFilter.GaussianBlur(1))


def _flood_from(mask, seeds):
    """Fill (value 128) every 0-region of `mask` that one of the seeds touches."""
    pixels = mask.load()
    width, height = mask.size
    for x, y in seeds:
        x, y = min(width - 1, max(0, int(x))), min(height - 1, max(0, int(y)))
        if pixels[x, y] == 0:
            ImageDraw.floodfill(mask, (x, y), 128)
    return mask


def _finish(alpha_small, size):
    """Close tiny holes, scale up, soften the edge a little."""
    alpha = alpha_small.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    alpha = alpha.resize(size, Image.BILINEAR)
    return alpha.filter(ImageFilter.GaussianBlur(0.8))


def _small(image):
    rgb = image.convert("RGB")
    scale = min(1.0, WORK_SIDE / max(rgb.size))
    if scale < 1.0:
        rgb = rgb.resize((max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))), Image.BILINEAR)
    return rgb, scale


def remove_background(image):
    """The image with a transparent background (RGBA)."""
    small, _scale = _small(image)
    width, height = small.size
    edge = ([(x, 0) for x in range(width)] + [(x, height - 1) for x in range(width)]
            + [(0, y) for y in range(height)] + [(width - 1, y) for y in range(height)])
    difference = _difference(small, _background_colour(small, edge))
    limit = _threshold(difference.histogram())
    mask = difference.point(lambda v: 255 if v > limit else 0)
    mask = _flood_from(mask, edge[::3])
    alpha = mask.point(lambda v: 0 if v == 128 else 255)
    result = image.convert("RGBA")
    result.putalpha(ImageChops.multiply(result.getchannel("A"), _finish(alpha, image.size)))
    return result


def lasso_cutout(image, polygon):
    """Smart Lasso: (box, RGBA cut-out) of the object inside a roughly drawn outline.
    The outline snaps to the object's edge; without a clear edge it stays the drawn shape."""
    if len(polygon) < 3:
        return None
    small, scale = _small(image)
    outline = [(x * scale, y * scale) for x, y in polygon]
    inside = Image.new("L", small.size, 0)
    ImageDraw.Draw(inside).polygon(outline, fill=255)
    if not inside.getbbox():
        return None
    difference = _difference(small, _background_colour(small, outline))
    limit = _threshold(difference.histogram())
    mask = difference.point(lambda v: 255 if v > limit else 0)
    mask = ImageChops.multiply(mask, inside)          # outside the lasso counts as background
    seeds = [(x, y) for x, y in outline] + [(0, 0)]
    mask = _flood_from(mask, seeds)
    alpha = mask.point(lambda v: 0 if v == 128 else 255)
    alpha = ImageChops.multiply(alpha, inside)
    if alpha.getbbox() is None or sum(alpha.histogram()[128:]) < sum(inside.histogram()[128:]) * 0.03:
        alpha = inside                                 # nothing recognised: keep the drawn shape
    alpha = _finish(alpha, image.size)
    full_inside = Image.new("L", image.size, 0)
    ImageDraw.Draw(full_inside).polygon([(x, y) for x, y in polygon], fill=255)
    alpha = ImageChops.multiply(alpha, full_inside.filter(ImageFilter.GaussianBlur(0.8)))
    box = alpha.getbbox()
    if box is None:
        return None
    cut = image.convert("RGBA")
    cut.putalpha(ImageChops.multiply(cut.getchannel("A"), alpha))
    return box, cut.crop(box)


def has_transparency(image):
    return image.mode in ("RGBA", "LA") and image.getchannel("A").getextrema()[0] < 255
