from pathlib import Path


class Document:

    def __init__(self, path):
        self.path = path

    @property
    def name(self):
        return Path(self.path).name

    @property
    def suffix(self):
        return Path(self.path).suffix.lower()
