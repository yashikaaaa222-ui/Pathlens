"""Photos are rebuilt on the server and shown to moderators only; free text is screened for personal details."""
import base64
import io
import os

import pytest
from PIL import Image
from sqlalchemy import func, select

from models.report import Report, ReportEvent
from services.privacy import PhotoError, clean_photo, contains_personal_info

from conftest import ADMIN, MID_A

REPORT = {"category": "streetlight", "lat": MID_A[0], "lng": MID_A[1], "description": "Light is out near the corner", "consent": True}


def make_image(fmt="JPEG", size=(1600, 1200), *, exif=True, orientation=None, mode="RGB") -> bytes:
    img = Image.new(mode, size, (120, 140, 160) if mode == "RGB" else (120, 140, 160, 90))
    kw = {}
    if fmt == "JPEG" and exif:
        ex = Image.Exif()
        ex[0x010F] = "SECRET-CAMERA-MAKER"
        ex[0x0110] = "SECRET-PHONE-MODEL"
        gps = ex.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", (12.0, 58.0, 12.0), "E", (77.0, 35.0, 30.0)
        if orientation:
            ex[0x0112] = orientation
        kw["exif"] = ex
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def data_url(raw: bytes, mime="image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


# ---- the photo cleaner ---------------------------------------------------------------------------


def test_the_source_image_really_carries_gps_and_camera_details():
    # A guard on the tests below: if this fails, "metadata removed" would prove nothing.
    src = Image.open(io.BytesIO(make_image()))
    assert src.getexif().get_ifd(0x8825) and src.getexif().get(0x010F) == "SECRET-CAMERA-MAKER"


def test_clean_photo_removes_every_trace_of_camera_and_location_metadata():
    out = clean_photo(data_url(make_image()))
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    assert len(img.getexif()) == 0 and not img.getexif().get_ifd(0x8825)
    assert b"SECRET-CAMERA" not in out and b"SECRET-PHONE" not in out and b"Exif" not in out


def test_clean_photo_shrinks_large_images_and_respects_the_byte_limit():
    out = clean_photo(data_url(make_image(size=(4000, 3000))))
    assert max(Image.open(io.BytesIO(out)).size) <= 1024
    assert len(out) <= 150_000


def test_the_cameras_rotation_is_applied_before_the_tag_is_dropped():
    out = clean_photo(data_url(make_image(size=(300, 200), orientation=6)))  # 6 = "rotate to portrait"
    assert Image.open(io.BytesIO(out)).size == (200, 300)


@pytest.mark.parametrize("fmt,mime,mode", [("PNG", "image/png", "RGB"), ("PNG", "image/png", "RGBA"), ("WEBP", "image/webp", "RGB")])
def test_png_and_webp_are_accepted_and_come_out_as_plain_jpeg(fmt, mime, mode):
    out = clean_photo(data_url(make_image(fmt, (400, 300), exif=False, mode=mode), mime))
    assert Image.open(io.BytesIO(out)).format == "JPEG"


@pytest.mark.parametrize(
    "value",
    [
        "data:text/html;base64," + base64.b64encode(b"<script>alert(1)</script>").decode(),
        "data:image/svg+xml;base64," + base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'/>").decode(),
        data_url(b"this is not an image at all"),
        data_url(b""),
        "https://example.com/photo.jpg",
        "not a data url",
        "data:image/jpeg;base64,@@@@",
    ],
)
def test_anything_that_is_not_a_real_image_is_refused(value):
    with pytest.raises(PhotoError):
        clean_photo(value)


def test_a_photo_that_cannot_be_made_small_enough_is_refused():
    noise = Image.frombytes("RGB", (900, 900), os.urandom(900 * 900 * 3))
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    with pytest.raises(PhotoError, match="too large"):
        clean_photo(data_url(buf.getvalue(), "image/png"), max_bytes=4_000)


def test_absurdly_large_declared_images_are_refused():
    import struct, zlib

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 60_000, 60_000, 8, 2, 0, 0, 0)  # 3.6 billion pixels
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b"")
    with pytest.raises(PhotoError):
        clean_photo(data_url(png, "image/png"))


# ---- photos through the API ----------------------------------------------------------------------


def test_a_photo_sent_with_a_report_is_stored_clean_and_flagged_for_moderators(client, db):
    r = client.post("/api/reports", json={**REPORT, "photo": data_url(make_image())})
    assert r.status_code == 201 and r.json()["photo_saved"] is True
    row = db.get(Report, r.json()["id"])
    assert row.has_photo is True
    assert b"SECRET-CAMERA" not in row.photo and len(Image.open(io.BytesIO(row.photo)).getexif()) == 0


def test_a_bad_photo_is_refused_and_nothing_is_saved(client, db):
    before = db.scalar(select(func.count()).select_from(Report))
    r = client.post("/api/reports", json={**REPORT, "photo": data_url(b"not an image")})
    assert r.status_code == 422 and "image" in r.text
    assert db.scalar(select(func.count()).select_from(Report)) == before


def test_an_oversized_upload_is_refused_before_it_is_decoded(client):
    r = client.post("/api/reports", json={**REPORT, "photo": "data:image/jpeg;base64," + "A" * 300_001})
    assert r.status_code == 422


def test_photos_are_never_public(client):
    rid = client.post("/api/reports", json={**REPORT, "photo": data_url(make_image())}).json()["id"]
    assert client.get(f"/api/reports/{rid}/photo").status_code == 401  # no token
    listed = client.get("/api/reports").json()[0]
    assert not any("photo" in k for k in listed)


def test_a_moderator_can_see_the_photo_but_it_is_never_cached(client):
    rid = client.post("/api/reports", json={**REPORT, "photo": data_url(make_image())}).json()["id"]
    r = client.get(f"/api/reports/{rid}/photo", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert Image.open(io.BytesIO(r.content)).format == "JPEG"


def test_the_queue_shows_which_reports_have_a_photo(client):
    rid = client.post("/api/reports", json={**REPORT, "photo": data_url(make_image())}).json()["id"]
    item = next(i for i in client.get("/api/dashboard/queue", headers=ADMIN).json() if i["report"]["id"] == rid)
    assert item["report"]["has_photo"] is True and "has a photo" in item["reasons"]


def test_rejecting_a_report_deletes_its_photo_and_says_so_in_the_trail(client, db):
    rid = client.post("/api/reports", json={**REPORT, "photo": data_url(make_image())}).json()["id"]
    resp = client.patch(f"/api/reports/{rid}/status", json={"status": "rejected", "note": "Not on a public road"}, headers=ADMIN)
    assert resp.status_code == 200 and resp.json()["has_photo"] is False
    assert client.get(f"/api/reports/{rid}/photo", headers=ADMIN).status_code == 404
    db.expire_all()
    assert db.get(Report, rid).photo is None
    actions = [e["action"] for e in client.get(f"/api/reports/{rid}/events", headers=ADMIN).json()]
    assert actions == ["created", "status:rejected", "photo:removed"]


def test_a_moderator_can_delete_a_photo_that_shows_a_face_and_keep_the_report(client):
    rid = client.post("/api/reports", json={**REPORT, "photo": data_url(make_image())}).json()["id"]
    assert client.delete(f"/api/reports/{rid}/photo").status_code == 401  # moderators only
    r = client.delete(f"/api/reports/{rid}/photo", params={"note": "shows a face"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["has_photo"] is False and r.json()["status"] == "unverified"
    assert client.delete(f"/api/reports/{rid}/photo", headers=ADMIN).status_code == 404
    trail = client.get(f"/api/reports/{rid}/events", headers=ADMIN).json()
    assert trail[-1]["action"] == "photo:removed" and trail[-1]["note"] == "shows a face"


# ---- personal details in text --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "call 98765 43210 tomorrow",
        "+91 98765-43210",
        "ring (020) 7946 0018 now",
        "mail a.b@example.com",
        "see www.example.com",
        "visit https://x.example/path",
        "photos on example.com",
        "contact @rahul_k about it",
        "KA 01 AB 1234 is parked here",
        "mh12ab1234 blocks the road",
    ],
)
def test_contact_details_links_handles_and_vehicle_numbers_are_refused(client, text):
    r = client.post("/api/reports", json={**REPORT, "description": text})
    assert r.status_code == 422 and "Describe the infrastructure only" in r.text
    assert contains_personal_info(text)


@pytest.mark.parametrize(
    "text",
    [
        "Light is out near 5th cross, 12th main",
        "A 100 m stretch with 3 lights out",
        "Pothole about 40 cm deep at the junction",
        "Footpath tiles missing since 2026",
        "Road 560001 side lane dark",
        "No light on NH 48 km 120",
        "Blocked by a parked truck near gate 2",
        "Broken.In front of the school the lamp is out",
    ],
)
def test_ordinary_descriptions_are_not_mistaken_for_personal_details(text):
    assert not contains_personal_info(text)


def test_the_same_screening_applies_to_disputes(client, make_report):
    r = make_report(status="verified")
    bad = client.post(f"/api/reports/{r.id}/disputes", json={"reason": "ask me on 98765 43210"})
    assert bad.status_code == 422
