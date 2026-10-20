"""Authenticated image storage for DormCare (standard library only)."""
import base64
import binascii
import os
import secrets

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 5
MAGIC = {
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
}


class UploadError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def decode_image(item):
    if not isinstance(item, dict):
        raise UploadError("bad_upload", "Each image must be an object.")
    mime = str(item.get("mime") or "").lower()
    if mime not in MAGIC:
        raise UploadError("bad_image_type", "Only JPEG and PNG images are allowed.")
    raw = item.get("data")
    if not isinstance(raw, str):
        raise UploadError("bad_base64", "Missing base64 image data.")
    if "," in raw and raw.lstrip().lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        raise UploadError("bad_base64", "Invalid base64 image data.")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise UploadError("image_too_large", "Each image must be at most 5 MB.")
    magic, ext = MAGIC[mime]
    if not data.startswith(magic):
        raise UploadError("image_signature_mismatch",
                          "The image content does not match its MIME type.")
    return data, mime, ext


def save_images(store, upload_dir, user_id, items, purpose="ticket", draft_id=""):
    if not isinstance(items, list) or not items:
        raise UploadError("no_images", "At least one image is required.")
    if len(items) > MAX_IMAGES:
        raise UploadError("too_many_images", "At most five images are allowed.")
    os.makedirs(upload_dir, exist_ok=True)
    saved = []
    for item in items:
        data, mime, ext = decode_image(item)
        filename = secrets.token_hex(24) + ext
        path = os.path.join(upload_dir, filename)
        with open(path, "xb") as fh:
            fh.write(data)
        record = store.add_upload({
            "owner_user_id": user_id,
            "purpose": purpose if purpose in ("ticket", "completion", "agent") else "ticket",
            "draft_id": draft_id or "",
            "ticket_id": "",
            "mime": mime,
            "size": len(data),
            "filename": filename,
        })
        saved.append(public_upload(record))
    return saved


def public_upload(item):
    return {
        "id": item.get("id", ""),
        "mime": item.get("mime", ""),
        "size": item.get("size", 0),
        "url": "/api/uploads/" + item.get("id", ""),
    }
