"""Shared plumbing for routes that take a file as the raw request body (the Library): the OpenAPI
body description, the size-limited reader and the data directory.

The upload is the raw request body, its Content-Type the file's media type (as imports: no
multipart parser is installed). Only the media types the service accepts are described.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request

from finance360.capabilities.library.service import MAX_BYTES, MEDIA_TYPES
from finance360.core.errors import FieldError, ValidationError

FILE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            media_type: {"schema": {"type": "string", "format": "binary"}}
            for media_type in MEDIA_TYPES
        },
    }
}


async def _file_body(request: Request) -> bytes:
    """The raw body, refused as soon as it passes the size limit."""
    too_big = ValidationError(
        [FieldError("file", f"The file is larger than {MAX_BYTES // 1_000_000} MB.")]
    )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise too_big
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BYTES:
            raise too_big
    return bytes(data)


FileDep = Annotated[bytes, Depends(_file_body)]


def _data_dir(request: Request) -> Path:
    return request.app.state.settings.data_dir


DataDirDep = Annotated[Path, Depends(_data_dir)]
