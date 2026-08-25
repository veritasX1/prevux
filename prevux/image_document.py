from PIL import Image, ImageOps


class ImageDocument:

    def __init__(self, path):

        self.path = path

        self.image = ImageOps.exif_transpose(
            Image.open(path)
        ).copy()

        if self.image.mode not in ("RGB", "RGBA"):
            self.image = self.image.convert("RGBA")

    def save(self, path=None):

        path = path or self.path

        image = self.image

        if path.lower().endswith((".jpg", ".jpeg")):
            image = image.convert("RGB")

        image.save(path)

        self.path = path
