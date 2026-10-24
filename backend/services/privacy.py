"""Privacy helpers: keep personal details out of free text, and clean photos on the server.

Free text. `contains_personal_info` refuses phone numbers, e-mail addresses, links, @handles and vehicle
registration numbers. It is a safety net, not a guarantee: it cannot recognise a person's name, so a
moderator still reads every report before its text is shown publicly.

Photos. The browser form already strips metadata, but a server must not trust that. `clean_photo` decodes
the image, applies its orientation, and writes a brand-new JPEG, which carries no EXIF block, no GPS
position and no camera details. It also shrinks the picture and refuses anything that is not an image.
It does not blur faces or number plates; that is why photos are only ever shown to moderators.
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import re
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

PII_MESSAGE = "Remove phone numbers, email addresses, links, @handles and vehicle numbers. Describe the infrastructure only."

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|org|net)\b", re.I)
_HANDLE = re.compile(r"(?<![\w@])@\w{3,}")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
# Indian vehicle registration numbers such as "KA 01 AB 1234" or "MH12AB1234". Only real state and union
# territory codes are matched, so a highway reference like "NH 48 km 120" is not mistaken for a plate. Other
# countries' formats are not covered.
_STATES = "AN|AP|AR|AS|BR|CG|CH|DD|DL|DN|GA|GJ|HP|HR|JH|JK|KA|KL|LA|LD|MH|ML|MN|MP|MZ|NL|OD|OR|PB|PY|RJ|SK|TN|TR|TS|UK|UP|WB"
_PLATE = re.compile(rf"\b(?:{_STATES})[\s-]?\d{{1,2}}[\s-]?[A-Z]{{1,3}}[\s-]?\d{{3,4}}\b", re.I)


def contains_personal_info(text: str) -> bool:
    if not text:
        return False
    if _EMAIL.search(text) or _URL.search(text) or _HANDLE.search(text) or _PLATE.search(text):
        return True
    # Eight or more digits in a run (spaces, dashes and brackets allowed) look like a phone number.
    return any(sum(ch.isdigit() for ch in m.group()) >= 8 for m in _PHONE.finditer(text))


# --------------------------------------------------------------------------------------------------
# Photos
# --------------------------------------------------------------------------------------------------

MAX_SIDE_PX = 1024
MAX_PIXELS = 25_000_000  # refuse absurdly large images before they are decoded into memory
_DATA_URL = re.compile(r"^data:image/(?:jpeg|jpg|png|webp);base64,([A-Za-z0-9+/=\s]+)$")


class PhotoError(ValueError):
    """The photo was not usable. The message is safe to show to the person who sent it."""


def max_photo_bytes() -> int:
    try:
        return int(os.getenv("PHOTO_MAX_BYTES", "150000"))
    except ValueError:
        return 150_000


def clean_photo(data_url: str, *, max_bytes: int | None = None) -> bytes:
    """Return a metadata-free JPEG, at most `max_bytes` long, made from an uploaded image data URL."""
    limit = max_bytes or max_photo_bytes()
    match = _DATA_URL.match((data_url or "").strip())
    if not match:
        raise PhotoError("The photo must be a JPEG, PNG or WebP image.")
    try:
        raw = base64.b64decode(match.group(1), validate=False)
    except (binascii.Error, ValueError) as err:
        raise PhotoError("The photo could not be read.") from err
    if not raw or len(raw) > limit * 8:
        raise PhotoError("The photo is too large. Use a smaller image.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            Image.MAX_IMAGE_PIXELS = MAX_PIXELS
            img = Image.open(io.BytesIO(raw))
            img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as err:
        raise PhotoError("That file is not a readable image.") from err

    img = ImageOps.exif_transpose(img)  # apply the camera's rotation first, because the tag is dropped below
    img = img.convert("RGB")  # a new image: no EXIF, no GPS, no embedded profile or text chunks
    img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    for quality in (72, 60, 48, 38):
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)  # no exif= argument, so none is written
        if out.tell() <= limit:
            return out.getvalue()
    raise PhotoError("The photo is still too large after shrinking it. Use a smaller image.")
