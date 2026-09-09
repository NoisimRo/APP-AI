"""Shared helpers for multipart uploads."""

from fastapi import HTTPException, UploadFile

from app.core.config import get_settings

CHUNK = 1024 * 1024


async def read_upload(file: UploadFile, max_bytes: int | None = None) -> bytes:
    """Read an upload fully, rejecting it with HTTP 413 once it exceeds ``max_bytes``.

    Reads in chunks so an oversized file is refused before it is held in
    memory in full.
    """
    limit = max_bytes or get_settings().max_upload_bytes
    buf = bytearray()
    while True:
        chunk = await file.read(CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Fișier prea mare. Maxim {limit // (1024 * 1024)}MB",
            )
    return bytes(buf)
