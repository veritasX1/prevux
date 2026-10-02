"""Earlier versions of a document (Preview: File → Revert To → Browse All Versions).

Linux has no version store like macOS, so Prevux keeps its own: right before a save
overwrites a file, the file as it was goes into the user's data folder. Each document
keeps its newest KEEP versions; very large files are not copied."""

import hashlib
import json
import shutil
import time
from pathlib import Path

from gi.repository import GLib

ROOT = Path(GLib.get_user_data_dir()) / "prevux" / "versions"
KEEP = 20
MAX_SIZE = 200 * 1024 * 1024


def _folder(path):
    return ROOT / hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:20]


def _index(folder):
    try:
        return json.loads((folder / "index.json").read_text())
    except (OSError, ValueError):
        return {"items": []}


def _write(folder, index):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.json").write_text(json.dumps(index, indent=1))


def keep(path):
    """Store the file as it is now, before it is overwritten."""
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError:
        return
    if size > MAX_SIZE:
        return
    folder = _folder(path)
    index = _index(folder)
    moment = time.time()
    name = f"{int(moment * 1000)}{source.suffix.lower()}"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, folder / name)
    except OSError:
        return
    index["path"] = str(source.resolve())
    index["items"].append({"file": name, "time": source.stat().st_mtime, "saved": moment, "size": size})
    for old in index["items"][:-KEEP]:
        (folder / old["file"]).unlink(missing_ok=True)
    index["items"] = index["items"][-KEEP:]
    _write(folder, index)


def versions(path):
    """[(file path, time of that state, size), …], newest first."""
    folder = _folder(path)
    items = [item for item in _index(folder)["items"] if (folder / item["file"]).exists()]
    return [(folder / item["file"], item["time"], item["size"]) for item in reversed(items)]


def moved(old, new):
    """The document was renamed or moved: its versions go along."""
    source, target = _folder(old), _folder(new)
    if not source.exists() or source == target:
        return
    if target.exists():
        shutil.rmtree(target)
    source.rename(target)
    index = _index(target)
    index["path"] = str(Path(new).resolve())
    _write(target, index)
