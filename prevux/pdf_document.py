import pymupdf


class PDFDocument:

    def __init__(self, path):

        self.path = path
        self.doc = pymupdf.open(path)
        self.page_index = 0

    @property
    def page_count(self):
        return len(self.doc)

    @property
    def page(self):
        return self.doc[self.page_index]

    def render(self, dpi=120):

        matrix = pymupdf.Matrix(
            dpi / 72,
            dpi / 72
        )

        pix = self.page.get_pixmap(
            matrix=matrix,
            alpha=False
        )

        return pix.tobytes("png")

    def save(self, path=None):

        path = path or self.path

        self.doc.save(path)

        self.path = path

    def highlight(self, rect):

        annot = self.page.add_highlight_annot(rect)

        annot.set_colors(
            stroke=(1.0, 1.0, 0.0)
        )

        annot.update()

    def underline(self, rect):

        annot = self.page.add_underline_annot(rect)
        annot.update()

    def strikeout(self, rect):

        annot = self.page.add_strikeout_annot(rect)
        annot.update()

    def add_note(self, point, text):

        annot = self.page.add_text_annot(
            point,
            text
        )

        annot.update()
