"""Safe file storage for uploads.

* Files are streamed to disk in 1 MB blocks (never fully held in memory).
* The stored filename is a random UUID + validated extension: user-supplied names
  never reach the filesystem, which prevents path traversal.
* Sizes are enforced while streaming; magic bytes are checked after upload.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.core.errors import PayloadTooLarge, ValidationFailed

BLOCK = 1024 * 1024


@dataclass
class StoredFile:
    original_name: str
    stored_name: str
    path: Path
    extension: str
    size: int
    sha256: str


def safe_display_name(filename: str | None) -> str:
    """Keep only the basename and strip control/unsafe characters (for display only)."""
    name = (filename or "upload").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:200] or "upload"


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def resolve_inside(base: Path, name: str) -> Path:
    """Return ``base/name`` and guarantee it stays inside ``base``."""
    candidate = (base / name).resolve()
    if base.resolve() not in candidate.parents:
        raise ValidationFailed("Invalid file location")
    return candidate


async def save_upload(upload: UploadFile, dest_dir: Path, allowed: set[str], max_bytes: int) -> StoredFile:
    original = safe_display_name(upload.filename)
    ext = extension_of(original)
    if ext not in allowed:
        raise ValidationFailed(
            f"Unsupported file type '.{ext or '?'}'. Allowed: {', '.join('.' + e for e in sorted(allowed))}"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = resolve_inside(dest_dir, stored_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as fh:
            while block := await upload.read(BLOCK):
                size += len(block)
                if size > max_bytes:
                    raise PayloadTooLarge(f"File exceeds the maximum upload size of {max_bytes // BLOCK} MB")
                digest.update(block)
                fh.write(block)
        if size == 0:
            raise ValidationFailed("The uploaded file is empty")
        validate_magic(path, ext)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return StoredFile(original, stored_name, path, ext, size, digest.hexdigest())


def validate_magic(path: Path, ext: str) -> None:
    """Check that file content matches its extension."""
    with path.open("rb") as fh:
        head = fh.read(4096)
    if ext == "pdf" and not head.startswith(b"%PDF"):
        raise ValidationFailed("File is not a valid PDF")
    if ext in {"docx", "xlsx"} and not head.startswith(b"PK\x03\x04"):
        raise ValidationFailed(f"File is not a valid .{ext} document")
    if ext in {"txt", "csv"}:
        if b"\x00" in head:
            raise ValidationFailed(f"File does not look like a text .{ext} file")


def delete_stored(dest_dir: Path, stored_name: str) -> None:
    try:
        resolve_inside(dest_dir, stored_name).unlink(missing_ok=True)
    except ValidationFailed:
        pass
