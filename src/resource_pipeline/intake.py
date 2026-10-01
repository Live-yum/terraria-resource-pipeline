"""Bound HTTP upload bodies before the multipart parser can spool them.

The archive limit remains separate from a small allowance for multipart headers
and fields. The receive wrapper counts every byte, including ignored parts;
Content-Length is only an optional early rejection, never a trusted counter.
"""
from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


MULTIPART_OVERHEAD_BYTES = 1024 * 1024
LIMIT_DETAIL = "上传请求总体积超过限制（包括所有 ZIP、表单字段与额外文件）"


class UploadBodyLimitMiddleware:
    """Streaming, upload-route-only ASGI guard; never buffers or drains a body.

    The pinned Starlette multipart parser closes its partial SpooledTemporaryFile
    objects on BaseException, including this HTTPException and disconnects. Raise
    through that parser rather than sending a response in the middle of parsing.
    FastAPI preserves the 413 and its ordinary exception handler sends it once.
    """

    def __init__(self, app: ASGIApp, archive_bytes: int,
                 multipart_overhead_bytes: int = MULTIPART_OVERHEAD_BYTES):
        if any(type(value) is not int or value < 0 for value in (archive_bytes, multipart_overhead_bytes)):
            raise ValueError("Upload body budgets must be nonnegative integers")
        self.app = app
        self.maximum = archive_bytes + multipart_overhead_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope["path"].rstrip("/") not in ("/api/jobs", "/api/raw-jobs")):
            await self.app(scope, receive, send)
            return

        # Do not keep reading an already rejected HTTP/1 upload just to reuse
        # its connection. HTTP/2 forbids the Connection header and closes streams
        # independently. ASGI servers own transport-level buffering and cleanup.
        headers = {"Connection": "close"} if scope.get("http_version", "1.1") in ("1.0", "1.1") else {}
        maximum = str(self.maximum).encode("ascii")
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            value = value.strip()
            if value.isdigit():
                digits = value.lstrip(b"0") or b"0"
                if len(digits) > len(maximum) or len(digits) == len(maximum) and digits > maximum:
                    await JSONResponse({"detail": LIMIT_DETAIL}, status_code=413, headers=headers)(scope, receive, send)
                    return

        total = 0

        async def bounded_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.maximum:
                    # The overflowing chunk never reaches python-multipart or
                    # disk. Earlier chunks are closed by parser error cleanup.
                    raise HTTPException(status_code=413, detail=LIMIT_DETAIL, headers=headers)
            return message

        await self.app(scope, bounded_receive, send)
