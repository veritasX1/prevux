# Prevux

A lightweight image and PDF viewer for Linux, inspired by macOS Preview —
made to feel familiar to people switching from the Mac.

Built with GTK 4 and libadwaita, PyMuPDF and Pillow.

## Features

- **Images and PDFs** in one window, several documents side by side in the
  thumbnail sidebar
- **Continuous scrolling** through PDF pages, zoom with <kbd>Ctrl</kbd> + scroll
  wheel or a touchpad pinch
- **Markup toolbar** like in Preview: text selection, rectangular selection,
  sketch, shapes (line, arrow, rectangle, oval, speech bubble, star, polygon),
  text boxes, notes, signatures, shape style, border and fill color, text style
- **Highlight, underline and strike through** text in PDFs
- Markup stays **editable**: in PDFs it is saved as standard PDF annotations
  that other viewers show as well, and Prevux can edit them again later
- **Pages**: rotate, reorder by drag and drop, insert blank pages, delete
- **Images**: crop, rotate, flip, adjust size, adjust color
- Undo/redo for everything, search in PDFs, print, export to other formats
- Light and dark mode, window controls on the leading edge like on the Mac
- German and English user interface

## Running

Prevux needs GTK 4, libadwaita (≥ 1.5) and PyGObject from the system:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py [FILES…]
```

## Keyboard shortcuts

Shortcuts follow Preview, with <kbd>⌘</kbd> mapped to <kbd>Ctrl</kbd> and
<kbd>⌥</kbd> to <kbd>Alt</kbd>:

| Action | Shortcut |
|---|---|
| Open / Save / Export | <kbd>Ctrl</kbd>+<kbd>O</kbd> / <kbd>Ctrl</kbd>+<kbd>S</kbd> / <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd> |
| Show markup toolbar | <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>A</kbd> |
| Rotate left / right | <kbd>Ctrl</kbd>+<kbd>L</kbd> / <kbd>Ctrl</kbd>+<kbd>R</kbd> |
| Crop | <kbd>Ctrl</kbd>+<kbd>K</kbd> |
| Highlight text | <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>H</kbd> |
| Actual size / zoom to fit | <kbd>Ctrl</kbd>+<kbd>0</kbd> / <kbd>Ctrl</kbd>+<kbd>9</kbd> |
| Zoom in / out | <kbd>Ctrl</kbd>+<kbd>+</kbd> / <kbd>Ctrl</kbd>+<kbd>-</kbd> |
| Thumbnails / content only | <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>2</kbd> / <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>1</kbd> |
| Previous / next page | <kbd>Alt</kbd>+<kbd>↑</kbd> / <kbd>Alt</kbd>+<kbd>↓</kbd> |
| Inspector | <kbd>Ctrl</kbd>+<kbd>I</kbd> |

All shortcuts are listed under *Menu → Keyboard Shortcuts*
(<kbd>Ctrl</kbd>+<kbd>?</kbd>).
