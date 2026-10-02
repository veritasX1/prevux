"""Live Text: recognise text in images and scanned PDF pages, so it can be selected, copied
and searched like real text (Preview: Live Text). Uses Tesseract on this computer – nothing
leaves the machine. Without Tesseract installed, Prevux simply works as before."""

import os
import shutil
import subprocess
import tempfile

PREFERRED = ("deu", "eng")
_languages = None


def available():
    return shutil.which("tesseract") is not None


def languages():
    """German and English if installed (as many as there are), joined for Tesseract."""
    global _languages
    if _languages is None:
        try:
            out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout
            installed = {line.strip() for line in out.splitlines()[1:]}
        except (OSError, subprocess.SubprocessError):
            installed = set()
        chosen = [lang for lang in PREFERRED if lang in installed] or sorted(installed - {"osd"})[:1]
        _languages = "+".join(chosen)
    return _languages


def recognize(image, scale=1.0):
    """Words as (x0, y0, x1, y1, text, block, line) in the image's coordinates / scale.
    `image` is a PIL image; `scale` is how many image pixels make one document unit."""
    if not available():
        return []
    with tempfile.TemporaryDirectory(prefix="prevux-ocr-") as folder:
        path = os.path.join(folder, "page.png")
        image.convert("RGB").save(path)
        command = ["tesseract", path, "-", "--psm", "3", "tsv"]
        if languages():
            command[3:3] = ["-l", languages()]
        try:
            out = subprocess.run(command, capture_output=True, text=True, timeout=180).stdout
        except (OSError, subprocess.SubprocessError):
            return []
    words = []
    for row in out.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        text = parts[11].strip()
        try:
            confidence = float(parts[10])
        except ValueError:
            confidence = -1
        if not text or confidence < 30:
            continue
        block, paragraph, line = int(parts[2]), int(parts[3]), int(parts[4])
        left, top, width, height = (int(v) for v in parts[6:10])
        words.append((left / scale, top / scale, (left + width) / scale, (top + height) / scale,
                      text, block * 1000 + paragraph, line))
    return words


def search_words(words, query):
    """Rectangles of `query` (case-insensitive, may span several words of a line)."""
    query = query.strip().lower()
    if not query:
        return []
    lines = {}
    for word in words:
        lines.setdefault((word[5], word[6]), []).append(word)
    hits = []
    for line in lines.values():
        text, spans = "", []
        for word in line:
            if text:
                text += " "
            spans.append((len(text), len(text) + len(word[4]), word))
            text += word[4]
        lower = text.lower()
        start = lower.find(query)
        while start >= 0:
            end = start + len(query)
            covered = [w for s, e, w in spans if s < end and e > start]
            if covered:
                hits.append((min(w[0] for w in covered), min(w[1] for w in covered),
                             max(w[2] for w in covered), max(w[3] for w in covered)))
            start = lower.find(query, end)
    return hits
