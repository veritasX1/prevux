"""Colour management (Preview: Tools → Assign Profile, View → Soft Proof with Profile).

Images keep their ICC profile; for the screen they are converted to sRGB, so an Adobe RGB
or ProPhoto photo shows its real colours. A soft proof shows on screen how a page or picture
comes out on another device – a printing press, newsprint, a grey-scale printer – using the
profiles installed on the system (colord, Ghostscript, ~/.local/share/icc)."""

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageCms

FOLDERS = [Path("/usr/share/color/icc"), Path("/usr/local/share/color/icc"),
           Path.home() / ".local/share/icc", Path.home() / ".color/icc"]

SRGB = ImageCms.createProfile("sRGB")
_transforms = {}
_profiles = None


def srgb_bytes():
    return ImageCms.ImageCmsProfile(SRGB).tobytes()


def _profile(data_or_path):
    if isinstance(data_or_path, (bytes, bytearray)):
        return ImageCms.ImageCmsProfile(io.BytesIO(data_or_path))
    return ImageCms.ImageCmsProfile(str(data_or_path))


def name(data_or_path):
    """The profile's own description, e.g. "Adobe RGB (1998)"."""
    try:
        return ImageCms.getProfileDescription(_profile(data_or_path)).strip() or None
    except Exception:
        return None


def space(data_or_path):
    """"RGB", "CMYK" or "GRAY" (others: None)."""
    try:
        value = _profile(data_or_path).profile.xcolor_space.strip().upper()
    except Exception:
        return None
    return value if value in ("RGB", "CMYK", "GRAY") else None


def is_srgb(data):
    return data is None or "srgb" in (name(data) or "").lower().replace(" ", "")


def installed():
    """[(name, path, space)] of the device profiles on this computer, sorted by name."""
    global _profiles
    if _profiles is None:
        seen, found = set(), []
        for folder in FOLDERS:
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*")):
                if path.suffix.lower() not in (".icc", ".icm") or path.name.startswith("edid-"):
                    continue
                title, kind = name(path), space(path)
                if title and kind and title not in seen:
                    seen.add(title)
                    found.append((title, path, kind))
        _profiles = sorted(found, key=lambda item: item[0].lower())
    return _profiles


def _key(*parts):
    return hashlib.sha1(b"|".join(p if isinstance(p, bytes) else str(p).encode() for p in parts)).hexdigest()


def for_screen(image, icc=None, proof=None):
    """The picture as it should look on an sRGB screen: converted from its own profile and,
    with a proof profile, as the target device would reproduce it."""
    if proof is None and is_srgb(icc):
        return image
    mode = image.mode if image.mode in ("RGB", "RGBA") else "RGBA"
    if image.mode != mode:
        image = image.convert(mode)
    key = _key(icc or b"srgb", proof or "", mode)
    transform = _transforms.get(key)
    if transform is None:
        source = _profile(icc) if icc else SRGB
        try:
            if proof:
                transform = ImageCms.buildProofTransform(
                    source, SRGB, _profile(proof), mode, mode,
                    renderingIntent=ImageCms.Intent.PERCEPTUAL,
                    proofRenderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                    flags=ImageCms.Flags.SOFTPROOFING | ImageCms.Flags.BLACKPOINTCOMPENSATION)
            else:
                transform = ImageCms.buildTransform(source, SRGB, mode, mode)
        except Exception as error:
            print("Prevux: colour transform failed:", error)
            return image
        _transforms[key] = transform
    return ImageCms.applyTransform(image, transform)


def cmyk_to_rgb(image, icc):
    """CMYK pictures (print JPEGs) to sRGB with their profile – a plain convert gets them wrong."""
    try:
        return ImageCms.profileToProfile(image, _profile(icc), SRGB, outputMode="RGB")
    except Exception:
        return image.convert("RGB")
